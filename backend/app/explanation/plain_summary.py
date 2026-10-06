"""Flow-level and system-level Behavioral Changes and Impact, written by rule from the PR's facts.

Used when no model-written item passes the evidence checks. Nothing is described per function.
Instead the changed steps are grouped by the flows that reach them (the entry points from stored
call edges at the head commit), and the system surfaces the analyzer found in the diff (HTTP
routes, stored data, configuration, dependencies, web interface) are described as interface
changes. Every sentence comes from a before/after fact, a surface finding, or a rule finding, and
each item keeps its evidence links.
"""

from __future__ import annotations

import re

from app.analyzer.behavior import CATEGORY_ORDER, call_names
from app.explanation.schema import BehaviorChangeFact, BehaviorFunctionFact, ImpactFact

ITEM_CAP = 6
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


# --- words ----------------------------------------------------------------------------------


def humanize(name: str) -> str:
    """createSession → "create session", audit.record → "audit record"."""
    text = (name or "").replace(".", "_").strip("_")
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    words = [word.lower() for word in re.split(r"[_\s]+", spaced) if word]
    return " ".join(words[:6]) or "this step"


def _list(items: list[str], cap: int = 4) -> str:
    shown = list(dict.fromkeys(items))
    extra = len(shown) - cap
    shown = shown[:cap] + ([f"{extra} more"] if extra > 0 else [])
    if len(shown) <= 1:
        return "".join(shown)
    return ", ".join(shown[:-1]) + " and " + shown[-1]


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


# --- what a flow sees --------------------------------------------------------------------------


def _flow_change(change: BehaviorChangeFact, fact: BehaviorFunctionFact) -> tuple[str, str] | None:
    """(what the flow did before, what it does now) at one changed step, in flow terms."""
    step = f"the {humanize(fact.function)} step"
    kind, before, after = change.kind, change.before, change.after
    if kind == "removed_function":
        return f"used {step}", f"no longer have {step}, which is removed"
    if kind == "signature" and before and after:
        from app.explanation.behavior_facts import param_delta

        added, removed, defaults = param_delta(before, after, fact.file)
        required = [name for name, optional in added if not optional]
        if required:
            n = len(required)
            return f"supplied {step} the inputs it needed", f"must supply {n} more required input{'s' if n != 1 else ''} to {step}"
        if removed:
            return f"could pass {step} inputs it no longer accepts", f"must stop passing those inputs to {step}"
        if defaults:
            return f"got the old default at {step} when they left an input out", "get a different default there"
        return None
    if kind == "error":
        if before and after:
            return f"failed with one kind of error at {step} in one case", "fail with a different error there"
        if after:
            return f"carried on through {step} in one case", f"can stop with an error at {step} in that case"
        return f"stopped with an error at {step} in one case", "carry on in that case"
    if kind == "return":
        if before and after:
            return f"got one result back from {step}", "get a different result back"
        if after:
            return f"continued past {step} in one case", f"can end early at {step} in that case"
        return f"ended early at {step} in one case", "continue in that case"
    if kind == "condition":
        return f"followed one path through {step}", "take a different path through it for some inputs"
    if kind == "call":
        target = next(iter(call_names(after or before or "")), "")
        what = humanize(target) if target else "another operation"
        if after and not before:
            return f"did not trigger {what} at {step}", f"also trigger {what} there"
        if before and not after:
            return f"triggered {what} at {step}", f"no longer trigger {what}"
        return f"triggered one operation at {step}", "trigger a different one"
    if kind == "value":
        return f"used one value at {step}", "use a different value there"
    return None


def _flow_outcomes(facts: list[BehaviorFunctionFact], cap: int = 3) -> tuple[list[str], list[str]]:
    befores: list[str] = []
    afters: list[str] = []
    for fact in facts:
        seen: set[str] = set()
        for change in sorted(fact.changes, key=lambda c: CATEGORY_ORDER.get(c.kind, 9)):
            if change.kind in seen:
                continue
            seen.add(change.kind)
            pair = _flow_change(change, fact)
            if pair:
                befores.append(pair[0])
                afters.append(pair[1])
            if len(afters) >= cap:
                return befores, afters
    return befores, afters


