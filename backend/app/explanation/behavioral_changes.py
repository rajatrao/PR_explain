"""Behavioral summary for the Explain view, from stored facts and the diff."""

from __future__ import annotations

from app.explanation.change_facts import (
    ChangeFacts,
    FnFact,
    added_params,
    body_clauses,
    code,
    collect_change_facts,
    guard,
    join_phrases,
    removed_params,
)


def build_behavioral_changes(*, symbols, relationships, claims, patches=None) -> list[dict]:
    """Before, Now, Conditions, and What observers notice for this pull request."""
    facts = collect_change_facts(
        symbols=symbols,
        relationships=relationships,
        claims=claims,
        patches=patches,
    )
    rows = [
        _row("Before", _before_text(facts)),
        _row("Now", _now_text(facts)),
        _row("Conditions", _conditions_text(facts)),
        _row("What observers notice", _observers_text(facts)),
    ]
    return [_finish(row, facts) for row in rows]


def render_behavioral_changes_markdown(rows: list[dict]) -> str:
    lines = ["### Behavioral Changes", ""]
    for row in rows:
        lines.append(f"- **{row['label']}** — {row['value']}")
    return "\n".join(lines)


def _row(label: str, value: str) -> dict:
    return {"label": label, "value": value, "href": None}


def _finish(row: dict, facts: ChangeFacts) -> dict:
    value = guard(row["value"], facts.hidden)
    if value == row["value"]:
        return row
    return {**row, "value": value or row["value"]}


def _before_text(facts: ChangeFacts) -> str:
    if facts.functions:
        sentences = [_before_function(fn) for fn in facts.functions[:4]]
        extra = facts.functions[4:]
        if extra:
            sentences.append("Also recorded as changed: " + _name_list(extra) + ".")
        return " ".join(sentences)
    if facts.other_files:
        return " ".join(_before_file(edit) for edit in facts.other_files[:4])
    if facts.changed_files:
        return (
            "Previous behavior is not established from this pull request. "
            f"Changed files: {join_phrases(code(path) for path in facts.changed_files[:6])}."
        )
    return "Previous behavior is not established from this pull request. No changed function or file is stored."


def _before_function(fn: FnFact) -> str:
    params = fn.old_params if fn.old_params is not None else fn.context_params
    clauses = body_clauses(fn.removed_lines, past=True, extra=fn.removed_extra)
    if params is not None and (fn.old_params is not None or fn.removed_lines):
        clauses.insert(0, f"accepted {_param_phrase(params)}")
    if clauses:
        return f"{code(fn.name)} in {code(fn.path)} {join_phrases(clauses)}."
    where = f"{code(fn.name)} in {code(fn.path)}"
    if params is not None and fn.context_params is not None and not fn.removed_lines:
        return (
            f"Previous body lines for {where} are not in the stored diff. "
            f"The signature in the diff lists {_param_phrase(params)}."
        )
    return f"Previous lines for {where} are not in the stored diff. {where} is the changed function."


def _now_text(facts: ChangeFacts) -> str:
    if facts.functions:
        sentences = [_now_function(fn) for fn in facts.functions[:4]]
        extra = facts.functions[4:]
        if extra:
            sentences.append("Also changed: " + _name_list(extra) + ".")
        return " ".join(sentences)
    if facts.other_files:
        sentences = [_now_file(edit) for edit in facts.other_files[:4]]
        if facts.dependencies:
            sentences.append(_dependency_sentence(facts))
        return " ".join(sentences)
    if facts.dependencies:
        return _dependency_sentence(facts) + " No changed function is stored."
    if facts.changed_files:
        listed = join_phrases(code(path) for path in facts.changed_files[:6])
        return f"{listed} changed in this pull request. No changed function is stored."
    return "No changed function or file is stored for this pull request."


def _now_function(fn: FnFact) -> str:
    params = fn.new_params if fn.new_params is not None else fn.context_params
    clauses = body_clauses(fn.added_lines, past=False, extra=fn.added_extra)
    if params is not None and (fn.new_params is not None or fn.added_lines or fn.context_params is not None):
        if fn.new_params is not None or fn.added_lines:
            clauses.insert(0, f"accepts {_param_phrase(params)}")
    if not clauses:
        text = f"{code(fn.name)} in {code(fn.path)} changed."
    else:
        text = f"{code(fn.name)} in {code(fn.path)} {join_phrases(clauses)}."
    dropped = removed_params(fn)
    introduced = added_params(fn)
    notes: list[str] = []
    if introduced:
        notes.append(f"Added parameters: {join_phrases(code(name) for name in introduced)}.")
    if dropped:
        notes.append(f"Removed parameters: {join_phrases(code(name) for name in dropped)}.")
    if notes:
        text = text + " " + " ".join(notes)
    return text


