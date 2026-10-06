"""Review sections for the Details tab and the Details comment.

Every row comes from a stored claim, symbol, relationship, or evidence
record. A missing area is reported as none found. This module does not
add files, callers, or a judgment about safety.
"""

from __future__ import annotations

import re

from app.analyzer.parse import is_dunder_name, is_package_marker, is_private_python_name, is_test_path
from app.explanation.key_changes import build_key_rows, build_outside_rows
from app.explanation.explain_view import private_python_hidden_names, without_hidden_symbols

_AREAS = ("API", "Database", "Auth", "Frontend", "Backend", "Tests", "Dependencies", "Configuration")

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


OUTSIDE_TITLE = "Callers outside the diff"

_TABLE_HEADERS = {
    "High-level areas affected": ("Area", "Names"),
    "Key Changes": ("Function", "What it means for callers"),
    "Risk Areas": ("Where", "Why look"),
}
_KEY_LIMIT = 6
_VISIBLE_ROWS = 20
_NOISE_SYMBOLS = {
    "add",
    "_symbol_id",
    "_text",
    "_subject",
    "_kind",
    "_rel_end",
    "_location",
    "_detail",
    "_field",
}
# Field getters such as _text or _symbol_id. Not a changed behavior function like _configured.
_ACCESSOR_NAME = re.compile(
    r"_(?:text|subject|kind|field|detail|location|name|value|path|file|id|label|symbol_id|rel_end)\Z"
)
_GENERIC_VERBS = {"push", "request", "upgrade", "main", "statements", "compare"}
_FOLDED_SECTIONS: set[str] = set()


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
    built: list[dict] = [
        {"title": "High-level areas affected", "rows": _area_rows(symbols, claims, evidence_by_id, repo, sha)},
        {
            "title": "Key Changes",
            "rows": _key_change_rows(symbols, relationships, claims, evidences, evidence_by_id, repo, sha),
        },
    ]
    _append_section(built, "Risk Areas", _risk_rows(symbols, claims, evidence_by_id, repo, sha), drop_empty=True)
    built.extend(
        [
            {"title": "What changed", "rows": _changed_rows(symbols, claims, evidence_by_id, repo, sha)},
        ]
    )
    built.extend(
        [
            {"title": "Shared code", "rows": _shared_rows(symbols, relationships)},
            {
                "title": OUTSIDE_TITLE,
                "rows": build_outside_rows(
                    symbols=symbols, relationships=relationships, claims=claims, evidences=evidences, repo=repo, sha=sha
                ),
            },
        ]
    )
    hidden = private_python_hidden_names(
        symbols,
        claims,
        claim_file=lambda claim: _claim_file(claim, evidence_by_id),
    )
    return {"sections": _strip_private_python_from_details(built, hidden)}


def _strip_private_python_from_details(sections: list[dict], hidden: set[str]) -> list[dict]:
    if not hidden:
        return sections
    stripped: list[dict] = []
    for section in sections:
        updated = dict(section)
        updated["rows"] = _strip_private_python_rows(section.get("rows") or [], hidden)
        subsections = []
        for subsection in section.get("subsections") or []:
            subsections.append(
                {
                    **subsection,
                    "rows": _strip_private_python_rows(subsection.get("rows") or [], hidden),
                }
            )
        if subsections:
            updated["subsections"] = subsections
        stripped.append(updated)
    return stripped


def _strip_private_python_rows(rows: list[dict], hidden: set[str]) -> list[dict]:
    kept: list[dict] = []
    for row in rows:
        cleaned = _strip_private_python_row(row, hidden)
        if cleaned is not None:
            kept.append(cleaned)
    return kept


def _strip_private_python_row(row: dict, hidden: set[str]) -> dict | None:
    label = (row.get("label") or "").strip()
    if label in hidden:
        return None
    new_label = without_hidden_symbols(label, hidden)
    if not new_label:
        return None
    value = row.get("value")
    new_value = value
    if value is not None:
        cleaned = without_hidden_symbols(str(value), hidden)
        if cleaned is None:
            return None
        new_value = cleaned
    evidence = row.get("evidence")
    new_evidence = evidence
    if evidence is not None:
        cleaned = without_hidden_symbols(str(evidence), hidden)
        if cleaned is None:
            return None
        new_evidence = cleaned
    if new_label == label and new_value == value and new_evidence == evidence:
        return row
    updated = dict(row)
    updated["label"] = new_label
    updated["value"] = new_value
    if evidence is not None:
        updated["evidence"] = new_evidence
    return updated


