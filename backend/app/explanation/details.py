"""Review sections for the Details tab and the Details comment.

Every row comes from a stored claim, symbol, relationship, or evidence
record. A missing area is reported as none found. This module does not
add files, callers, or a judgment about safety.
"""

from __future__ import annotations

import re

_AREAS = ("API", "Database", "Auth", "Frontend", "Backend", "Tests", "Dependencies", "Configuration")
_UNKNOWN_KINDS = ("fanout_truncated", "ambiguous_call", "diff_only", "unknown_boundary")
_AUTH_STEMS = {"auth", "oauth", "password", "session", "login"}
_CONFIG_NAMES = {
    "tsconfig.json",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "alembic.ini",
}
_DEPENDENCY_NAMES = {
    "package.json",
    "package-lock.json",
    "requirements.txt",
    "pyproject.toml",
    "go.mod",
    "cargo.toml",
    "gemfile",
}


_TABLE_HEADERS = {
    "High-level areas affected": ("Area", "Names"),
    "Key Changes": ("Change", "Location"),
    "Behavior Changes": ("Call", "Evidence"),
    "Risk Areas": ("Where", "Why look"),
}
_KEY_LIMIT = 6


def build_details(
    *,
    symbols,
    relationships,
    evidences,
    claims,
    sections,
    repo: str,
    sha: str,
    document_unknowns: list[str] | None = None,
    review_questions: list[str] | None = None,
) -> dict:
    evidence_by_id = _evidence_index(evidences)
    behavior = _behavior_rows(symbols, relationships, claims, sections or [], evidence_by_id, repo, sha)
    return {
        "sections": [
            {"title": "Change Overview", "rows": _overview_rows(symbols, claims, behavior, evidence_by_id, repo, sha)},
            {"title": "High-level areas affected", "rows": _area_rows(symbols, claims, evidence_by_id, repo, sha)},
            {"title": "Key Changes", "rows": _key_rows(symbols, claims, evidence_by_id, repo, sha)},
            {"title": "Behavior Changes", "rows": behavior},
            {"title": "Risk Areas", "rows": _risk_rows(symbols, claims, evidence_by_id, repo, sha)},
            {"title": "What changed", "rows": _changed_rows(symbols, claims, evidence_by_id, repo, sha)},
            {"title": "Change flow", "rows": _flow_rows(sections or [], repo, sha)},
            {"title": "Impact", "rows": _impact_rows(symbols, claims, evidence_by_id, repo, sha)},
            {"title": "Shared code", "rows": _shared_rows(symbols, relationships)},
            {"title": "Tests", "rows": _test_rows(claims)},
            {"title": "Unchanged boundary", "rows": _boundary_rows(claims)},
            {"title": "Why a file outside the diff matters", "rows": _outside_rows(claims, evidence_by_id, repo, sha)},
            {"title": "Unknowns", "rows": _unknown_rows(claims, document_unknowns or [])},
            {
                "title": "Reviewer Attention",
                "rows": _attention_rows(symbols, claims, evidence_by_id, repo, sha),
                "subsections": [
                    {
                        "title": "Suggested review areas",
                        "rows": _suggested_rows(symbols, claims, evidence_by_id, repo, sha),
                    }
                ],
            },
            {"title": "Review questions", "rows": _question_rows(claims, review_questions or [])},
        ]
    }


def render_details_markdown(details: dict) -> str:
    blocks: list[str] = []
    for section in details.get("sections") or []:
        title = section.get("title")
        if title == "Impact":
            blocks.append(_impact_markdown(section))
            continue
        if title == "Change Overview":
            blocks.append(_overview_markdown(section))
            continue
        headers = _TABLE_HEADERS.get(title or "")
        if headers:
            blocks.append(_two_column_markdown(section, headers[0], headers[1]))
            continue
        lines = [f"### {title}"]
        for row in section.get("rows") or []:
            value = row.get("value") or "none found"
            href = row.get("href")
            shown = f"[{value}]({href})" if href else value
            lines.append(f"- **{row.get('label') or 'Item'}** — {shown}")
        if len(lines) == 1:
            lines.append("- **Item** — none found")
        body = "\n".join(lines)
        subsections = _subsection_markdown(section)
        if subsections:
            body = f"{body}\n\n{subsections}"
        blocks.append(body)
    return "\n\n".join(blocks)