def _flows(facts: list[BehaviorFunctionFact]) -> list[tuple[list[str], list[BehaviorFunctionFact]]]:
    """Changed public steps grouped by the set of entry points that reach them."""
    groups: dict[tuple[str, ...], list[BehaviorFunctionFact]] = {}
    for fact in facts:
        if not fact.public:
            continue
        entries = tuple(sorted(set(fact.reached_from)))
        if not entries:
            callers = sorted({site.split(" → ")[0] for site in fact.callers_at_head})
            entries = tuple(callers)
        groups.setdefault(entries, []).append(fact)
    ordered = sorted(groups.items(), key=lambda pair: (not pair[0], -len(pair[0]), -len(pair[1])))
    return [(list(entries), members) for entries, members in ordered]


def _flow_title(entries: list[str]) -> str:
    if not entries:
        return "Changes no analyzed flow reaches"
    names = [humanize(name) for name in entries]
    return _cap(_list(names)) + (" flows" if len(names) > 1 else " flow")


# --- system surfaces ---------------------------------------------------------------------------


def surfaces_from(claims, evidences, repo: str | None) -> list[dict]:
    """Surface findings (routes, data, configuration, dependencies, web interface) with evidence links."""
    by_id = {getattr(e, "public_id", None) or getattr(e, "id", None): e for e in evidences or []}
    out: list[dict] = []
    for claim in claims or []:
        if getattr(claim, "kind", None) != "surface_changed":
            continue
        for evidence_id in getattr(claim, "evidence_public_ids", None) or getattr(claim, "evidence_ids", None) or []:
            evidence = by_id.get(evidence_id)
            parts = (getattr(evidence, "description", "") or "").split("|", 3) if evidence is not None else []
            if len(parts) != 4:
                continue
            level, change, name, detail = parts
            location = f"{evidence.file}:{evidence.start_line}" if evidence.start_line else evidence.file
            href = (
                f"https://github.com/{repo}/blob/{evidence.commit_sha}/{evidence.file}" + (f"#L{evidence.start_line}" if evidence.start_line else "")
                if repo and evidence.commit_sha
                else None
            )
            out.append({"level": level, "change": change, "name": name, "detail": detail, "evidence": {"label": location, "href": href}})
    return out


_SURFACE_TITLE = {
    "api": "HTTP interface",
    "data": "Stored data",
    "config": "Configuration",
    "dependency": "Dependencies",
    "ui": "Web interface",
}


def _surface_sentences(level: str, items: list[dict]) -> tuple[str, str]:
    added = [i for i in items if i["change"] == "added"]
    removed = [i for i in items if i["change"] == "removed"]
    changed = [i for i in items if i["change"] == "changed"]
    if level == "ui":
        n = len(items)
        return "The web interface rendered as before.", f"{n} web interface file{'s' if n != 1 else ''} change what users see."
    noun = {"api": "route", "data": "", "config": "setting", "dependency": "package"}.get(level, "")
    def names(group):
        return _list([f"{i['name']}" + (f" ({i['detail']})" if level in {"data", "dependency"} and i["detail"] else "") for i in group])
    after: list[str] = []
    before: list[str] = []
    if added:
        after.append(f"{'adds' if level != 'config' else 'now reads'} {noun + ' ' if noun else ''}{names(added)}")
        before.append(f"had no {noun + ' ' if noun else ''}{names(added)}")
    if removed:
        after.append(f"removes {noun + ' ' if noun else ''}{names(removed)}")
        before.append(f"had {noun + ' ' if noun else ''}{names(removed)}")
    if changed:
        after.append(f"changes {noun + ' ' if noun else ''}{names(changed)}")
        before.append(f"used the earlier {noun + ' ' if noun else ''}{names(changed)}")
    subject = {"api": "The service", "data": "The data model", "config": "The service", "dependency": "The build"}.get(level, "The system")
    return f"{subject} {'; '.join(before)}.", f"{subject} {'; '.join(after)}."


def _surface_severity(level: str, items: list[dict]) -> str:
    if level == "api" and any(i["change"] == "removed" for i in items):
        return "high"
    if level == "data" and any("dropped" in i["detail"] or i["change"] == "removed" for i in items):
        return "high"
    if level in {"api", "data", "config"}:
        return "medium"
    return "low"


def _by_level(surfaces: list[dict]) -> list[tuple[str, list[dict]]]:
    grouped: dict[str, list[dict]] = {}
    for item in surfaces or []:
        grouped.setdefault(item["level"], []).append(item)
    order = ["api", "data", "config", "dependency", "ui"]
    return [(level, grouped[level]) for level in order if level in grouped]


# --- sections ----------------------------------------------------------------------------------