def _append_section(sections: list[dict], title: str, rows: list[dict], *, drop_empty: bool = False) -> None:
    kept = [row for row in rows or [] if not _is_none_found_row(row)] if drop_empty else list(rows or [])
    if drop_empty and not kept:
        return
    sections.append({"title": title, "rows": kept})


def _is_none_found_row(row: dict) -> bool:
    value = " ".join(str(row.get("value") or "").split()).casefold()
    return value in {"", "none found"}


def render_details_markdown(details: dict) -> str:
    blocks: list[str] = []
    for section in details.get("sections") or []:
        title = section.get("title")
        headers = _TABLE_HEADERS.get(title or "")
        if headers:
            blocks.append(_two_column_markdown(section, headers[0], headers[1]))
            continue
        if title in _FOLDED_SECTIONS:
            blocks.append(_folded_markdown(section))
            continue
        lines = [f"### {title}"]
        rows = section.get("rows") or []
        shown, hidden = _split_rows(rows)
        lines.extend(_bullet_lines(shown))
        if len(lines) == 1:
            lines.append("- **Item** — none found")
        if hidden:
            lines.extend(_collapsed_block(len(hidden), _bullet_lines(hidden)))
        body = "\n".join(lines)
        subsections = _subsection_markdown(section)
        if subsections:
            body = f"{body}\n\n{subsections}"
        blocks.append(body)
    return "\n\n".join(blocks)


def _folded_markdown(section: dict) -> str:
    """Whole section body stays closed. The summary is the section title."""
    title = section.get("title") or "Section"
    rows = section.get("rows") or []
    body = _bullet_lines(rows) or ["- **Item** — none found"]
    return "\n".join(["<details>", f"<summary>{title}</summary>", "", *body, "</details>"])


def _split_rows(rows: list) -> tuple[list, list]:
    if len(rows) <= _VISIBLE_ROWS:
        return list(rows), []
    return list(rows[:_VISIBLE_ROWS]), list(rows[_VISIBLE_ROWS:])


def _collapsed_block(hidden_count: int, body: list[str]) -> list[str]:
    """Collapsed GitHub block. No open attribute, so the extra rows stay hidden."""
    return ["", "<details>", f"<summary>{hidden_count} more</summary>", "", *body, "</details>"]


def _bullet_lines(rows: list) -> list[str]:
    lines = []
    for row in rows:
        value = row.get("value") or "none found"
        href = row.get("href")
        shown = f"[{value}]({href})" if href else value
        label = f"**{row.get('label') or 'Item'}**"
        if row.get("label_href"):
            label = f"[{label}]({row['label_href']})"
        lines.append(f"- {label} — {shown}")
    return lines


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
    shown, hidden = _split_rows(rows)
    lines.extend(_two_column_row_lines(shown))
    if hidden:
        lines.extend(
            _collapsed_block(
                len(hidden),
                [f"| {left} | {right} |", "| --- | --- |", *_two_column_row_lines(hidden)],
            )
        )
    return "\n".join(lines)


def _two_column_row_lines(rows: list) -> list[str]:
    lines = []
    for row in rows:
        label = _md_cell(row.get("label") or "Item")
        if row.get("label_href"):
            label = f"[{label}]({row['label_href']})"
        value = row.get("value") or "none found"
        href = row.get("href")
        shown = f"[{_md_cell(value)}]({href})" if href else _md_cell(value)
        lines.append(f"| {label} | {shown} |")
    return lines


def _md_cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


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


