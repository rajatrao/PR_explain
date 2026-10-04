"""System impact summary for the Details tab (three labeled parts)."""

from __future__ import annotations

import re

from app.analyzer.parse import is_test_path
from app.explanation.explain_view import explain_skip_symbol, text_mentions_test_path

_CHECK_AREAS = ("API", "Database", "Frontend", "Auth", "Dependencies", "Configuration")
_NO_DIRECT = "No direct impact was identified in the available code."
_NO_REVIEW = "No additional review interaction was identified."
_PATH_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+(?![A-Za-z0-9_])")
_CONFIG_NAMES = {
    "tsconfig.json",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "alembic.ini",
}


def render_system_impact_markdown(rows: list[dict]) -> str:
    lines = ["### System Impact", ""]
    for row in rows:
        lines.append(f"- **{row['label']}** — {row['value']}")
    return "\n".join(lines)


def build_system_impact_rows(*, symbols, relationships, claims) -> list[dict]:
    changed = _production_changed_names(symbols, claims)
    rows = [
        _row("System Impact", _system_impact_text(symbols, relationships, claims, changed)),
        _row("Reviewer Considerations", _reviewer_text(symbols, relationships, claims, changed)),
        _row("Risk & Scope", _risk_scope_text(symbols, relationships, claims, changed)),
    ]
    return [_scrub_row(row, changed) for row in rows]


def _system_impact_text(symbols, relationships, claims, changed: set[str]) -> str:
    parts: list[str] = []
    api_changed = _has_public_api_change(claims, changed)
    outside = _outside_coupling(claims, symbols, relationships, changed)
    if api_changed and outside:
        parts.append("An exported API change is reached from call paths outside this pull request.")
    elif api_changed:
        parts.append("An exported API surface changed in this pull request.")
    elif outside:
        parts.append("Call paths outside this pull request reach updated application logic.")
    if _dependency_changed(claims):
        parts.append("Dependency metadata in this pull request changed.")
    if _configuration_changed(claims, symbols):
        parts.append("Configuration referenced by the application changed in this pull request.")
    if changed and _stored_tests_cover_changed(claims, changed):
        parts.append("Stored tests reference part of the changed application logic.")
    if parts:
        return " ".join(parts)
    if changed or any(_kind(c) == "file_changed" for c in claims or []):
        return _NO_DIRECT
    return _NO_DIRECT


def _reviewer_text(symbols, relationships, claims, changed: set[str]) -> str:
    parts: list[str] = []
    if _outside_coupling(claims, symbols, relationships, changed):
        parts.append("Verify behavior where code outside this diff reaches the updated logic.")
    if changed and _missing_test_for_changed(claims, changed):
        if _has_public_api_change(claims, changed):
            parts.append("Confirm automated coverage for the exported API change.")
        else:
            parts.append("Confirm automated coverage for the updated application logic.")
    if _shared_callers(symbols, relationships, changed):
        parts.append("Verify shared entry points that multiple callers use still behave consistently.")
    if _dependency_changed(claims):
        parts.append("Verify dependency and build settings still match how the application is run.")
    if parts:
        return " ".join(parts)
    return _NO_REVIEW


def _risk_scope_text(symbols, relationships, claims, changed: set[str]) -> str:
    observed: list[str] = []
    if _outside_coupling(claims, symbols, relationships, changed):
        observed.append("Scope includes call paths outside this diff that reach the change.")
    if _has_public_api_change(claims, changed):
        observed.append("Scope includes the exported API surface.")
    if changed and _missing_test_for_changed(claims, changed):
        observed.append("Stored facts do not show automated tests for part of the changed logic.")
    if _dependency_changed(claims):
        observed.append("Scope includes dependency metadata changes.")
    if _configuration_changed(claims, symbols):
        observed.append("Scope includes configuration changes.")

    coupled = _coupled_area_labels(claims, symbols, relationships, changed)
    unchecked = [area for area in _CHECK_AREAS if area not in coupled]
    no_id: list[str] = []
    if unchecked:
        labels = _join_area_labels(unchecked)
        no_id.append(f"No stored relationship ties the change to {labels}.")

    if observed:
        observed_text = "Observed impact: " + " ".join(observed)
    else:
        observed_text = f"Observed impact: {_NO_DIRECT}"
    if no_id:
        no_text = "No identified impact: " + " ".join(no_id)
    elif not observed:
        no_text = f"No identified impact: {_NO_DIRECT}"
    else:
        no_text = ""
    return observed_text if not no_text else f"{observed_text} {no_text}"


def _row(label: str, value: str) -> dict:
    return {"label": label, "value": value, "href": None}


def _production_changed_names(symbols, claims) -> set[str]:
    names: set[str] = set()
    for symbol in symbols or []:
        if getattr(symbol, "kind", None) != "function" or not getattr(symbol, "changed", False):
            continue
        name = getattr(symbol, "name", None)
        path = getattr(symbol, "file_path", None)
        if not name or not path or is_test_path(path) or explain_skip_symbol(name, path):
            continue
        names.add(name)
    if names:
        return names
    for claim in claims or []:
        if _kind(claim) != "symbol_changed":
            continue
        subject = _subject(claim)
        if subject and not explain_skip_symbol(subject, None):
            names.add(subject)
    return names


def _has_public_api_change(claims, changed: set[str]) -> bool:
    for claim in claims or []:
        if _kind(claim) != "defines_api":
            continue
        subject = _subject(claim)
        if subject and not explain_skip_symbol(subject, None):
            if not changed or subject in changed:
                return True
    return False


