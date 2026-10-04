"""System impact for the Explain view, from stored facts and the diff.

Three labeled parts. Sentences name the symbol, file, caller, test, or
config they come from, and they do not restate the Behavioral Changes lines.
"""

from __future__ import annotations

from app.explanation.change_facts import (
    ChangeFacts,
    FnFact,
    added_params,
    code,
    collect_change_facts,
    guard,
    join_phrases,
    removed_params,
)


def render_system_impact_markdown(rows: list[dict]) -> str:
    lines = ["### System Impact", ""]
    for row in rows:
        lines.append(f"- **{row['label']}** — {row['value']}")
    return "\n".join(lines)


def build_system_impact_rows(*, symbols, relationships, claims, patches=None) -> list[dict]:
    facts = collect_change_facts(
        symbols=symbols,
        relationships=relationships,
        claims=claims,
        patches=patches,
    )
    rows = [
        _row("System Impact", _impact_text(facts)),
        _row("Reviewer Considerations", _reviewer_text(facts)),
        _row("Risk & Scope", _risk_text(facts)),
    ]
    return [_finish(row, facts) for row in rows]


def _row(label: str, value: str) -> dict:
    return {"label": label, "value": value, "href": None}


def _finish(row: dict, facts: ChangeFacts) -> dict:
    value = guard(row["value"], facts.hidden)
    if value == row["value"]:
        return row
    return {**row, "value": value or row["value"]}


def _impact_text(facts: ChangeFacts) -> str:
    sentences: list[str] = []
    by_target: dict[str, list] = {}
    for caller in facts.callers:
        by_target.setdefault(caller.target, []).append(caller)
    for target, callers in list(by_target.items())[:4]:
        fn = _fn(facts, target)
        where = f"{code(target)} in {code(fn.path)}" if fn and fn.path else code(target)
        shown = callers[:6]
        who = join_phrases(_caller_label(item) for item in shown)
        extra = len(callers) - len(shown)
        tail = f", plus {extra} more stored callers" if extra else ""
        outside = [item for item in shown if item.outside]
        if outside and len(outside) == len(shown):
            qualifier = "which is not in the diff" if len(shown) == 1 else "none of which are in the diff"
            sentences.append(f"Call edges into {where} come from {who}{tail}, {qualifier}.")
        else:
            sentences.append(f"Call edges into {where} come from {who}{tail}.")
    for item in facts.imports[:4]:
        place = " That file is outside the diff." if item.outside else ""
        sentences.append(f"{code(item.path)} imports {code(item.target)} and has no stored call.{place}")
    for path, names, outside in facts.reaches[:4]:
        place = " That file is outside the diff." if outside else ""
        sentences.append(
            f"{code(path)} has a stored path to {join_phrases(code(name) for name in names)}.{place}"
        )
    if facts.dependencies:
        files = join_phrases(code(path) for path in facts.dependency_files) or code("package.json")
        sentences.append(f"Dependency metadata in {files} lists {join_phrases(code(name) for name in facts.dependencies[:8])}.")
    for path in facts.config_files[:4]:
        sentences.append(f"Configuration file {code(path)} is in this diff.")
    if not sentences and facts.functions:
        listed = join_phrases(
            f"{code(fn.name)} in {code(fn.path)}" if fn.path else code(fn.name) for fn in facts.functions[:4]
        )
        sentences.append(f"{listed} changed, and no caller or outside-diff import is stored.")
    elif not sentences and facts.changed_files:
        listed = join_phrases(code(path) for path in facts.changed_files[:6])
        sentences.append(f"{listed} changed. No function, caller, or test fact is stored for those files.")
    elif not sentences:
        sentences.append("No changed function, caller, or dependency is stored.")
    return " ".join(sentences)