def _overview_markdown(section: dict) -> str:
    lines = ["### Change Overview", ""]
    values = [
        row.get("value")
        for row in section.get("rows") or []
        if row.get("value") and row.get("value") != "none found"
    ]
    if not values:
        lines.append("none found")
    elif len(values) == 1:
        lines.append(values[0])
    else:
        lines.extend(f"- {value}" for value in values)
    return "\n".join(lines)


def _two_column_markdown(section: dict, left: str, right: str) -> str:
    lines = [
        f"### {section.get('title')}",
        "",
        f"| {left} | {right} |",
        "| --- | --- |",
    ]
    rows = section.get("rows") or []
    if not rows:
        lines.append("| Item | none found |")
        return "\n".join(lines)
    for row in rows:
        label = _md_cell(row.get("label") or "Item")
        value = row.get("value") or "none found"
        href = row.get("href")
        shown = f"[{_md_cell(value)}]({href})" if href else _md_cell(value)
        lines.append(f"| {label} | {shown} |")
    return "\n".join(lines)


def _impact_markdown(section: dict) -> str:
    lines = [
        "### Impact",
        "",
        "| Area | Reason | Evidence file |",
        "| --- | --- | --- |",
    ]
    rows = section.get("rows") or []
    if not rows:
        lines.append("| Item | none found | |")
        return "\n".join(lines)
    for row in rows:
        label = _md_cell(row.get("label") or "Item")
        value = _md_cell(row.get("value") or "none found")
        evidence = row.get("evidence") or ""
        href = row.get("href")
        if evidence and href:
            cell = f"[{_md_cell(evidence)}]({href})"
        elif evidence:
            cell = _md_cell(evidence)
        elif href:
            cell = href
        else:
            cell = ""
        lines.append(f"| {label} | {value} | {cell} |")
    return "\n".join(lines)


def _md_cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _overview_rows(symbols, claims, behavior, evidence_by_id, repo: str, sha: str) -> list[dict]:
    rows: list[dict] = []
    bits: list[str] = []
    for symbol in _changed_functions(symbols)[:4]:
        loc = _location(symbol.file_path, getattr(symbol, "start_line", None), getattr(symbol, "end_line", None))
        bits.append(f"{symbol.name} in {loc}")
    if not bits:
        for claim in claims:
            if _kind(claim) != "symbol_changed" or not _subject(claim):
                continue
            location, _href = _claim_location(claim, evidence_by_id, repo, sha)
            bits.append(f"{_subject(claim)} in {location}" if location else _subject(claim))
            if len(bits) == 4:
                break
    if bits:
        rows.append(_row("Changed", f"{_join(bits)} changed.", None))
        extra = len(_changed_functions(symbols)) - len(bits)
        if extra > 0:
            rows.append(_row("Changed", f"{extra} more changed symbols are stored.", None))
    else:
        files = [_subject(claim) for claim in claims if _kind(claim) == "file_changed" and _subject(claim)]
        if files:
            shown = files[:4]
            sentence = f"{_join(shown)} changed."
            if len(files) > len(shown):
                sentence += f" {len(files) - len(shown)} more changed files are stored."
            rows.append(_row("Changed", sentence, None))
    calls: list[str] = []
    for row in behavior:
        if row.get("value") in {None, "", "none found"}:
            continue
        if row.get("value") == "stored call":
            calls.append(row["label"])
        else:
            calls.append(f"{row['label']} at {row['value']}")
        if len(calls) == 2:
            break
    if calls:
        rows.append(_row("Calls", f"{_join(calls)}.", None))
    return rows or [_row("Overview", "none found", None)]


