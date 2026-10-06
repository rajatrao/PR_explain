"""Impact: what a reviewer should care about in this pull request.

Two layers. ``build_impact`` derives findings by rule from stored facts (below). Those findings
go into the packet as IMPACT FACTS; the configured model writes the reviewer-facing summary
from them (an overview and impact areas with severity), with no
implementation details. ``screen_impact`` keeps an item only when it cites known facts, names
no function, method, class, or file, uses no code token outside its cited facts, and is not
rated above the most severe finding it cites. Without a usable summary the section says so.

Rule-derived findings (Scope always; the rest when present):

* Scope            localized, one workflow, several workflows, or cross-cutting, from the
                   entry points that reach changed behavior,
* Needs attention  up to three findings ranked by rule, never by the model:
                   high    a caller no longer matches a changed signature, a migration drops a
                           table or column, a route is removed, a new failure reaches an entry
                           point with no caller wrapping the call in a try block,
                   medium  a shared function changes its contract, a new early return reaches
                           callers, a new setting is read but not documented, a package crosses a
                           major version, a schema change needs a migration, a route declaration
                           changes,
* Who depends on this  entry points that reach the changed behavior, with the calls made at head,
* Verify           stale tests, changed behavior with no test, settings to provide,
* Not affected / partial view  only from measured call-resolution coverage.

Sources: behavior facts (before/after statements with conditions, entry points, head call
sites, contract notes) and stored claims: ``surface_changed``, ``call_context``,
``stale_test``, ``config_undocumented``, ``resolution_coverage``, ``file_changed``.
"""

from __future__ import annotations

import re

from app.analyzer.behavior import error_target
from app.analyzer.parse import is_test_path
from app.explanation.behavior_facts import param_delta
from app.explanation.behavioral_changes import Grounding, _evidence_markdown, evidence_links
from app.explanation.schema import BehaviorFunctionFact, ImpactAreaNote, ImpactFact, ImpactNarrative

ATTENTION_CAP = 3
DEPENDENT_CAP = 5
VERIFY_CAP = 3
COVERAGE_COMPLETE = 0.9
_RANK = {"high": 0, "medium": 1, "low": 2}