def _reviewer_text(facts: ChangeFacts) -> str:
    sentences: list[str] = []
    by_target: dict[str, list] = {}
    for caller in facts.callers:
        by_target.setdefault(caller.target, []).append(caller)
    for fn in facts.functions[:4]:
        callers = by_target.get(fn.name, [])
        shown = callers[:6]
        who = join_phrases(_caller_label(item) for item in shown)
        introduced = added_params(fn)
        dropped = removed_params(fn)
        plural = len(shown) != 1
        if shown and introduced:
            verb = "pass" if plural else "passes"
            sentences.append(f"Confirm {who} {verb} {join_phrases(code(name) for name in introduced)} to {code(fn.name)}.")
        elif shown and dropped:
            verb = "match" if plural else "matches"
            sentences.append(
                f"Confirm {who} {verb} {code(fn.name)} in {code(fn.path)}, "
                f"whose parameters no longer include {join_phrases(code(name) for name in dropped)}."
            )
        elif shown:
            verb = "match" if plural else "matches"
            sentences.append(f"Confirm {who} still {verb} {code(fn.name)} in {code(fn.path)}.")
        elif introduced:
            sentences.append(
                f"Confirm callers of {code(fn.name)} in {code(fn.path)} pass {join_phrases(code(name) for name in introduced)}. "
                "No caller is stored in this packet."
            )
    for item in facts.imports[:4]:
        fn = _fn(facts, item.target)
        dest = f"{code(item.target)} in {code(fn.path)}" if fn and fn.path else code(item.target)
        sentences.append(f"Confirm the import of {code(item.target)} in {code(item.path)} still matches {dest}.")
    missing = list(facts.missing_tests[:4])
    if missing:
        listed = join_phrases(_named(name, path) for name, path in missing)
        if len(missing) == 1:
            tail = "no test reference is stored."
        elif len(missing) == 2:
            tail = "no test reference is stored for either."
        else:
            tail = "no test reference is stored for these symbols."
        sentences.append(f"Check {listed}; {tail}")
    if facts.dependencies:
        sentences.append(
            f"Confirm {join_phrases(code(name) for name in facts.dependencies[:8])} "
            f"in {join_phrases(code(path) for path in facts.dependency_files) or code('package.json')} "
            "still match how dependencies are installed."
        )
    for path in facts.config_files[:3]:
        sentences.append(f"Confirm {code(path)} still matches the build settings in that file.")
    if sentences:
        return " ".join(sentences)
    if facts.changed_files:
        listed = join_phrases(code(path) for path in facts.changed_files[:6])
        return f"Read the diff of {listed}. No caller or test fact is stored."
    return "No caller, test, or dependency fact is stored for a review check."


def _risk_text(facts: ChangeFacts) -> str:
    sentences: list[str] = []
    if facts.functions:
        listed = join_phrases(
            f"{code(fn.name)} in {code(fn.path)}" if fn.path else code(fn.name) for fn in facts.functions[:4]
        )
        sentences.append(f"Scope includes {listed}.")
    outside_files: list[str] = []
    for caller in facts.callers:
        if caller.outside and caller.path and caller.path not in outside_files:
            outside_files.append(caller.path)
    for item in facts.imports:
        if item.outside and item.path not in outside_files:
            outside_files.append(item.path)
    for path, _names, outside in facts.reaches:
        if outside and path not in outside_files:
            outside_files.append(path)
    if outside_files:
        shown = outside_files[:6]
        extra = len(outside_files) - len(shown)
        tail = f" and {extra} more files" if extra else ""
        sentences.append(f"Outside this diff: {join_phrases(code(path) for path in shown)}{tail}.")
    if facts.config_files:
        sentences.append(f"Configuration in scope: {join_phrases(code(path) for path in facts.config_files[:4])}.")
    if facts.dependencies:
        sentences.append(f"Dependencies in scope: {join_phrases(code(name) for name in facts.dependencies[:8])}.")
    if not sentences and facts.changed_files:
        sentences.append(f"Scope is {join_phrases(code(path) for path in facts.changed_files[:6])}.")
    if not sentences:
        sentences.append("No changed function or file is stored.")
    return " ".join(sentences)


def _fn(facts: ChangeFacts, name: str) -> FnFact | None:
    for fn in facts.functions:
        if fn.name == name:
            return fn
    return None


def _caller_label(caller) -> str:
    if caller.path:
        return f"{code(caller.name)} in {code(caller.path)}"
    return code(caller.name)


def _named(name: str, path: str) -> str:
    if path:
        return f"{code(name)} in {code(path)}"
    return code(name)
