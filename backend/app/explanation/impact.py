"""Impact: what the pull request affects at each level (system, API, data, configuration,
dependencies, user interface, testing).

The configured model writes the impact from the packet's IMPACT FACTS and BEHAVIOR
FACTS during the explanation step. Each level is kept only after it passes the same
grounding check as Behavioral Changes (``Grounding``): it must cite known facts, name
no function, method, class, or file (public entry points are allowed), and use no code
token outside the facts it cites.

When the model's impact is missing or nothing survives, the section lists the impact
facts themselves per level. Those sentences come from analysis (routes, tables,
settings, packages, entry points, test coverage counts), not from the model.
"""

from __future__ import annotations

from app.explanation.behavioral_changes import Grounding
from app.explanation.impact_facts import LEVEL_LABEL, LEVELS
from app.explanation.schema import BehaviorFunctionFact, ImpactFact, ImpactLevelNote, ImpactNarrative

DETAIL_CAP = 5
FACT_CAP = 6

_LEVEL_ALIASES = {
    **{level: level for level in LEVELS},
    **{label.lower(): level for level, label in LEVEL_LABEL.items()},
    "api": "api",
    "contracts": "api",
    "database": "data",
    "configuration": "config",
    "operations": "config",
    "dependencies": "dependency",
    "user interface": "ui",
    "frontend": "ui",
    "tests": "testing",
}


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
        try:
            narrative = ImpactNarrative.model_validate(narrative)
        except Exception:
            log.append("impact did not match the schema")
            return None
    grounding = Grounding(behavior_facts, impact_facts)
    kept: list[ImpactLevelNote] = []
    seen: set[str] = set()
    for note in narrative.levels:
        level = _LEVEL_ALIASES.get((note.level or "").strip().lower())
        if level is None or level in seen:
            log.append(f"dropped impact level '{note.level[:30]}': unknown or repeated level")
            continue
        allowed = grounding.scope(note.fact_ids)
        if allowed is None:
            log.append(f"dropped impact level '{level}': cites no known fact")
            continue
        issue = grounding.problem(note.summary, allowed, may_name_entries=True)
        if issue:
            log.append(f"dropped impact level '{level}': {issue}")
            continue
        details = [item for item in note.details if grounding.problem(item, allowed, may_name_entries=True) is None]
        kept.append(
            ImpactLevelNote(level=level, summary=note.summary, details=details[:DETAIL_CAP], fact_ids=grounding.known(note.fact_ids))
        )
        seen.add(level)
    if not kept:
        return None
    kept.sort(key=lambda item: LEVELS.index(item.level))
    return ImpactNarrative(levels=kept)


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
    if chosen is not None and chosen.levels:
        return {
            "source": "model",
            "levels": [
                {"level": note.level, "label": LEVEL_LABEL.get(note.level, note.level), "summary": note.summary, "details": note.details}
                for note in chosen.levels
            ],
            "note": "",
        }
    levels = []
    for level in LEVELS:
        texts = [fact.text for fact in impact_facts if fact.level == level]
        if not texts:
            continue
        levels.append(
            {
                "level": level,
                "label": LEVEL_LABEL[level],
                "summary": texts[0],
                "details": texts[1:FACT_CAP],
            }
        )
    why = [reason for reason in (reasons or []) if reason]
    note = ""
    if levels and why:
        note = f"Listed from the analysis facts; the model's impact summary was not used ({why[0]})."
    elif levels:
        note = "Listed from the analysis facts."
    return {"source": "facts", "levels": levels, "note": note}


def render_impact_markdown(section: dict) -> str:
    lines = ["### Impact", ""]
    levels = section.get("levels") or []
    if not levels:
        lines.append("No impact beyond the changed code was found in the stored facts.")
        return "\n".join(lines)
    for level in levels:
        lines.append(f"**{level['label']}** — {level['summary']}")
        lines.extend(f"  - {item}" for item in level.get("details") or [])
    lines.append("")
    if section.get("source") == "model":
        lines.append("_Written by the configured model from the impact and behavior facts; each level was checked against the facts it cites._")
    elif section.get("note"):
        lines.append(f"_{section['note']}_")
    return "\n".join(lines).strip()


def _parse(narrative: dict) -> ImpactNarrative | None:
    try:
        return ImpactNarrative.model_validate(narrative)
    except Exception:
        return None