def _key_change_rows(symbols, relationships, claims, evidences, evidence_by_id, repo: str, sha: str) -> list[dict]:
    """Changed functions with what they mean for callers; changed files when no function changed."""
    changed = _changed_rows(symbols, claims, evidence_by_id, repo, sha)
    symbol_rows = [row for row in changed if not _is_path(row.get("label") or "") and row.get("value") != "none found"]
    rows = build_key_rows(
        symbols=symbols,
        relationships=relationships,
        claims=claims,
        evidences=evidences,
        repo=repo,
        sha=sha,
        fallback_rows=symbol_rows,
    )
    if len(rows) == 1 and rows[0].get("value") == "none found":
        return _key_rows(symbols, claims, evidence_by_id, repo, sha)
    return rows


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


def _risk_rows(symbols, claims, evidence_by_id, repo: str, sha: str) -> list[dict]:
    """Changed production symbols with no stored test, and files outside the diff that reach one."""
    production = _production_changed_names(symbols, claims)
    reason_files = {_subject(claim) for claim in claims if _kind(claim) == "file_reason" and _subject(claim)}
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for claim in claims:
        kind = _kind(claim)
        href = _claim_href(claim, evidence_by_id, repo, sha)
        if kind == "missing_test":
            subject = _subject(claim)
            if subject not in production or _low_value_symbol(subject, production):
                continue
            _push(rows, seen, subject, "no test reference is stored", None)
        elif kind == "file_reason" and _subject(claim):
            if _is_test_evidence(_subject(claim)) or not _mentions_production(claim, production):
                continue
            _push(rows, seen, _subject(claim), _sentence(claim), href)
        elif kind == "reaches_changed" and _subject(claim) and _subject(claim) not in reason_files:
            if _is_test_evidence(_subject(claim)) or not _mentions_production(claim, production):
                continue
            _push(rows, seen, _subject(claim), _outside_reason(claim), href)
    return _dedupe_risk_rows(rows)


def _production_changed_names(symbols, claims) -> set[str]:
    names = {symbol.name for symbol in _changed_functions(symbols) if not _noise_symbol(symbol.name)}
    if names:
        return names
    found = set()
    for claim in claims or []:
        if _kind(claim) != "symbol_changed":
            continue
        subject = _subject(claim)
        if subject and not _noise_symbol(subject):
            found.add(subject)
    return found


def _noise_symbol(name: str | None) -> bool:
    """Dunder methods and private field getters. A changed behavior function is not one of these."""
    if not name:
        return False
    if name in _NOISE_SYMBOLS or is_dunder_name(name):
        return True
    return _ACCESSOR_NAME.fullmatch(name) is not None


def _low_value_symbol(name: str | None, production: set[str]) -> bool:
    """Generic verbs stay out unless this pull request actually changed that symbol."""
    if _noise_symbol(name):
        return True
    return bool(name) and name in _GENERIC_VERBS and name not in production


def _mentions_production(claim, production: set[str]) -> bool:
    text = _claim_text(claim)
    for name in production:
        if name and re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text):
            return True
    return False


def _risk_preference(row: dict) -> int:
    """Lower is the row to keep when one subject is repeated with different wording."""
    value = (row.get("value") or "").lower()
    if "no test reference is stored" in value:
        return 0
    if "reaches changed symbol" in value or "not in the diff" in value:
        return 1
    return 2


def _dedupe_risk_rows(rows: list[dict]) -> list[dict]:
    """One row per subject. Drop duplicate private accessors. Keep a real symbol."""
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        label = row.get("label") or ""
        if not label or _noise_symbol(label):
            continue
        bucket = grouped.get(label)
        if bucket is None:
            grouped[label] = bucket = []
            order.append(label)
        value = row.get("value") or ""
        if any((item.get("value") or "") == value for item in bucket):
            continue
        bucket.append(row)
    kept: list[dict] = []
    for label in order:
        items = grouped[label]
        preferred = [item for item in items if _risk_preference(item) < 2 and not _is_none_found_row(item)]
        if not preferred:
            continue
        preferred.sort(key=_risk_preference)
        kept.append(preferred[0])
    return kept