def rule_behavior_items(facts: list[BehaviorFunctionFact], links, surfaces: list[dict] | None = None) -> list[dict]:
    """System-level interface changes first, then one item per flow."""
    out: list[dict] = []
    for level, items in _by_level(surfaces or []):
        before, after = _surface_sentences(level, items)
        out.append(
            {
                "title": _SURFACE_TITLE.get(level, "System"),
                "before": before,
                "after": after,
                "impact": "",
                "evidence": [item["evidence"] for item in items[:4]],
            }
        )
    for entries, members in _flows(facts):
        befores, afters = _flow_outcomes(members)
        if not afters:
            continue
        subject = "these flows" if len(entries) > 1 else "this flow"
        out.append(
            {
                "title": _flow_title(entries),
                "before": f"Previously, requests through {subject} {'; '.join(befores)}.",
                "after": f"Now they {'; '.join(afters)}.",
                "impact": "",
                "evidence": links([c.id for fact in members for c in fact.changes]),
            }
        )
        if len(out) >= ITEM_CAP:
            break
    return out[:ITEM_CAP]


def rule_impact_areas(
    behavior_facts: list[BehaviorFunctionFact], impact_facts: list[ImpactFact], links, surfaces: list[dict] | None = None
) -> list[dict]:
    """Which flows and which system interfaces this PR affects, with the analyzer's severity."""
    areas: list[tuple[int, dict]] = []
    for entries, members in _flows(behavior_facts):
        _befores, afters = _flow_outcomes(members, cap=2)
        if not afters:
            continue
        ids = {c.id for fact in members for c in fact.changes}
        related = [f for f in impact_facts if f.kind == "attention" and ids & set(f.behavior_ids)]
        severity = min((f.severity or "low" for f in related), key=lambda s: _SEVERITY_RANK.get(s, 2), default="low")
        untested = [fact for fact in members if not fact.tests]
        parts = [f"Requests through {'these flows' if len(entries) > 1 else 'this flow'} now {'; '.join(afters)}."]
        if untested:
            parts.append("No test exercises the changed steps." if len(untested) == len(members) else "Some changed steps have no test.")
        areas.append(
            (
                _SEVERITY_RANK.get(severity, 2),
                {
                    "title": _flow_title(entries),
                    "severity": severity,
                    "summary": " ".join(parts),
                    "who_notices": _list([humanize(name) for name in entries]) if entries else "",
                    "evidence": links([f.id for f in related] or sorted(ids)),
                },
            )
        )
    for level, items in _by_level(surfaces or []):
        severity = _surface_severity(level, items)
        _before, after = _surface_sentences(level, items)
        who = {
            "api": "Clients of the HTTP interface.",
            "data": "Every environment that runs the migration, and code reading the stored data.",
            "config": "Every deployment; the setting has to be provided.",
            "dependency": "Builds and anything using the package.",
            "ui": "Users of the web interface.",
        }.get(level, "")
        areas.append(
            (
                _SEVERITY_RANK[severity],
                {
                    "title": _SURFACE_TITLE.get(level, "System"),
                    "severity": severity,
                    "summary": after,
                    "who_notices": who,
                    "evidence": [item["evidence"] for item in items[:4]],
                },
            )
        )
    areas.sort(key=lambda pair: pair[0])
    return [area for _, area in areas[:ITEM_CAP]]


# --- overall summaries ---------------------------------------------------------------------------


def _outcome(change: BehaviorChangeFact, fact: BehaviorFunctionFact) -> str | None:
    """What requests experience after the change, with no step, code, or condition named."""
    kind, before, after = change.kind, change.before, change.after
    if kind == "removed_function":
        return "lose a step that was removed"
    if kind == "signature" and before and after:
        from app.explanation.behavior_facts import param_delta

        added, removed, defaults = param_delta(before, after, fact.file)
        required = [name for name, optional in added if not optional]
        if required:
            n = len(required)
            return f"need {n} more required input{'s' if n != 1 else ''}"
        if removed:
            return "must stop passing inputs that are no longer accepted"
        if defaults:
            return "get a different default when an input is left out"
        return None
    if kind == "error":
        if before and after:
            return "fail with a different error in some cases"
        return "can now stop with an error where they used to continue" if after else "no longer fail in a case that used to fail"
    if kind == "return":
        if before and after:
            return "get a different result"
        return "can now end early where they used to continue" if after else "continue where they used to end early"
    if kind == "condition":
        return "take a different path for some inputs"
    if kind == "call":
        target = next(iter(call_names(after or before or "")), "")
        what = humanize(target) if target else "another operation"
        if after and not before:
            return f"also trigger {what}"
        if before and not after:
            return f"no longer trigger {what}"
        return None
    if kind == "value":
        return "use a different value at one point"
    return None


