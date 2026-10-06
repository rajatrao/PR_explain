"""Plain-language Behavioral Changes and Impact, written by rule from the PR's facts.

Used when no model-written item passes the evidence checks, so the Explain tab is never empty and
never shows code. Every sentence comes from a before/after fact in the diff or a rule finding; the
wording names capabilities in plain words (``createSession`` reads as "create session"), never
code, files, conditions, or call syntax. Each item keeps its evidence links.
"""

from __future__ import annotations

import re

from app.analyzer.behavior import CATEGORY_ORDER, call_names
from app.explanation.schema import BehaviorChangeFact, BehaviorFunctionFact, ImpactFact

ITEM_CAP = 5
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def humanize(name: str) -> str:
    """createSession → "create session", render_combined_comment → "render combined comment"."""
    tail = (name or "").rsplit(".", 1)[-1].strip("_")
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", tail)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    words = [word.lower() for word in re.split(r"[_\s]+", spaced) if word]
    return " ".join(words[:6]) or "this code"


def _capability(name: str) -> str:
    text = humanize(name)
    return text[0].upper() + text[1:]


def _people(names: list[str], cap: int = 4) -> str:
    shown = [humanize(name) for name in names[:cap]]
    if len(names) > cap:
        shown.append(f"{len(names) - cap} more")
    if len(shown) <= 1:
        return "".join(shown)
    return ", ".join(shown[:-1]) + " and " + shown[-1]


def describe(change: BehaviorChangeFact, fact: BehaviorFunctionFact) -> tuple[str, str] | None:
    """Plain before/after sentences for one statement-level change, or None when it shows no outcome."""
    kind, before, after = change.kind, change.before, change.after
    if kind == "removed_function":
        return "This capability existed.", "It was removed."
    if kind == "signature" and before and after:
        from app.explanation.behavior_facts import param_delta

        added, removed, defaults = param_delta(before, after, fact.file)
        required = [name for name, optional in added if not optional]
        optional = [name for name, optional in added if optional]
        if required:
            n = len(required)
            return "Callers supplied the inputs it needed.", f"Callers must now supply {n} more required input{'s' if n != 1 else ''}."
        if removed:
            n = len(removed)
            return f"It accepted {n} input{'s' if n != 1 else ''} it no longer takes.", "Callers that still pass them must change."
        if defaults:
            return "Callers that left an input out got its old default.", "They now get a different default."
        if optional:
            n = len(optional)
            return "It took a fixed set of inputs.", f"It accepts {n} new optional input{'s' if n != 1 else ''}; existing calls keep working."
        return None
    if kind == "error":
        if before and after:
            return "In one case it failed with one kind of error.", "In that case it now fails with a different error."
        if after:
            return "It carried on in one case.", "It now stops with an error in that case."
        return "It stopped with an error in one case.", "It no longer fails in that case."
    if kind == "return":
        if before and after:
            return "It returned one result.", "It now returns a different result."
        if after:
            return "It ran to the end in one case.", "It now returns early in that case."
        return "It returned early in one case.", "It now carries on in that case."
    if kind == "condition":
        return "One rule decided which path it took.", "That rule changed, so some inputs now take a different path."
    if kind == "call":
        if after and not before:
            targets = [humanize(name.replace(".", "_")) for name in call_names(after)][:1]
            return "It did not trigger this step.", f"It now also triggers {targets[0] if targets else 'another step'}."
        if before and not after:
            targets = [humanize(name.replace(".", "_")) for name in call_names(before)][:1]
            return f"It triggered {targets[0] if targets else 'another step'}.", "It no longer does."
        return "It triggered one step.", "It now triggers a different one."
    if kind == "value":
        return "It worked with one value.", "It now works with a different value."
    return None


def _main_change(fact: BehaviorFunctionFact) -> tuple[BehaviorChangeFact, tuple[str, str]] | None:
    for change in sorted(fact.changes, key=lambda c: CATEGORY_ORDER.get(c.kind, 9)):
        described = describe(change, fact)
        if described:
            return change, described
    return None


def _also(fact: BehaviorFunctionFact, main: BehaviorChangeFact, cap: int = 2) -> list[str]:
    """The other kinds of change in the same function, one standalone plain sentence each."""
    out: list[str] = []
    seen = {main.kind}
    for change in sorted(fact.changes, key=lambda c: CATEGORY_ORDER.get(c.kind, 9)):
        if change.kind in seen:
            continue
        seen.add(change.kind)
        sentence = _standalone(change, fact)
        if sentence:
            out.append(sentence)
        if len(out) >= cap:
            break
    return out


def _standalone(change: BehaviorChangeFact, fact: BehaviorFunctionFact) -> str | None:
    kind, before, after = change.kind, change.before, change.after
    if kind == "signature":
        described = describe(change, fact)
        return described[1] if described else None
    if kind == "error":
        return "It fails differently in one case." if before and after else (
            "It can now stop with an error in a new case." if after else "One case no longer fails."
        )
    if kind == "return":
        return "It returns a different result." if before and after else (
            "It returns early in a new case." if after else "One early return is gone."
        )
    if kind == "condition":
        return "A rule deciding which path it takes changed."
    if kind == "call":
        name = next(iter(call_names(after or before or "")), "")
        step = humanize(name.replace(".", "_")) if name else "another step"
        return f"It now also triggers {step}." if after and not before else (
            f"It no longer triggers {step}." if before and not after else "It triggers a different step."
        )
    if kind == "value":
        return "It uses a different value."
    return None


def rule_behavior_items(facts: list[BehaviorFunctionFact], links) -> list[dict]:
    """One plain item per changed public function, from its most caller-visible change."""
    out: list[dict] = []
    for fact in facts:
        if not fact.public:
            continue
        found = _main_change(fact)
        if found is None:
            continue
        change, (before, after) = found
        after = " ".join([after, *_also(fact, change)])
        out.append(
            {
                "title": _capability(fact.function),
                "before": before,
                "after": after,
                "impact": f"Anything that goes through {_people(fact.reached_from)}." if fact.reached_from else "",
                "evidence": links([c.id for c in fact.changes]),
            }
        )
        if len(out) >= ITEM_CAP:
            break
    return out


def rule_impact_areas(behavior_facts: list[BehaviorFunctionFact], impact_facts: list[ImpactFact], links) -> list[dict]:
    """One plain area per changed public function: what changes for whoever depends on it, how widely,
    and the severity the analyzer's own findings give it."""
    areas: list[tuple[int, dict]] = []
    for fact in behavior_facts:
        if not fact.public:
            continue
        found = _main_change(fact)
        if found is None:
            continue
        change, (_before, after) = found
        after = " ".join([after, *_also(fact, change)])
        ids = {c.id for c in fact.changes}
        related = [f for f in impact_facts if f.kind == "attention" and ids & set(f.behavior_ids)]
        severity = min((f.severity or "low" for f in related), key=lambda s: _SEVERITY_RANK.get(s, 2), default="low")
        reach = len(fact.reached_from)
        parts = [after]
        if reach:
            parts.append(f"{reach} entry point{'s' if reach != 1 else ''} lead to it.")
        if not fact.tests:
            parts.append("No test exercises it.")
        areas.append(
            (
                _SEVERITY_RANK.get(severity, 2),
                {
                    "title": _capability(fact.function),
                    "severity": severity,
                    "summary": " ".join(parts),
                    "who_notices": _people(fact.reached_from) if fact.reached_from else "",
                    "evidence": links([f.id for f in related] or sorted(ids)),
                },
            )
        )
    areas.sort(key=lambda pair: pair[0])
    return [area for _, area in areas[:ITEM_CAP]]