def _subsection_markdown(section: dict) -> str:
    blocks: list[str] = []
    for subsection in section.get("subsections") or []:
        title = subsection.get("title") or "Section"
        lines = [f"#### {title}", ""]
        rows = [
            row
            for row in subsection.get("rows") or []
            if row.get("value") and row.get("value") != "none found"
        ]
        if not rows:
            lines.append("none found")
        else:
            lines.extend(["| Where | Why look |", "| --- | --- |"])
            for row in rows:
                label = _md_cell(row.get("label") or "Item")
                value = row.get("value") or "none found"
                href = row.get("href")
                shown = f"[{_md_cell(value)}]({href})" if href else _md_cell(value)
                lines.append(f"| {label} | {shown} |")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _hidden_review_file(path: str | None) -> bool:
    return bool(path) and (is_test_path(path) or is_package_marker(path))


def _is_test_evidence(text: str | None) -> bool:
    """True when text names a test file. Production paths stay."""
    if not text:
        return False
    normalized = text.replace("\\", "/")
    if ".test." in normalized or ".spec." in normalized:
        return True
    for token in re.findall(r"[A-Za-z0-9_./-]+\.[A-Za-z0-9]+", normalized):
        path = re.split(r":\d", token, maxsplit=1)[0]
        if is_test_path(path):
            return True
    return False


def _claim_file(claim, evidence_by_id) -> str | None:
    for evidence_id in _evidence_ids(claim):
        item = evidence_by_id.get(evidence_id)
        file = getattr(item, "file", None) if item is not None else None
        if file:
            return file
    return None


def _changed_functions(symbols) -> list:
    rows = []
    for symbol in symbols or []:
        if getattr(symbol, "kind", None) != "function" or not getattr(symbol, "changed", False):
            continue
        if not getattr(symbol, "name", None) or not getattr(symbol, "file_path", None):
            continue
        if _hidden_review_file(symbol.file_path) or is_dunder_name(symbol.name):
            continue
        if is_private_python_name(symbol.name, symbol.file_path):
            continue
        rows.append(symbol)
    rows.sort(key=lambda symbol: (symbol.file_path, getattr(symbol, "start_line", 0) or 0, symbol.name))
    return rows


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
        if _hidden_review_file(_subject(claim)):
            continue
        evidence, href = _claim_location(claim, evidence_by_id, repo, sha)
        push(_subject(claim), _sentence(claim), evidence, href)
    for symbol in symbols:
        if not getattr(symbol, "changed", False) or not getattr(symbol, "file_path", None):
            continue
        name = getattr(symbol, "name", None)
        if _hidden_review_file(symbol.file_path) or is_dunder_name(name):
            continue
        if is_private_python_name(name, symbol.file_path):
            continue
        path = symbol.file_path
        start = getattr(symbol, "start_line", None)
        end = getattr(symbol, "end_line", None)
        reason = f"{name} changed" if getattr(symbol, "kind", None) == "function" else "changed file"
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
        if _hidden_review_file(symbol.file_path) or is_dunder_name(symbol.name):
            continue
        if is_private_python_name(symbol.name, symbol.file_path):
            continue
        value = _location(symbol.file_path, getattr(symbol, "start_line", None), getattr(symbol, "end_line", None))
        push(symbol.name, value, _blob(repo, sha, symbol.file_path, getattr(symbol, "start_line", None), getattr(symbol, "end_line", None)))
        named.add(symbol.name)
    for claim in claims:
        if _kind(claim) != "symbol_changed":
            continue
        if _hidden_review_file(_claim_file(claim, evidence_by_id)):
            continue
        subject = _subject(claim)
        if is_dunder_name(subject) or is_private_python_name(subject, _claim_file(claim, evidence_by_id)):
            continue
        if subject in named:
            continue
        location, href = _claim_location(claim, evidence_by_id, repo, sha)
        push(subject or "Symbol", location or "changed", href)
    for claim in claims:
        if _kind(claim) != "file_changed" or not _subject(claim):
            continue
        if _hidden_review_file(_subject(claim)):
            continue
        location, href = _claim_location(claim, evidence_by_id, repo, sha)
        push(_subject(claim), location or "changed file", href)
    return rows or [_row("Changed", "none found", None)]


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
    return _sentence(claim)


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


def _location(file: str | None, start, end) -> str:
    if file and start and end and end != start:
        return f"{file}:{start}-{end}"
    if file and start:
        return f"{file}:{start}"
    return file or "changed"


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