def _conditions_text(facts: ChangeFacts) -> str:
    sentences: list[str] = []
    by_target: dict[str, list] = {}
    for caller in facts.callers:
        by_target.setdefault(caller.target, []).append(caller)
    for target, callers in by_target.items():
        shown = callers[:6]
        who = join_phrases((f"{code(item.name)} in {code(item.path)}" if item.path else code(item.name) for item in shown), "or")
        sentence = f"When {who} calls {code(target)}."
        extra = len(callers) - len(shown)
        if extra:
            sentence = sentence[:-1] + f", plus {extra} more stored callers."
        outside = [item.path for item in shown if item.outside and item.path]
        inside = [item.path for item in shown if not item.outside and item.path]
        if outside:
            sentence += f" {join_phrases(code(path) for path in _unique(outside))} {'is' if len(outside) == 1 else 'are'} outside this diff."
        if inside:
            sentence += f" {join_phrases(code(path) for path in _unique(inside))} {'is' if len(inside) == 1 else 'are'} in this diff."
        sentences.append(sentence)
    if facts.functions and not facts.callers:
        sentences.append(f"No stored call invokes {_name_list(facts.functions[:4])}.")
    if sentences:
        return " ".join(sentences)
    if facts.changed_files:
        listed = join_phrases(code(path) for path in facts.changed_files[:6])
        return f"No stored call is tied to a changed function. Changed files: {listed}."
    return "No caller of a changed function is stored."


def _observers_text(facts: ChangeFacts) -> str:
    sentences: list[str] = []
    by_target: dict[str, list] = {}
    for caller in facts.callers:
        by_target.setdefault(caller.target, []).append(caller)
    for target, callers in by_target.items():
        shown = callers[:6]
        who = join_phrases(
            (f"{code(item.name)} in {code(item.path)}" if item.path else code(item.name) for item in shown)
        )
        verb = "calls" if len(shown) == 1 else "call"
        sentences.append(f"{who} {verb} {code(target)}.")
    for path, names, outside in facts.reaches[:4]:
        place = ", outside this diff, " if outside else " "
        sentences.append(f"{code(path)}{place}reaches {join_phrases(code(name) for name in names)}.")
    exported = [fn for fn in facts.functions if fn.exported]
    if exported:
        listed = join_phrases(f"{code(fn.name)} in {code(fn.path)}" for fn in exported[:4])
        verb = "is" if len(exported) == 1 else "are"
        sentences.append(f"{listed} {verb} exported.")
    missing_changed = [item for item in facts.missing_tests if item[0] in {fn.name for fn in facts.functions}]
    if missing_changed:
        sentences.append(f"No stored test references {join_phrases(code(name) for name, _path in missing_changed[:4])}.")
    tested = [item for item in facts.tested if item[0] not in {name for name, _path in missing_changed}]
    if tested:
        sentences.append(f"Stored tests reference {join_phrases(code(name) for name, _path in tested[:4])}.")
    if sentences:
        return " ".join(sentences)
    if facts.functions:
        return f"No caller or test reference is stored for {_name_list(facts.functions[:4])}."
    return "No caller, export, or test reference is stored for a changed function."


def _before_file(edit) -> str:
    clauses = body_clauses(edit.removed_lines, past=True, extra=edit.removed_extra)
    if clauses:
        return f"{code(edit.path)} {join_phrases(clauses)}."
    return f"Previous lines for {code(edit.path)} are not in the stored diff."


def _now_file(edit) -> str:
    clauses = body_clauses(edit.added_lines, past=False, extra=edit.added_extra)
    if clauses:
        return f"{code(edit.path)} {join_phrases(clauses)}."
    return f"{code(edit.path)} changed in this pull request. No changed function is stored in that file."


def _dependency_sentence(facts: ChangeFacts) -> str:
    files = join_phrases(code(path) for path in facts.dependency_files) or code("package.json")
    if facts.dependencies:
        return f"{files} changes {join_phrases(code(name) for name in facts.dependencies[:8])}."
    return f"{files} changed."


def _name_list(functions) -> str:
    return join_phrases(f"{code(fn.name)} in {code(fn.path)}" if fn.path else code(fn.name) for fn in functions)


def _param_phrase(params: tuple[str, ...]) -> str:
    if not params:
        return "no parameters"
    return join_phrases(code(name) for name in params)


def _unique(items: list[str]) -> list[str]:
    found: list[str] = []
    for item in items:
        if item and item not in found:
            found.append(item)
    return found