def _outcomes(members: list[BehaviorFunctionFact], cap: int = 3) -> list[str]:
    out: list[str] = []
    for fact in members:
        for change in sorted(fact.changes, key=lambda c: CATEGORY_ORDER.get(c.kind, 9)):
            phrase = _outcome(change, fact)
            if phrase and phrase not in out:
                out.append(phrase)
            if len(out) >= cap:
                return out
    return out


def _interface_phrases(surfaces: list[dict]) -> list[str]:
    phrases: list[str] = []
    for level, items in _by_level(surfaces):
        verbs = {"added": "adds", "removed": "removes", "changed": "changes"}
        counts: dict[str, int] = {}
        for item in items:
            counts[item["change"]] = counts.get(item["change"], 0) + 1
        noun = {"api": "HTTP route", "data": "stored data definition", "config": "configuration setting",
                "dependency": "dependency", "ui": "web interface file"}.get(level, "interface")
        for change, n in counts.items():
            phrases.append(f"{verbs.get(change, change)} {n} {noun}{'s' if n != 1 else ''}")
    return phrases


def _join_clauses(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def behavior_overview(facts: list[BehaviorFunctionFact], surfaces: list[dict] | None = None) -> str:
    """A short, plain summary of how the system behaves differently, from the PR's facts only."""
    flows = [(entries, members) for entries, members in _flows(facts) if entries]
    unreached = [members for entries, members in _flows(facts) if not entries]
    interfaces = _interface_phrases(surfaces or [])
    sentences: list[str] = []
    if flows:
        names = [humanize(name) for entries, _ in flows for name in entries]
        count = len(set(names))
        sentences.append(
            f"This pull request changes how {count} flow{'s' if count != 1 else ''} behave{'s' if count == 1 else ''}"
            f" ({_list(sorted(set(names)), 5)})" + (f", and {_join_clauses(interfaces)}." if interfaces else ".")
        )
        for entries, members in flows[:3]:
            outcomes = _outcomes(members)
            if outcomes:
                subject = _list([humanize(name) for name in entries])
                sentences.append(f"Requests through {subject} now {_join_clauses(outcomes)}.")
    elif interfaces:
        sentences.append(f"This pull request {_join_clauses(interfaces)}.")
    if unreached:
        n = sum(len(members) for members in unreached)
        sentences.append(
            f"{n} other changed part{'s' if n != 1 else ''} of the code {'are' if n != 1 else 'is'} not reached from any analyzed entry point."
        )
    return " ".join(sentences)


def impact_overview(
    behavior_facts: list[BehaviorFunctionFact], impact_facts: list[ImpactFact], surfaces: list[dict] | None = None
) -> tuple[str, str]:
    """(overall severity, a short plain summary of what the PR affects), from rule findings only."""
    attention = [f for f in impact_facts if f.kind == "attention"]
    levels = [f.severity or "low" for f in attention] + [
        _surface_severity(level, items) for level, items in _by_level(surfaces or [])
    ]
    severity = min(levels, key=lambda s: _SEVERITY_RANK.get(s, 2), default="low")
    flows = [(entries, members) for entries, members in _flows(behavior_facts) if entries]
    sentences: list[str] = [f"Overall impact: {severity}."]
    if flows:
        names = sorted({humanize(name) for entries, _ in flows for name in entries})
        sentences.append(
            f"{len(names)} flow{'s' if len(names) != 1 else ''} reach the changed behavior ({_list(names, 5)})."
        )
    counts = {level: sum(1 for f in attention if (f.severity or "low") == level) for level in ("high", "medium")}
    if attention:
        parts = [f"{n} {level}" for level, n in counts.items() if n]
        sentences.append(
            f"The analysis found {len(attention)} finding{'s' if len(attention) != 1 else ''} that need{'s' if len(attention) == 1 else ''} attention"
            + (f" ({', '.join(parts)})." if parts else ".")
        )
    untested = [members for entries, members in flows if all(not fact.tests for fact in members)]
    if untested:
        sentences.append(
            "No test exercises the changed behavior." if len(untested) == len(flows) else "Some of these flows have no test for the changed behavior."
        )
    interfaces = _interface_phrases(surfaces or [])
    if interfaces:
        sentences.append(f"At the system level it {_join_clauses(interfaces)}.")
    else:
        sentences.append("The diff changes no HTTP route, stored data, configuration, or dependency.")
    return severity, " ".join(sentences)