def _outside_coupling(claims, symbols, relationships, changed: set[str]) -> bool:
    if not changed:
        return False
    for claim in claims or []:
        if _kind(claim) not in {"reaches_changed", "file_reason"}:
            continue
        subject = _subject(claim)
        if not subject or is_test_path(subject) or text_mentions_test_path(subject):
            continue
        if text_mentions_test_path(_text(claim)):
            continue
        if _kind(claim) == "reaches_changed":
            return True
        if _kind(claim) == "file_reason" and _mentions_changed_logic(claim, changed):
            return True
    return _caller_outside_diff(symbols, relationships, claims, changed)


def _caller_outside_diff(symbols, relationships, claims, changed: set[str]) -> bool:
    changed_files = _changed_files(symbols, claims)
    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        target_name = getattr(rel, "target_name", None) or getattr(rel, "target", None) or ""
        source_file = getattr(rel, "source_file", None) or ""
        if target_name in changed and source_file and source_file not in changed_files:
            return True
    return False


def _shared_callers(symbols, relationships, changed: set[str]) -> bool:
    if not changed:
        return False
    counts: dict[str, int] = {}
    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        target = getattr(rel, "target_name", None) or ""
        if target not in changed:
            continue
        source_file = getattr(rel, "source_file", None) or ""
        if is_test_path(source_file):
            continue
        counts[target] = counts.get(target, 0) + 1
    return any(count >= 2 for count in counts.values())


def _dependency_changed(claims) -> bool:
    return any(_kind(claim) in {"dependency_changed", "dependency"} for claim in claims or [])


def _configuration_changed(claims, symbols) -> bool:
    for claim in claims or []:
        if _kind(claim) != "file_changed":
            continue
        path = _subject(claim)
        if path and _is_configuration_path(path):
            return True
    for symbol in symbols or []:
        path = getattr(symbol, "file_path", None)
        if getattr(symbol, "changed", False) and path and _is_configuration_path(path):
            return True
    return False


def _is_configuration_path(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    base = lowered.rsplit("/", 1)[-1]
    return (
        base in _CONFIG_NAMES
        or base.startswith("tsconfig.")
        or base.startswith(".env")
        or base == "docker-compose.yml"
        or base == "docker-compose.yaml"
        or lowered.endswith("/config.py")
        or base == "config.py"
    )


def _stored_tests_cover_changed(claims, changed: set[str]) -> bool:
    for claim in claims or []:
        if _kind(claim) != "tests":
            continue
        subject = _subject(claim)
        if subject and subject in changed:
            return True
    return False


def _missing_test_for_changed(claims, changed: set[str]) -> bool:
    for claim in claims or []:
        if _kind(claim) != "missing_test":
            continue
        subject = _subject(claim)
        if subject in changed and not explain_skip_symbol(subject, None):
            return True
    return False


def _coupled_area_labels(claims, symbols, relationships, changed: set[str]) -> set[str]:
    labels: set[str] = set()
    if _has_public_api_change(claims, changed):
        labels.add("API")
    if _dependency_changed(claims):
        labels.add("Dependencies")
    if _configuration_changed(claims, symbols):
        labels.add("Configuration")
    for claim in claims or []:
        if _kind(claim) == "tests" and not text_mentions_test_path(_subject(claim)):
            labels.add("Tests")
    for path in _changed_files(symbols, claims):
        area = _path_area_label(path)
        if area:
            labels.add(area)
    if _outside_coupling(claims, symbols, relationships, changed):
        labels.add("Backend")
    return labels


def _path_area_label(path: str) -> str | None:
    lowered = path.replace("\\", "/").lower()
    if "/migrations/" in f"/{lowered}" or "/db/" in f"/{lowered}" or lowered.endswith(".sql"):
        return "Database"
    if lowered.startswith("frontend/") or "/frontend/" in f"/{lowered}" or lowered.endswith((".tsx", ".css", ".scss", ".vue")):
        return "Frontend"
    auth_stems = {"auth", "oauth", "password", "session", "login"}
    stem = lowered.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if stem in auth_stems or any(part in auth_stems for part in lowered.split("/")):
        return "Auth"
    if lowered.startswith("backend/") or "/backend/" in f"/{lowered}" or lowered.endswith(".py"):
        return "Backend"
    return None


def _join_area_labels(areas: list[str]) -> str:
    readable = {"API": "the API", "Configuration": "configuration", "Tests": "tests"}
    names = [readable.get(area, area.lower()) for area in areas]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} or {names[1]}"
    return ", ".join(names[:-1]) + f", or {names[-1]}"


def _changed_files(symbols, claims) -> set[str]:
    files: set[str] = set()
    for symbol in symbols or []:
        path = getattr(symbol, "file_path", None)
        if getattr(symbol, "changed", False) and path and not is_test_path(path):
            files.add(path)
    for claim in claims or []:
        if _kind(claim) != "file_changed":
            continue
        path = _subject(claim)
        if path and not is_test_path(path):
            files.add(path)
    return files


def _mentions_changed_logic(claim, changed: set[str]) -> bool:
    text = _text(claim)
    for name in changed:
        if name and re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text):
            return True
    return False


def _scrub_row(row: dict, changed: set[str]) -> dict:
    value = _scrub_text(row.get("value") or "", changed)
    if value == row.get("value"):
        return row
    return {**row, "value": value}


def _scrub_text(text: str, changed: set[str]) -> str:
    if _PATH_TOKEN.search(text) or text_mentions_test_path(text):
        return _NO_DIRECT
    for name in sorted(changed, key=len, reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,")
    if " calls " in text.lower():
        return _NO_DIRECT
    return text or _NO_DIRECT


def _kind(claim) -> str:
    return getattr(claim, "kind", "") or ""


def _subject(claim) -> str:
    return getattr(claim, "subject", None) or ""


def _text(claim) -> str:
    return getattr(claim, "text", None) or ""
