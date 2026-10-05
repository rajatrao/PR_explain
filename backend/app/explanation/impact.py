"""Impact: the areas a pull request touches and the risks a reviewer should weigh.

The configured model writes up to five impact areas and up to five risks from the
packet's IMPACT FACTS and BEHAVIOR FACTS during the explanation step. Each item is
kept only after the same grounding check as Behavioral Changes (``Grounding``): it
cites known facts, names no function, method, class, or file (public entry points
are allowed), and uses no code token outside the facts it cites. A risk must cite at
least one risk fact, and its severity can be no higher than the highest severity
among the risk facts it cites.

Without a usable model impact, the section shows the area and risk facts as they
are. They come from analysis, not from the model.
"""

from __future__ import annotations

from app.explanation.behavioral_changes import Grounding
from app.explanation.impact_facts import AREAS, SEVERITIES
from app.explanation.schema import (
    BehaviorFunctionFact,
    ImpactAreaNote,
    ImpactFact,
    ImpactNarrative,
    ImpactRiskNote,
)

AREA_CAP = 5
RISK_CAP = 5
FACTS_PER_AREA = 4
_RANK = {severity: index for index, severity in enumerate(SEVERITIES)}


def screen_impact(
    narrative: ImpactNarrative | dict | None,
    behavior_facts: list[BehaviorFunctionFact],
    impact_facts: list[ImpactFact],
    reasons: list[str] | None = None,
) -> ImpactNarrative | None:
    log = reasons if reasons is not None else []
    if narrative is None:
        log.append("the model returned no impact")
        return None
    if not impact_facts and not behavior_facts:
        log.append("the packet had no impact facts")
        return None
    if isinstance(narrative, dict):
        narrative = _parse(narrative)
        if narrative is None:
            log.append("impact did not match the schema")
            return None
    grounding = Grounding(behavior_facts, impact_facts)
    by_id = {fact.id: fact for fact in impact_facts}

    areas: list[ImpactAreaNote] = []
    for note in narrative.areas:
        allowed = grounding.scope(note.fact_ids)
        if allowed is None:
            log.append(f"dropped area '{note.area[:30]}': cites no known fact")
            continue
        issue = grounding.problem(note.area, allowed, may_name_entries=False) or grounding.problem(
            note.summary, allowed, may_name_entries=True
        )
        if issue:
            log.append(f"dropped area '{note.area[:30]}': {issue}")
            continue
        areas.append(note.model_copy(update={"fact_ids": grounding.known(note.fact_ids)}))
        if len(areas) >= AREA_CAP:
            break

    risks: list[ImpactRiskNote] = []
    for note in narrative.risks:
        cited = [by_id[item] for item in note.fact_ids if item in by_id and by_id[item].kind == "risk"]
        if not cited:
            log.append(f"dropped risk '{note.risk[:30]}': cites no risk fact")
            continue
        allowed = grounding.scope(note.fact_ids) or ""
        issue = grounding.problem(note.risk, allowed, may_name_entries=True)
        if issue:
            log.append(f"dropped risk '{note.risk[:30]}': {issue}")
            continue
        ceiling = min(_RANK.get(fact.severity or "low", 2) for fact in cited)
        severity = (note.severity or "").strip().lower()
        if severity not in _RANK or _RANK[severity] < ceiling:
            severity = SEVERITIES[ceiling]
        risks.append(note.model_copy(update={"severity": severity, "fact_ids": grounding.known(note.fact_ids)}))
        if len(risks) >= RISK_CAP:
            break
    risks.sort(key=lambda item: _RANK[item.severity])
    if not areas and not risks:
        return None
    return ImpactNarrative(areas=areas, risks=risks)


def build_impact_section(
    *,
    narrative: ImpactNarrative | dict | None,
    behavior_facts: list[BehaviorFunctionFact],
    impact_facts: list[ImpactFact],
    prescreened: bool = False,
    reasons: list[str] | None = None,
) -> dict:
    if prescreened and narrative:
        chosen = narrative if isinstance(narrative, ImpactNarrative) else _parse(narrative)
    else:
        chosen = screen_impact(narrative, behavior_facts, impact_facts) if narrative else None
    if chosen is not None and (chosen.areas or chosen.risks):
        return {
            "source": "model",
            "areas": [{"area": note.area, "summary": note.summary} for note in chosen.areas],
            "risks": [{"severity": note.severity, "risk": note.risk} for note in chosen.risks],
            "note": "",
        }

    areas = []
    for area in AREAS:
        texts = [fact.text for fact in impact_facts if fact.kind == "area" and fact.area == area]
        if texts:
            summary = " ".join(texts[:FACTS_PER_AREA])
            if len(texts) > FACTS_PER_AREA:
                summary += f" ({len(texts) - FACTS_PER_AREA} more.)"
            areas.append({"area": area, "summary": summary})
    risks = sorted(
        ({"severity": fact.severity or "low", "risk": fact.text} for fact in impact_facts if fact.kind == "risk"),
        key=lambda item: _RANK.get(item["severity"], 2),
    )[: RISK_CAP + 3]
    why = [reason for reason in (reasons or []) if reason]
    note = ""
    if areas or risks:
        note = "Listed from the analysis facts" + (f"; the model's impact summary was not used ({why[0]})." if why else ".")
    return {"source": "facts", "areas": areas, "risks": risks, "note": note}


def render_impact_markdown(section: dict) -> str:
    lines = ["### Impact", ""]
    areas = section.get("areas") or []
    risks = section.get("risks") or []
    if not areas and not risks:
        lines.append("No impact on routes, data, configuration, dependencies, the web interface, or public entry points was found.")
        return "\n".join(lines)
    if areas:
        lines.append("**Impact areas**")
        lines.extend(f"- **{item['area']}** — {item['summary']}" for item in areas)
        lines.append("")
    if risks:
        lines.append("**Risks**")
        lines.extend(f"- **{item['severity'].capitalize()}** — {item['risk']}" for item in risks)
        lines.append("")
    if section.get("source") == "model":
        lines.append("_Written by the configured model from the impact and behavior facts; each item was checked against the facts it cites._")
    elif section.get("note"):
        lines.append(f"_{section['note']}_")
    return "\n".join(lines).strip()


def _parse(narrative: dict) -> ImpactNarrative | None:
    try:
        return ImpactNarrative.model_validate(narrative)
    except Exception:
        return None
