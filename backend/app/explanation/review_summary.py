"""Reviewer summary: What changed, Review focus, Blast radius, Potential risks.

The configured model writes it during the explanation step from the packet's BEHAVIOR FACTS
and IMPACT FACTS. ``screen_summary`` keeps an item only when it cites known facts, uses no
code token outside them, names no file, quotes no call, and names only public entry points
or changed public functions (the things a reviewer opens). Risks such as race conditions or
performance appear only when the cited facts show them; the screen cannot prove absence of
speculation, so the prompt forbids it and every item must rest on cited facts.

Without a usable model summary, the section is built from the rule-derived findings
(``build_impact``) and labelled as such.
"""

from __future__ import annotations

import re

from app.explanation.behavioral_changes import Grounding
from app.explanation.impact import build_impact
from app.explanation.schema import BehaviorFunctionFact, ImpactFact, ReviewSummary, SummaryItem

FOCUS_CAP = 7
LIST_CAP = 6
_SPECULATION = re.compile(
    r"\b(?:could|might|may)\s+(?:cause|lead to|introduce|result in)\s+(?:a\s+)?(?:race|deadlock|performance|memory|security)",
    re.IGNORECASE,
)


def screen_summary(
    summary: ReviewSummary | dict | None,
    behavior_facts: list[BehaviorFunctionFact],
    impact_facts: list[ImpactFact],
    reasons: list[str] | None = None,
) -> ReviewSummary | None:
    log = reasons if reasons is not None else []
    if summary is None:
        log.append("the model returned no review_summary")
        return None
    if not behavior_facts and not impact_facts:
        log.append("the packet had no behavior or impact facts")
        return None
    if isinstance(summary, dict):
        summary = _parse(summary)
        if summary is None:
            log.append("review_summary did not match the schema")
            return None
    grounding = Grounding(behavior_facts, impact_facts)
    allow = {fact.function for fact in behavior_facts if fact.public and fact.function}
    everything = grounding.scope([c.id for f in behavior_facts for c in f.changes] + [f.id for f in impact_facts]) or ""

    def check(items: list[SummaryItem], cap: int, section: str) -> list[SummaryItem]:
        kept: list[SummaryItem] = []
        for item in items:
            allowed = grounding.scope(item.fact_ids)
            if allowed is None:
                log.append(f"{section}: dropped '{item.text[:40]}': cites no known fact")
                continue
            issue = grounding.problem(item.text, allowed, may_name_entries=True, allow=allow)
            if issue is None and _SPECULATION.search(item.text):
                issue = "speculated beyond the facts"
            if issue:
                log.append(f"{section}: dropped '{item.text[:40]}': {issue}")
                continue
            kept.append(item.model_copy(update={"fact_ids": grounding.known(item.fact_ids)}))
            if len(kept) >= cap:
                break
        return kept

    what = summary.what_changed.strip()
    if what and grounding.problem(what, everything, may_name_entries=True, allow=allow):
        log.append("what_changed: dropped: it used names outside the facts")
        what = ""
    focus = check(summary.review_focus, FOCUS_CAP, "review_focus")
    blast = check(summary.blast_radius, LIST_CAP, "blast_radius")
    risks = check(summary.risks, LIST_CAP, "risks")
    if not what and not focus:
        return None
    return ReviewSummary(what_changed=what, review_focus=focus, blast_radius=blast, risks=risks)


def build_summary_section(
    *,
    summary: ReviewSummary | dict | None,
    behavior_facts: list[BehaviorFunctionFact],
    claims,
    evidences,
    prescreened: bool = False,
    reasons: list[str] | None = None,
) -> dict:
    if prescreened and summary:
        chosen = summary if isinstance(summary, ReviewSummary) else _parse(summary)
    else:
        chosen = None
    if chosen is not None and (chosen.what_changed or chosen.review_focus):
        return {
            "source": "model",
            "what_changed": chosen.what_changed,
            "review_focus": [item.text for item in chosen.review_focus],
            "blast_radius": [item.text for item in chosen.blast_radius],
            "risks": [item.text for item in chosen.risks],
            "note": "",
        }

    findings = build_impact(claims=claims, evidences=evidences, behavior_facts=behavior_facts)
    focus = [f"{item['title']}: {item['why']}" for item in findings["attention"]] + list(findings["verify"])
    blast = [
        f"{item['entry']} (reaches {', '.join(item['reaches'])})" for item in findings["dependents"]
    ]
    if findings["partial"]:
        blast.append(findings["partial"])
    risks = [f"{item['severity'].capitalize()}: {item['title']}." for item in findings["attention"]]
    untested = [item for item in findings["verify"] if item.startswith("No test reaches")]
    risks += untested
    why = [reason for reason in (reasons or []) if reason]
    note = "Built from the analysis findings" + (f"; the model's summary was not used ({why[0]})." if why else ".")
    return {
        "source": "facts",
        "what_changed": findings["scope"],
        "review_focus": _unique(focus)[:FOCUS_CAP],
        "blast_radius": _unique(blast)[:LIST_CAP],
        "risks": _unique(risks)[:LIST_CAP],
        "note": note,
    }


def render_summary_markdown(section: dict) -> str:
    lines = ["### Summary", "", "**1. What changed**", "", section.get("what_changed") or "No behavior change was found."]
    for number, title, key in ((2, "Review focus", "review_focus"), (3, "Blast radius", "blast_radius"), (4, "Potential risks", "risks")):
        items = section.get(key) or []
        lines.extend(["", f"**{number}. {title}**", ""])
        lines.extend(f"- {item}" for item in items) if items else lines.append("- None found in the analysis.")
    lines.append("")
    if section.get("source") == "model":
        lines.append("_Written by the configured model from the behavior and impact facts; each item was checked against the facts it cites._")
    elif section.get("note"):
        lines.append(f"_{section['note']}_")
    return "\n".join(lines).strip()


def _parse(summary: dict) -> ReviewSummary | None:
    try:
        return ReviewSummary.model_validate(summary)
    except Exception:
        return None


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out