def _area_rows(symbols, claims, evidence_by_id, repo: str, sha: str) -> list[dict]:
    names: dict[str, list[str]] = {area: [] for area in _AREAS}

    def note(area: str, label: str | None) -> None:
        if not area or not label or label in names[area]:
            return
        names[area].append(label)

    for claim in claims:
        kind = _kind(claim)
        evidence, _href = _claim_location(claim, evidence_by_id, repo, sha)
        file = evidence.split(":")[0] if evidence else None
        if kind == "defines_api":
            note("API", _subject(claim) or file)
        elif kind in {"dependency_changed", "dependency"}:
            note("Dependencies", _subject(claim) or file)
        elif kind == "tests":
            note("Tests", file or _subject(claim))

    for path, _reason, _evidence, _href in _path_facts(symbols, claims, evidence_by_id, repo, sha):
        area = _path_area(path)
        if area is None or area == "API":
            continue
        if area == "Dependencies" and any(_kind(claim) in {"dependency_changed", "dependency"} for claim in claims):
            continue
        note(area, path)

    rows: list[dict] = []
    for area in _AREAS:
        found = names[area]
        if not found:
            continue
        shown = found[:6]
        value = ", ".join(shown)
        if len(found) > len(shown):
            value += f", {len(found) - len(shown)} more"
        rows.append(_row(area, value, None))
    return rows or [_row("Areas", "none found", None)]


def _key_rows(symbols, claims, evidence_by_id, repo: str, sha: str) -> list[dict]:
    changed = _changed_rows(symbols, claims, evidence_by_id, repo, sha)
    if len(changed) == 1 and changed[0].get("value") == "none found":
        return [_row("Changed", "none found", None)]
    exported = {_subject(claim) for claim in claims if _kind(claim) == "defines_api" and _subject(claim)}
    symbol_rows = [row for row in changed if not _is_path(row.get("label") or "")]
    file_rows = [row for row in changed if _is_path(row.get("label") or "")]
    symbol_rows.sort(key=lambda row: row.get("label") not in exported)
    chosen = symbol_rows[:_KEY_LIMIT]
    hidden = len(symbol_rows) - len(chosen)
    if not chosen:
        chosen = file_rows[:_KEY_LIMIT]
        hidden = len(file_rows) - len(chosen)
        if hidden:
            chosen.append(_row("More", f"{hidden} more changed files are stored.", None))
        return chosen or [_row("Changed", "none found", None)]
    if hidden:
        chosen.append(_row("More", f"{hidden} more changed symbols are stored.", None))
    return chosen


def _behavior_rows(symbols, relationships, claims, sections, evidence_by_id, repo: str, sha: str) -> list[dict]:
    rows = _behavior_from_relationships(symbols, relationships, evidence_by_id, repo, sha)
    if not rows:
        rows = _behavior_from_claims(claims, evidence_by_id, repo, sha)
    if not rows:
        rows = _behavior_from_sections(sections, repo, sha)
    return rows or [_row("Behavior", "none found", None)]


def _behavior_from_relationships(symbols, relationships, evidence_by_id, repo: str, sha: str) -> list[dict]:
    changed_ids, changed_names = _changed_identity(symbols)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        source = _rel_end(rel, "source")
        target = _rel_end(rel, "target")
        source_name = getattr(rel, "source_name", None) or ""
        target_name = getattr(rel, "target_name", None) or ""
        touches = (
            (source and source in changed_ids)
            or (target and target in changed_ids)
            or source_name in changed_names
            or target_name in changed_names
        )
        if not touches or not source_name or not target_name:
            continue
        label = f"{_short_name(source_name)} calls {target_name}"
        evidence_id = getattr(rel, "evidence_public_id", None) or getattr(rel, "evidence_id", None)
        value, href = _evidence_place(evidence_by_id.get(evidence_id), repo, sha)
        _push(rows, seen, label, value or "stored call", href)
    rows.sort(key=lambda row: (row["label"], row["value"]))
    return rows


