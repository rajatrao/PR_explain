"""Outcome-language behavioral summary for the Explain view."""

from __future__ import annotations

import re

from app.analyzer.parse import is_test_path
from app.explanation.explain_view import explain_skip_symbol, text_mentions_test_path

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

_NOT_ESTABLISHED = "Not established from this pull request."
_PATH_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+(?![A-Za-z0-9_])")


def build_behavioral_changes(*, symbols, relationships, claims) -> list[dict]:
    """Four labeled rows derived from stored facts only."""
    changed_names = _production_changed_names(symbols, claims)
    rows = [
        _row("Before", _before_text(claims, changed_names)),
        _row("Now", _now_text(claims, symbols, changed_names)),
        _row("Conditions", _conditions_text(claims, symbols, relationships, changed_names)),
        _row("What observers notice", _observers_text(claims, changed_names)),
    ]
    return [_scrub_row(row, changed_names) for row in rows]


def render_behavioral_changes_markdown(rows: list[dict]) -> str:
    lines = ["### Behavioral Changes", ""]
    for row in rows:
        lines.append(f"- **{row['label']}** — {row['value']}")
    return "\n".join(lines)


def _row(label: str, value: str) -> dict:
    return {"label": label, "value": value, "href": None}


def _before_text(claims, changed_names: set[str]) -> str:
    del changed_names
    for claim in claims or []:
        if _kind(claim) != "dependency_changed":
            continue
        text = (_text(claim) or "").casefold()
        if any(token in text for token in ("removed", "dropped", "no longer", "stopped")):
            cleaned = _outcome_from_fact(_text(claim))
            if cleaned:
                return cleaned
    return "The previous behavior is not established from this pull request."


def _now_text(claims, symbols, changed_names: set[str]) -> str:
    parts: list[str] = []
    if changed_names:
        parts.append("Application logic in this pull request was updated.")
    if _has_public_api_change(claims):
        parts.append("An exported API surface changed.")
    if _outside_reach_claims(claims):
        parts.append(
            "Logic outside the diff can still execute paths that reach the changed application code."
        )
    areas = _changed_area_labels(claims, symbols)
    if areas:
        parts.append(f"Changes touch { _join_phrases(areas) }.")
    if parts:
        return " ".join(parts)
    if any(_kind(claim) == "file_changed" for claim in claims or []):
        return (
            "Files in this pull request changed; the observable runtime effect is "
            "not established from stored facts."
        )
    return _NOT_ESTABLISHED


def _conditions_text(claims, symbols, relationships, changed_names: set[str]) -> str:
    parts: list[str] = []
    if _outside_reach_claims(claims):
        parts.append("When execution flows through code outside this diff into updated application logic.")
    if _caller_outside_diff(symbols, relationships, claims, changed_names):
        parts.append("When existing callers invoke the updated entry points.")
    if changed_names and not parts:
        parts.append("When the updated application paths run.")
    if parts:
        return " ".join(parts)
    return _NOT_ESTABLISHED


def _observers_text(claims, changed_names: set[str]) -> str:
    parts: list[str] = []
    if changed_names and any(
        _kind(claim) == "missing_test" and _subject(claim) in changed_names for claim in claims or []
    ):
        parts.append(
            "Reviewers and operators may lack stored automated checks for part of the changed application logic."
        )
    if _has_public_api_change(claims):
        parts.append("API consumers may see different responses or contracts.")
    if _outside_reach_claims(claims):
        parts.append("Systems that rely on unchanged files may still observe different runtime behavior.")
    if parts:
        return " ".join(parts)
    return (
        "The observable difference for users or downstream systems is not established "
        "from this pull request."
    )


def _has_public_api_change(claims) -> bool:
    for claim in claims or []:
        if _kind(claim) != "defines_api":
            continue
        subject = _subject(claim)
        if subject and not explain_skip_symbol(subject, None):
            return True
    return False


def _outside_reach_claims(claims) -> list:
    found = []
    for claim in claims or []:
        if _kind(claim) != "reaches_changed":
            continue
        subject = _subject(claim)
        if not subject or text_mentions_test_path(subject) or is_test_path(subject):
            continue
        if text_mentions_test_path(_text(claim)):
            continue
        found.append(claim)
    return found


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
        if path and not is_test_path(path) and not text_mentions_test_path(path):
            files.add(path)
    return files


def _caller_outside_diff(symbols, relationships, claims, changed_names: set[str]) -> bool:
    if not changed_names:
        return False
    changed_files = _changed_files(symbols, claims)
    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        target_name = getattr(rel, "target_name", None) or getattr(rel, "target", None) or ""
        source_file = getattr(rel, "source_file", None) or ""
        if target_name in changed_names and source_file and source_file not in changed_files:
            return True
    return False


def _changed_area_labels(claims, symbols) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()

    def note(label: str | None) -> None:
        if not label or label in seen:
            return
        seen.add(label)
        labels.append(label)

    for claim in claims or []:
        if _kind(claim) == "defines_api":
            note("the API")
        elif _kind(claim) in {"dependency_changed", "dependency"}:
            note("dependencies")
    for path in _changed_files(symbols, claims):
        note(_path_area_label(path))
    return labels


def _path_area_label(path: str) -> str | None:
    lowered = path.replace("\\", "/").lower()
    base = lowered.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    if base in _DEPENDENCY_NAMES:
        return "dependencies"
    if base in _CONFIG_NAMES or base.startswith("tsconfig.") or base.startswith(".env"):
        return "configuration"
    if "/migrations/" in f"/{lowered}" or "/db/" in f"/{lowered}" or base.endswith(".sql"):
        return "the database"
    if stem in _AUTH_STEMS or any(part in _AUTH_STEMS for part in lowered.split("/")):
        return "authentication"
    if lowered.startswith("frontend/") or "/frontend/" in f"/{lowered}" or base.endswith((".tsx", ".css", ".scss", ".vue")):
        return "the frontend"
    if lowered.startswith("backend/") or "/backend/" in f"/{lowered}" or base.endswith(".py"):
        return "the backend"
    return None


def _join_phrases(items: list[str]) -> str:
    unique: list[str] = []
    for item in items:
        if item and item not in unique:
            unique.append(item)
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    if len(unique) == 2:
        return f"{unique[0]} and {unique[1]}"
    return ", ".join(unique[:-1]) + f", and {unique[-1]}"


def _outcome_from_fact(text: str | None) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned or _PATH_TOKEN.search(cleaned):
        return ""
    if " calls " in cleaned.lower():
        return ""
    return cleaned.rstrip(".")


def _scrub_row(row: dict, changed_names: set[str]) -> dict:
    value = _scrub_outcome_text(row.get("value") or "", changed_names)
    if value == row.get("value"):
        return row
    return {**row, "value": value}


def _scrub_outcome_text(text: str, changed_names: set[str]) -> str:
    if not text:
        return text
    if _PATH_TOKEN.search(text):
        return _NOT_ESTABLISHED
    if text_mentions_test_path(text):
        return _NOT_ESTABLISHED
    banned = set(changed_names)
    for name in sorted(banned, key=len, reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,")
    if " calls " in text.lower():
        return _NOT_ESTABLISHED
    return text or _NOT_ESTABLISHED


def _kind(claim) -> str:
    return getattr(claim, "kind", "") or ""


def _subject(claim) -> str:
    return getattr(claim, "subject", None) or ""


def _text(claim) -> str:
    return getattr(claim, "text", None) or ""