def build_impact(*, claims, evidences, behavior_facts: list[BehaviorFunctionFact]) -> dict:
    evidence_by_id = {_id(item): item for item in evidences or []}
    contexts = _contexts(claims, evidence_by_id)
    surfaces = _surfaces(claims, evidence_by_id)
    findings: list[dict] = []
    verify: list[str] = []

    def find(severity: str, title: str, why: str, weight: int = 0) -> None:
        item = {"severity": severity, "title": title, "why": why, "_weight": weight}
        if not any(existing["title"] == title for existing in findings):
            findings.append(item)

    for fact in behavior_facts:
        if not fact.public:
            continue
        entries = fact.reached_from
        weight = len(entries)
        name = fact.function
        # Contract: callers that no longer match, or a contract change on a shared function.
        for note in fact.notes:
            if " passes " in note:
                find("high", f"A caller no longer matches the new signature of {name}", note, weight + 10)
        signature = next((c for c in fact.changes if c.kind == "signature" and c.before and c.after), None)
        if signature is not None and fact.callers_at_head:
            added, removed, defaults = param_delta(signature.before, signature.after, fact.file)
            parts = []
            required = [n for n, optional in added if not optional]
            if required:
                parts.append("callers must now pass " + ", ".join(f"`{n}`" for n in required))
            if removed:
                parts.append("callers no longer pass " + ", ".join(f"`{n}`" for n in removed))
            for param, old, new in defaults:
                parts.append(f"omitting `{param}` now means `{new}` instead of `{old}`")
            if parts:
                callers = _unique([site.split(" → ")[0] for site in fact.callers_at_head])
                matching = " ".join(n for n in fact.notes if " passes " not in n)
                why = f"{_cap_first('; '.join(parts))}. Called from {_names(callers)}." + (f" {matching}" if matching else "")
                find("medium", f"Shared function {name} changes its contract", why, weight)
        # Failure path: a new or changed failure on a path that entry points reach.
        for change in fact.changes:
            if change.kind == "error" and change.after and entries:
                target = error_target(change.after)
                when = f" when `{change.after_when}`" if change.after_when else ""
                was = f" (it used to fail with {error_target(change.before)})" if change.before else ""
                handled = [c for c in contexts.get(name, []) if c["handled"]]
                unhandled = [c for c in contexts.get(name, []) if not c["handled"]]
                if unhandled and not handled:
                    severity = "high"
                    tail = f"No caller wraps the call in a try block: {_names([c['caller'] for c in unhandled])}."
                elif handled:
                    severity = "medium"
                    tail = f"Handled by {_names([c['caller'] for c in handled])}" + (
                        f"; not by {_names([c['caller'] for c in unhandled])}." if unhandled else "."
                    )
                else:
                    severity = "medium"
                    tail = "No resolved caller shows whether it is handled."
                find(severity, f"New failure can reach {_names(entries)}", f"{name} now fails with {target}{when}{was}. {tail}", weight + 5)
            if change.kind == "return" and change.after and not change.before and change.after_when and fact.callers_at_head:
                expr = re.sub(r"^(?:return|yield)\s*", "", change.after).rstrip(";").strip() or "nothing"
                checks = [c for c in contexts.get(name, []) if c["checks"]]
                tail = (
                    f" {_names([c['caller'] for c in checks])} check the result."
                    if checks
                    else " No caller checks the result right after the call."
                )
                find("medium", f"Callers of {name} can now receive `{expr}`", f"{name} returns `{expr}` early when `{change.after_when}`.{tail}", weight)
        if fact.removed:
            find("medium", f"{name} is no longer available", f"{name} is defined in the base commit and not at the head commit.", weight)

    for level, change, name, detail in surfaces:
        if level == "api" and change == "removed":
            find("high", f"Route {name} is removed", "Clients that call it will no longer reach it.", 8)
        elif level == "api" and change == "changed":
            find("medium", f"The declaration of route {name} changes", "Check that existing clients still match it.", 2)
        elif level == "data" and "dropped" in detail:
            noun, _, ident = name.partition(" ")
            find("high", f"A migration drops the {noun} {ident}", "Data stored in it is lost when the migration runs.", 9)
        elif level == "data" and change == "added" and not detail.startswith("ORM"):
            noun, _, ident = name.partition(" ")
            find("medium", f"Schema change: {noun} {ident}", "The migration has to run wherever this is deployed, before the new code serves traffic.", 1)
        elif level == "dependency" and change == "changed" and "→" in detail:
            old, new = (part.strip() for part in detail.split("→", 1))
            if _major(old) is not None and _major(new) is not None and _major(old) != _major(new):
                find("medium", f"Package {name} crosses a major version", f"It moves from {old} to {new}.", 0)

    for claim in claims or []:
        if _kind(claim) == "config_undocumented":
            setting = _subject(claim)
            find("medium", f"New setting {setting} is not documented", f"The code now reads {setting}, and no env example or compose file lists it.", 3)
            verify.append(f"Set `{setting}` in every environment before deploying.")

    stale = [claim for claim in claims or [] if _kind(claim) == "stale_test"]
    for claim in stale:
        test_file = _text(claim).split(" exercises ", 1)[0]
        verify.insert(0, f"`{test_file}` exercises {_subject(claim)} but was not updated; confirm it still asserts the intended behavior.")
    for fact in behavior_facts:
        if fact.public and not fact.removed and not fact.tests and any(
            c.kind in {"error", "return", "condition", "signature"} for c in fact.changes
        ) and (fact.reached_from or fact.callers_at_head):
            verify.append(f"No test reaches the changed behavior of {fact.function}.")

    findings.sort(key=lambda item: (_RANK[item["severity"]], -item["_weight"]))
    attention = [{key: value for key, value in item.items() if key != "_weight"} for item in findings[:ATTENTION_CAP]]

    dependents = _dependents(behavior_facts)
    coverage = _coverage(claims)
    scope = _scope(claims, behavior_facts, surfaces)
    not_affected = partial = ""
    if coverage is not None:
        resolved, total = coverage
        if total and resolved / total >= COVERAGE_COMPLETE:
            not_affected = (
                "Every call in the changed files resolved to a single function, so no consumers beyond those listed were found."
                if dependents
                else "Every call in the changed files resolved, and no caller outside the changed code was found."
            )
        elif total:
            partial = (
                f"{total - resolved} of {total} calls in the changed files could not be resolved to one function, "
                "so other consumers may exist."
            )
    return {
        "scope": scope,
        "attention": attention,
        "dependents": dependents,
        "verify": _unique(verify)[:VERIFY_CAP],
        "not_affected": not_affected,
        "partial": partial,
    }