def _behavior_from_claims(claims, evidence_by_id, repo: str, sha: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for claim in claims or []:
        if _kind(claim) != "calls":
            continue
        label = _sentence(claim)
        if not label or "import" in label.lower():
            continue
        value, href = _claim_location(claim, evidence_by_id, repo, sha)
        _push(rows, seen, label, value or "stored call", href)
    return rows


def _behavior_from_sections(sections, repo: str, sha: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for section in sections or []:
        heading = section.get("heading") or ""
        if heading in {"Changed", "Tests"}:
            continue
        for item in section.get("items") or []:
            text = (item.get("text") or "").strip()
            lowered = text.lower()
            if "calls " not in lowered or "import" in lowered or text == "no direct call found":
                continue
            label = f"{heading} {text}" if text.startswith("calls ") and heading else text
            detail = item.get("detail")
            href = _href_for_location(repo, sha, detail) if detail else None
            _push(rows, seen, label, detail or "stored call", href)
    return rows


def _risk_rows(symbols, claims, evidence_by_id, repo: str, sha: str) -> list[dict]:
    changed_names = _changed_names(symbols, claims)
    reason_files = {_subject(claim) for claim in claims if _kind(claim) == "file_reason" and _subject(claim)}
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for claim in claims:
        kind = _kind(claim)
        href = _claim_href(claim, evidence_by_id, repo, sha)
        if kind == "missing_test":
            subject = _subject(claim)
            if changed_names and subject not in changed_names:
                continue
            _push(rows, seen, subject or "Symbol", "no test reference is stored", None)
        elif kind == "file_reason" and _subject(claim):
            _push(rows, seen, _subject(claim), _sentence(claim), href)
        elif kind == "reaches_changed" and _subject(claim) and _subject(claim) not in reason_files:
            _push(rows, seen, _subject(claim), _outside_reason(claim), href)
        elif kind in _UNKNOWN_KINDS:
            _push(rows, seen, _subject(claim) or "Evidence", _sentence(claim), href)
    return rows or [_row("Attention", "none found", None)]


def _suggested_rows(symbols, claims, evidence_by_id, repo: str, sha: str) -> list[dict]:
    """Places to inspect, from areas, missing tests, and outside-diff reaches.

    An area with no stored fact is left out. This does not score the change.
    """
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in _area_rows(symbols, claims, evidence_by_id, repo, sha):
        if row.get("value") in {None, "", "none found"}:
            continue
        _push(rows, seen, row["label"], row["value"], row.get("href"))
    changed_names = _changed_names(symbols, claims)
    reason_files = {_subject(claim) for claim in claims if _kind(claim) == "file_reason" and _subject(claim)}
    for claim in claims:
        kind = _kind(claim)
        href = _claim_href(claim, evidence_by_id, repo, sha)
        if kind == "missing_test":
            subject = _subject(claim)
            if changed_names and subject not in changed_names:
                continue
            _push(rows, seen, subject or "Symbol", "no test reference is stored", None)
        elif kind == "file_reason" and _subject(claim):
            _push(rows, seen, _subject(claim), _sentence(claim), href)
        elif kind == "reaches_changed" and _subject(claim) and _subject(claim) not in reason_files:
            _push(rows, seen, _subject(claim), _outside_reason(claim), href)
    return rows


def _subsection_markdown(section: dict) -> str:
    blocks: list[str] = []
    for subsection in section.get("subsections") or []:
        title = subsection.get("title") or "Suggested review areas"
        lines = [f"#### {title}", ""]
        rows = [
            row
            for row in subsection.get("rows") or []
            if row.get("value") and row.get("value") != "none found"
        ]
        if not rows:
            lines.append("none found")
        else:
            lines.extend(["| Area | Why look |", "| --- | --- |"])
            for row in rows:
                label = _md_cell(row.get("label") or "Item")
                value = row.get("value") or "none found"
                href = row.get("href")
                shown = f"[{_md_cell(value)}]({href})" if href else _md_cell(value)
                lines.append(f"| {label} | {shown} |")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _attention_rows(symbols, claims, evidence_by_id, repo: str, sha: str) -> list[dict]:
    changed_names = _changed_names(symbols, claims)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    exported = 0
    for symbol in _changed_functions(symbols):
        if not getattr(symbol, "exported", False):
            continue
        if exported >= _KEY_LIMIT:
            break
        exported += 1
        loc = _location(symbol.file_path, getattr(symbol, "start_line", None), getattr(symbol, "end_line", None))
        href = _blob(repo, sha, symbol.file_path, getattr(symbol, "start_line", None), getattr(symbol, "end_line", None))
        where = f"at {loc}" if loc else "in this pull request"
        _push(rows, seen, symbol.name, f"exported in this pull request {where}", href)
    reason_files: set[str] = set()
    for claim in claims:
        if _kind(claim) != "file_reason" or not _subject(claim):
            continue
        reason_files.add(_subject(claim))
        _push(rows, seen, _subject(claim), _sentence(claim), _claim_href(claim, evidence_by_id, repo, sha))
    for claim in claims:
        if _kind(claim) != "reaches_changed" or not _subject(claim) or _subject(claim) in reason_files:
            continue
        _push(rows, seen, _subject(claim), _outside_reason(claim), _claim_href(claim, evidence_by_id, repo, sha))
    missing = 0
    for claim in claims:
        if _kind(claim) != "missing_test":
            continue
        subject = _subject(claim)
        if changed_names and subject not in changed_names:
            continue
        if missing >= _KEY_LIMIT:
            break
        missing += 1
        _push(rows, seen, subject or "Symbol", "no test reference is stored", None)
    for claim in claims:
        if _kind(claim) not in _UNKNOWN_KINDS:
            continue
        _push(rows, seen, _subject(claim) or "Evidence", _sentence(claim), _claim_href(claim, evidence_by_id, repo, sha))
    return rows or [_row("Inspect", "none found", None)]


def _changed_functions(symbols) -> list:
    rows = []
    for symbol in symbols or []:
        if getattr(symbol, "kind", None) != "function" or not getattr(symbol, "changed", False):
            continue
        if not getattr(symbol, "name", None) or not getattr(symbol, "file_path", None):
            continue
        rows.append(symbol)
    rows.sort(key=lambda symbol: (symbol.file_path, getattr(symbol, "start_line", 0) or 0, symbol.name))
    return rows


def _changed_identity(symbols) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    names: set[str] = set()
    for symbol in _changed_functions(symbols):
        symbol_id = _symbol_id(symbol)
        if symbol_id:
            ids.add(symbol_id)
        names.add(symbol.name)
    return ids, names


def _changed_names(symbols, claims) -> set[str]:
    names = {symbol.name for symbol in _changed_functions(symbols)}
    if names:
        return names
    return {_subject(claim) for claim in claims if _kind(claim) == "symbol_changed" and _subject(claim)}


def _evidence_place(item, repo: str, sha: str) -> tuple[str | None, str | None]:
    file = getattr(item, "file", None) if item is not None else None
    if not file:
        return None, None
    start = getattr(item, "start_line", None)
    end = getattr(item, "end_line", None)
    return _location(file, start, end), _blob(repo, sha, file, start, end)


def _push(rows: list[dict], seen: set[tuple[str, str]], label: str, value: str, href: str | None) -> None:
    key = (label, value)
    if not label or not value or key in seen:
        return
    seen.add(key)
    rows.append(_row(label, value, href))


def _is_path(label: str) -> bool:
    if "/" in label or "\\" in label:
        return True
    base = label.rsplit("/", 1)[-1].lower()
    if "." in base:
        return True
    return base in _CONFIG_NAMES or base in _DEPENDENCY_NAMES


def _short_name(name: str) -> str:
    if "/" in name:
        return name.rstrip("/").rsplit("/", 1)[-1]
    return name


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _impact_rows(symbols, claims, evidence_by_id, repo: str, sha: str) -> list[dict]:
    buckets: dict[str, list[dict]] = {area: [] for area in _AREAS}
    seen: set[tuple[str, str]] = set()

    def add(area: str, label: str, value: str, href: str | None, evidence: str | None) -> None:
        key = (area, label)
        if not label or key in seen:
            return
        seen.add(key)
        buckets[area].append(_impact_row(label, value or "none found", href, evidence))

    for claim in claims:
        kind = _kind(claim)
        evidence, href = _claim_location(claim, evidence_by_id, repo, sha)
        if kind == "defines_api":
            add("API", _subject(claim) or "API", _sentence(claim), href, evidence)
        elif kind in {"dependency_changed", "dependency"}:
            add("Dependencies", _subject(claim) or "Dependency", _sentence(claim), href, evidence)
        elif kind == "tests":
            add("Tests", _subject(claim) or "Test", _sentence(claim), href, evidence)

    for path, reason, evidence, href in _path_facts(symbols, claims, evidence_by_id, repo, sha):
        area = _path_area(path)
        if area is None or area == "API":
            continue
        if area == "Dependencies" and any(_kind(claim) in {"dependency_changed", "dependency"} for claim in claims):
            continue
        add(area, path, reason, href, evidence)

    rows: list[dict] = []
    for area in _AREAS:
        rows.extend(buckets[area] or [_row(area, "none found", None)])
    return rows


def _path_facts(symbols, claims, evidence_by_id, repo: str, sha: str) -> list[tuple[str, str, str | None, str | None]]:
    facts: list[tuple[str, str, str | None, str | None]] = []
    seen: set[str] = set()

    def push(path: str, reason: str, evidence: str | None, href: str | None) -> None:
        if not path or path in seen:
            return
        seen.add(path)
        facts.append((path, reason, evidence or path, href))

    for claim in claims:
        if _kind(claim) != "file_changed" or not _subject(claim):
            continue
        evidence, href = _claim_location(claim, evidence_by_id, repo, sha)
        push(_subject(claim), _sentence(claim), evidence, href)
    for symbol in symbols:
        if not getattr(symbol, "changed", False) or not getattr(symbol, "file_path", None):
            continue
        path = symbol.file_path
        start = getattr(symbol, "start_line", None)
        end = getattr(symbol, "end_line", None)
        reason = f"{symbol.name} changed" if getattr(symbol, "kind", None) == "function" else "changed file"
        evidence = _location(path, start, end) if start else path
        push(path, reason, evidence, _blob(repo, sha, path, start, end) if start else None)
    for claim in claims:
        if _kind(claim) not in {"reaches_changed", "file_reason"} or not _subject(claim):
            continue
        evidence, href = _claim_location(claim, evidence_by_id, repo, sha)
        push(_subject(claim), _outside_reason(claim), evidence, href)
    return facts


def _changed_rows(symbols, claims, evidence_by_id, repo: str, sha: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def push(label: str, value: str, href: str | None) -> None:
        key = (label, value)
        if not label or not value or key in seen:
            return
        seen.add(key)
        rows.append(_row(label, value, href))

    named: set[str] = set()
    for symbol in symbols:
        if getattr(symbol, "kind", None) != "function" or not getattr(symbol, "changed", False):
            continue
        if not getattr(symbol, "name", None) or not getattr(symbol, "file_path", None):
            continue
        value = _location(symbol.file_path, getattr(symbol, "start_line", None), getattr(symbol, "end_line", None))
        push(symbol.name, value, _blob(repo, sha, symbol.file_path, getattr(symbol, "start_line", None), getattr(symbol, "end_line", None)))
        named.add(symbol.name)
    for claim in claims:
        if _kind(claim) != "symbol_changed":
            continue
        subject = _subject(claim)
        if subject in named:
            continue
        location, href = _claim_location(claim, evidence_by_id, repo, sha)
        push(subject or "Symbol", location or "changed", href)
    for claim in claims:
        if _kind(claim) != "file_changed" or not _subject(claim):
            continue
        location, href = _claim_location(claim, evidence_by_id, repo, sha)
        push(_subject(claim), location or "changed file", href)
    return rows or [_row("Changed", "none found", None)]


def _flow_rows(sections, repo: str, sha: str) -> list[dict]:
    rows: list[dict] = []
    for section in sections:
        heading = section.get("heading") or "Change flow"
        items = section.get("items") or []
        if not items:
            rows.append(_row(heading, "none found", None))
            continue
        for item in items:
            text = item.get("text") or "none found"
            detail = item.get("detail")
            href = _href_for_location(repo, sha, detail) if detail else None
            value = f"{text} ({detail})" if detail else text
            rows.append(_row(heading, value, href))
    return rows or [_row("Change flow", "none found", None)]


def _shared_rows(symbols, relationships) -> list[dict]:
    changed_ids = {
        symbol_id
        for symbol in symbols
        if getattr(symbol, "changed", False) and (symbol_id := _symbol_id(symbol))
    }
    callers: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for rel in relationships:
        if getattr(rel, "type", None) != "CALLS":
            continue
        source = _rel_end(rel, "source")
        target = _rel_end(rel, "target")
        if not source or not target or target not in changed_ids:
            continue
        caller = getattr(rel, "source_name", None) or source
        names.setdefault(target, getattr(rel, "target_name", None) or target)
        bucket = callers.setdefault(target, [])
        if caller not in bucket:
            bucket.append(caller)
    rows = []
    for target, sources in sorted(callers.items(), key=lambda item: names.get(item[0], item[0])):
        if len(sources) < 2:
            continue
        rows.append(_row(names.get(target, target), "callers: " + ", ".join(sources), None))
    return rows or [_row("Shared code", "none found", None)]


def _test_rows(claims) -> list[dict]:
    rows: list[dict] = []
    for claim in claims:
        if _kind(claim) == "tests":
            rows.append(_row(_subject(claim) or "Test", _sentence(claim), None))
        elif _kind(claim) == "missing_test":
            subject = _subject(claim) or "Symbol"
            rows.append(_row(subject, "no test reference", None))
    return rows or [_row("Tests", "none found", None)]


def _boundary_rows(claims) -> list[dict]:
    rows: list[dict] = []
    reaching = {_subject(claim) for claim in claims if _kind(claim) == "reaches_changed" and _subject(claim)}
    for claim in claims:
        subject = _subject(claim)
        if _kind(claim) == "reaches_changed" and subject:
            rows.append(_row(subject, _outside_reason(claim), None))
        elif _kind(claim) == "behavior_unchanged" and subject and subject not in reaching:
            rows.append(_row(subject, "does not reach a changed symbol", None))
    return rows or [_row("Boundary", "none found", None)]


def _outside_rows(claims, evidence_by_id, repo: str, sha: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for claim in claims:
        if _kind(claim) not in {"file_reason", "reaches_changed"}:
            continue
        path = _subject(claim)
        if not path or path in seen:
            continue
        seen.add(path)
        rows.append(_row(path, _outside_reason(claim), _claim_href(claim, evidence_by_id, repo, sha)))
    return rows or [_row("Outside the diff", "none found", None)]


def _unknown_rows(claims, document_unknowns: list[str]) -> list[dict]:
    texts: list[str] = []
    for text in document_unknowns:
        cleaned = (text or "").strip()
        if cleaned and cleaned not in texts:
            texts.append(cleaned)
    for claim in claims:
        if _kind(claim) not in _UNKNOWN_KINDS:
            continue
        cleaned = _claim_text(claim).strip()
        if cleaned and cleaned not in texts:
            texts.append(cleaned)
    if not texts:
        return [_row("Unknowns", "none found", None)]
    return [_row(_unknown_label(text), text[:-1] if text.endswith(".") else text, None) for text in texts]


def _question_rows(claims, review_questions: list[str]) -> list[dict]:
    texts: list[str] = []
    for text in review_questions:
        cleaned = (text or "").strip()
        if cleaned and cleaned not in texts:
            texts.append(cleaned)
    for claim in claims:
        question = _question_for(claim)
        if question and question not in texts:
            texts.append(question)
    if not texts:
        return [_row("Review", "none found", None)]
    return [_row("Review", text, None) for text in texts]


def _question_for(claim) -> str | None:
    kind = _kind(claim)
    subject = _subject(claim) or "this symbol"
    sentence = _sentence(claim)
    if kind == "missing_test":
        return f"What test should reference {subject}? No test reference is stored."
    if kind == "ambiguous_call":
        return f"Which function does this call resolve to? {sentence}"
    if kind == "fanout_truncated":
        return f"Which callers were not stored? {sentence}"
    if kind == "diff_only":
        return f"What behavior is outside the analyzed files? {sentence}"
    if kind == "unknown_boundary":
        return f"What reaches this boundary? {sentence}"
    return None


def _path_area(path: str) -> str | None:
    lowered = path.replace("\\", "/").lower()
    base = lowered.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    if ".test." in base or ".spec." in base or "/test/" in f"/{lowered}" or lowered.startswith("tests/"):
        return "Tests"
    if base in _DEPENDENCY_NAMES:
        return "Dependencies"
    if base in _CONFIG_NAMES or base.startswith("tsconfig.") or base.startswith(".env"):
        return "Configuration"
    if "/migrations/" in f"/{lowered}" or "/db/" in f"/{lowered}" or base.endswith(".sql"):
        return "Database"
    if stem in _AUTH_STEMS or any(part in _AUTH_STEMS for part in lowered.split("/")):
        return "Auth"
    if lowered.startswith("frontend/") or "/frontend/" in f"/{lowered}" or base.endswith((".tsx", ".css", ".scss", ".vue")):
        return "Frontend"
    if lowered.startswith("backend/") or "/backend/" in f"/{lowered}" or base.endswith(".py"):
        return "Backend"
    return None


def _outside_reason(claim) -> str:
    text = _claim_text(claim)
    match = re.search(r"reaches changed symbol (.+), so its behavior", text)
    if _kind(claim) == "reaches_changed" and match:
        return f"reaches changed symbol {match.group(1)}"
    if _kind(claim) == "file_reason":
        return _sentence(claim)
    return _sentence(claim)


def _unknown_label(text: str) -> str:
    if re.search(r"dependenc", text, re.I):
        return "Dependencies"
    if re.search(r"database|schema", text, re.I):
        return "Database"
    if re.search(r"external", text, re.I):
        return "External"
    return "Unknown"


def _claim_location(claim, evidence_by_id, repo: str, sha: str) -> tuple[str | None, str | None]:
    for evidence_id in _evidence_ids(claim):
        item = evidence_by_id.get(evidence_id)
        file = getattr(item, "file", None) if item is not None else None
        if not file:
            continue
        start = getattr(item, "start_line", None)
        end = getattr(item, "end_line", None)
        return _location(file, start, end), _blob(repo, sha, file, start, end)
    return None, None


def _claim_href(claim, evidence_by_id, repo: str, sha: str) -> str | None:
    return _claim_location(claim, evidence_by_id, repo, sha)[1]


def _evidence_index(evidences) -> dict:
    found = {}
    for item in evidences or []:
        evidence_id = getattr(item, "public_id", None) or getattr(item, "id", None)
        if evidence_id:
            found[evidence_id] = item
    return found


def _row(label: str, value: str, href: str | None) -> dict:
    return {"label": label, "value": value, "href": href}


def _impact_row(label: str, value: str, href: str | None, evidence: str | None) -> dict:
    row = _row(label, value, href)
    if evidence:
        row["evidence"] = evidence
    return row


def _location(file: str | None, start, end) -> str:
    if file and start and end and end != start:
        return f"{file}:{start}-{end}"
    if file and start:
        return f"{file}:{start}"
    return file or "changed"


def _href_for_location(repo: str, sha: str, location: str | None) -> str | None:
    if not location:
        return None
    match = re.match(r"^(.*):(\d+)(?:-(\d+))?$", location)
    if not match:
        return None
    end = int(match.group(3)) if match.group(3) else None
    return _blob(repo, sha, match.group(1), int(match.group(2)), end)


def _blob(repo: str, sha: str, file: str | None, start, end) -> str | None:
    if not repo or not sha or not file:
        return None
    base = f"https://github.com/{repo}/blob/{sha}/{file}"
    if start and end and end != start:
        return f"{base}#L{start}-L{end}"
    if start:
        return f"{base}#L{start}"
    return base


def _symbol_id(symbol) -> str | None:
    public_id = getattr(symbol, "public_id", None)
    if public_id:
        return public_id
    symbol_id = getattr(symbol, "id", None)
    return str(symbol_id) if symbol_id else None


def _rel_end(rel, end: str) -> str | None:
    public_id = getattr(rel, f"{end}_public_id", None)
    if public_id:
        return public_id
    symbol_id = getattr(rel, f"{end}_id", None)
    return symbol_id or None


def _kind(claim) -> str:
    return getattr(claim, "kind", "") or ""


def _subject(claim) -> str:
    return getattr(claim, "subject", None) or ""


def _claim_text(claim) -> str:
    return getattr(claim, "text", None) or ""


def _sentence(claim) -> str:
    subject = _subject(claim)
    text = _claim_text(claim).strip()
    if subject and text.startswith(subject):
        text = text[len(subject) :].strip()
    if text.endswith("."):
        text = text[:-1]
    return text or "none found"


def _evidence_ids(claim) -> list:
    ids = getattr(claim, "evidence_ids", None)
    if ids is None:
        ids = getattr(claim, "evidence_public_ids", None)
    return list(ids or [])