# --- parts --------------------------------------------------------------------------------


def _dependents(behavior_facts: list[BehaviorFunctionFact]) -> list[dict]:
    by_entry: dict[str, dict] = {}
    for fact in behavior_facts:
        if not fact.public:
            continue
        direct = {site.split(" → ")[0]: site.split(" → ", 1)[1] for site in fact.callers_at_head if " → " in site}
        for entry in fact.reached_from:
            item = by_entry.setdefault(entry, {"entry": entry, "reaches": [], "calls": []})
            if fact.function not in item["reaches"]:
                item["reaches"].append(fact.function)
            if entry in direct and direct[entry] not in item["calls"]:
                item["calls"].append(direct[entry])
    ordered = sorted(by_entry.values(), key=lambda item: (-len(item["reaches"]), item["entry"]))
    return ordered[:DEPENDENT_CAP]


def _scope(claims, behavior_facts, surfaces) -> str:
    changed = [s for s in (_subject(c) for c in claims or [] if _kind(c) == "file_changed") if s and not is_test_path(s)]
    entries = _unique([name for fact in behavior_facts if fact.public for name in fact.reached_from])
    sides = {("frontend" if path.startswith("frontend/") or path.endswith((".tsx", ".jsx", ".vue", ".css")) else "backend") for path in changed}
    has_data = any(level == "data" for level, *_ in surfaces)
    if len(entries) >= 5 or (len(sides) > 1 and (entries or has_data)):
        verdict = "Cross-cutting"
    elif len(entries) >= 2:
        verdict = "Several workflows"
    elif len(entries) == 1:
        verdict = "One workflow"
    else:
        verdict = "Localized"
    text = f"{verdict}. {len(changed)} production file{'s' if len(changed) != 1 else ''} changed"
    if entries:
        text += f"; the changed behavior is reached from {len(entries)} entry point{'s' if len(entries) != 1 else ''}"
        outside = sum(1 for claim in claims or [] if _kind(claim) == "reaches_changed")
        if outside:
            text += f", including code in {outside} file{'s' if outside != 1 else ''} the diff does not touch"
    return text + "."


def _contexts(claims, evidence_by_id) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for claim in claims or []:
        if _kind(claim) != "call_context":
            continue
        for evidence_id in _evidence_ids(claim):
            evidence = evidence_by_id.get(evidence_id)
            parts = (evidence.description or "").split("|") if evidence is not None else []
            if len(parts) != 5:
                continue
            _tag, caller, callee, handled, checks = parts
            out.setdefault(callee, []).append({"caller": caller, "handled": handled == "handled", "checks": checks})
    return out


def _surfaces(claims, evidence_by_id) -> list[tuple[str, str, str, str]]:
    out = []
    for claim in claims or []:
        if _kind(claim) != "surface_changed":
            continue
        for evidence_id in _evidence_ids(claim):
            evidence = evidence_by_id.get(evidence_id)
            parts = (evidence.description or "").split("|", 3) if evidence is not None else []
            if len(parts) == 4:
                out.append(tuple(parts))
    return out


def _coverage(claims) -> tuple[int, int] | None:
    for claim in claims or []:
        if _kind(claim) == "resolution_coverage":
            match = re.match(r"coverage:(\d+)/(\d+)", _subject(claim))
            if match:
                return int(match.group(1)), int(match.group(2))
    return None


# --- helpers ------------------------------------------------------------------------------


def _names(names: list[str], limit: int = 3) -> str:
    names = _unique(names)
    shown = ", ".join(names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def _cap_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _major(version: str) -> int | None:
    match = re.search(r"\d+", version or "")
    return int(match.group()) if match else None


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _kind(claim) -> str:
    return getattr(claim, "kind", "") or ""


def _subject(claim) -> str:
    return getattr(claim, "subject", None) or ""


def _text(claim) -> str:
    return getattr(claim, "text", None) or ""


def _evidence_ids(claim) -> list[str]:
    ids = getattr(claim, "evidence_public_ids", None)
    if ids is None:
        ids = getattr(claim, "evidence_ids", None)
    return list(ids or [])


def _id(item):
    return getattr(item, "public_id", None) or getattr(item, "id", None)


# --- model summary --------------------------------------------------------------------


NO_FACTS = "No impact beyond the changed code was found."
NO_SUMMARY = (
    "No impact summary could be verified against the diff for this commit. "
    "Callers outside the diff are listed under Details › Callers outside the diff."
)
AREA_CAP = 5


def build_impact_facts(*, claims, evidences, behavior_facts: list[BehaviorFunctionFact]) -> list[ImpactFact]:
    """The rule-derived findings as packet facts the model may cite."""
    section = build_impact(claims=claims, evidences=evidences, behavior_facts=behavior_facts)
    ids_by_name = {fact.function: [c.id for c in fact.changes] for fact in behavior_facts}
    facts: list[ImpactFact] = []

    def add(kind: str, text: str, severity: str | None = None) -> None:
        related = [bid for name, ids in ids_by_name.items() if name and re.search(rf"\b{re.escape(name)}\b", text) for bid in ids]
        facts.append(ImpactFact(id=f"i{len(facts) + 1}", kind=kind, text=text, severity=severity, behavior_ids=related[:12]))

    if section["scope"] and (section["attention"] or section["dependents"]):
        add("scope", section["scope"])
    for item in section["attention"]:
        add("attention", f"{item['title']}. {item['why']}", item["severity"])
    for item in section["dependents"]:
        via = "; ".join(item["calls"])
        add("dependents", f"{item['entry']} reaches {', '.join(item['reaches'])}" + (f" via {via}" if via else "") + ".")
    for item in section["verify"]:
        add("verify", item)
    if section["not_affected"] or section["partial"]:
        add("coverage", section["not_affected"] or section["partial"])
    return facts


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
    if not impact_facts:
        log.append("the packet had no impact facts")
        return None
    if isinstance(narrative, dict):
        narrative = _parse(narrative)
        if narrative is None:
            log.append("impact did not match the schema")
            return None
    grounding = Grounding(behavior_facts, impact_facts)
    by_id = {fact.id: fact for fact in impact_facts}

    def problem(text: str, allowed: str) -> str | None:
        # No function, method, or caller names anywhere in Impact, entry points included.
        return grounding.problem(text, allowed, may_name_entries=False)

    areas: list[ImpactAreaNote] = []
    for note in narrative.areas:
        allowed = grounding.scope(note.fact_ids)
        if allowed is None:
            log.append(f"dropped '{note.title[:40]}': cites no known fact")
            continue
        note = note.model_copy(
            update={field: grounding.strip_judgments(getattr(note, field), note.fact_ids) for field in ("title", "summary", "who_notices")}
        )
        if not (note.title and note.summary):
            log.append("dropped an area: only judged the change")
            continue
        issue = None
        for text in (note.title, note.summary, note.who_notices):
            if text:
                issue = problem(text, allowed)
                if issue:
                    break
        if issue is None and grounding.off_topic([note.title, note.summary, note.who_notices], note.fact_ids):
            issue = "shares no subject with the facts it cites"
        if issue is None:
            issue = grounding.unsupported_claim(" ".join([note.title, note.summary, note.who_notices or ""]), note.fact_ids)
        if issue:
            log.append(f"dropped '{note.title[:40]}': {issue}")
            continue
        cited = [by_id[item].severity for item in note.fact_ids if item in by_id and by_id[item].severity]
        ceiling = min((_RANK[item] for item in cited), default=_RANK["low"])
        severity = (note.severity or "").strip().lower()
        if severity not in _RANK or _RANK[severity] < ceiling:
            severity = ["high", "medium", "low"][ceiling]
        areas.append(note.model_copy(update={"severity": severity, "fact_ids": grounding.known(note.fact_ids)}))
        if len(areas) >= AREA_CAP:
            break
    if not areas:
        return None
    areas.sort(key=lambda item: _RANK[item.severity])

    kept_ids = [fact_id for area in areas for fact_id in area.fact_ids]
    overview = grounding.strip_judgments(narrative.overview.strip(), kept_ids)
    everything = grounding.scope([fact.id for fact in impact_facts]) or ""
    # The overview may only summarize the kept areas: it is checked against their facts alone.
    if overview and (
        problem(overview, everything)
        or grounding.off_topic([overview], kept_ids)
        or grounding.unsupported_claim(overview, kept_ids)
    ):
        log.append("dropped the overview: it goes beyond the kept areas")
        overview = ""
    return ImpactNarrative(overview=overview, areas=areas)


def build_impact_section(
    *,
    narrative: ImpactNarrative | dict | None,
    behavior_facts: list[BehaviorFunctionFact],
    impact_facts: list[ImpactFact],
    prescreened: bool = False,
    reasons: list[str] | None = None,
) -> dict:
    # Screened again against the facts rebuilt from the stored analysis, as Behavioral Changes is.
    del prescreened
    chosen = screen_impact(narrative, behavior_facts, impact_facts, reasons) if narrative else None
    if chosen is None or not chosen.areas:
        areas = _rule_areas(impact_facts, behavior_facts)
        if areas:
            # No model summary passed the checks: show the analyzer's own findings, each from stored facts.
            return {"source": "rules", "overview": RULES_NOTE, "areas": areas}
        return {"source": "none", "overview": NO_FACTS, "areas": []}
    return {
        "source": "model",
        "overview": chosen.overview,
        "areas": [
            {
                "title": note.title,
                "severity": note.severity,
                "summary": note.summary,
                "who_notices": note.who_notices,
                "evidence": evidence_links(note.fact_ids, behavior_facts, impact_facts),
            }
            for note in chosen.areas
        ],
    }


RULES_NOTE = "From the analyzer's findings: the model's summary did not pass the evidence checks for this commit."


def _rule_areas(impact_facts: list[ImpactFact], behavior_facts: list[BehaviorFunctionFact]) -> list[dict]:
    """The rule findings that need attention, then one area for the workflows that reach the change."""
    out: list[dict] = []
    for fact in impact_facts:
        if fact.kind != "attention":
            continue
        title, _, rest = fact.text.partition(". ")
        out.append(
            {
                "title": title.rstrip("."),
                "severity": fact.severity or "low",
                "summary": rest.strip() or title,
                "who_notices": "",
                "evidence": evidence_links([fact.id], behavior_facts, impact_facts),
            }
        )
    dependents = [fact for fact in impact_facts if fact.kind == "dependents"]
    if dependents:
        entries = [fact.text.split(" reaches ", 1)[0] for fact in dependents]
        out.append(
            {
                "title": f"{len(entries)} entry point{'s' if len(entries) != 1 else ''} reach the changed code",
                "severity": "low",
                "summary": " ".join(fact.text for fact in dependents[:5]),
                "who_notices": ", ".join(entries[:8]),
                "evidence": evidence_links([fact.id for fact in dependents], behavior_facts, impact_facts),
            }
        )
    return out[:AREA_CAP]


def render_impact_markdown(section: dict) -> str:
    lines = ["### Impact", ""]
    areas = section.get("areas") or []
    if not areas:
        lines.append(section.get("overview") or NO_FACTS)
        return "\n".join(lines)
    if section.get("overview"):
        lines.extend([section["overview"], ""])
    for area in areas:
        lines.append(f"**{area['title']}** ({area['severity']})")
        lines.append(f"- {area['summary']}")
        if area.get("who_notices"):
            lines.append(f"- **Who notices:** {area['who_notices']}")
        if area.get("evidence"):
            lines.append("- **Evidence:** " + ", ".join(_evidence_markdown(item) for item in area["evidence"]))
        lines.append("")
    if section.get("source") == "model":
        lines.append(
            "_Written by the configured model from rule-derived impact findings; each item was checked against the facts it cites._"
        )
    return "\n".join(lines).strip()


def _parse(narrative: dict) -> ImpactNarrative | None:
    try:
        return ImpactNarrative.model_validate(narrative)
    except Exception:
        return None
