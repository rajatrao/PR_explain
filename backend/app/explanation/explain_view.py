"""Filters for the Explain tab, Explain comment, and related bullets."""

from __future__ import annotations

import re

from app.analyzer.parse import is_dunder_name, is_private_python_name, is_test_path


def function_file_by_name(symbols) -> dict[str, str]:
    found: dict[str, str] = {}
    for symbol in symbols or []:
        if getattr(symbol, "kind", None) != "function":
            continue
        name = getattr(symbol, "name", None)
        path = getattr(symbol, "file_path", None)
        if name and path and name not in found:
            found[name] = path
    return found


def explain_skip_symbol(name: str | None, file_path: str | None) -> bool:
    return is_dunder_name(name) or is_private_python_name(name, file_path)


_PRIVATE_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(_[A-Za-z]\w*)(?![A-Za-z0-9_])")


def _python_path_hint(text: str | None, fallback: str | None = None) -> str | None:
    if fallback:
        return fallback
    if not text:
        return None
    match = re.search(r"([A-Za-z0-9_./-]+\.py)", text.replace("\\", "/"))
    return match.group(1) if match else None


def private_python_names_in_text(text: str | None, *, path_hint: str | None = None) -> set[str]:
    if not text:
        return set()
    hint = _python_path_hint(text, path_hint)
    found: set[str] = set()
    for name in _PRIVATE_TOKEN.findall(text):
        if is_dunder_name(name):
            continue
        if is_private_python_name(name, hint):
            found.add(name)
    return found


def private_python_hidden_names(symbols, claims=None, *, claim_file=None) -> set[str]:
    """Leading-underscore Python helpers referenced in stored symbols or claims."""
    paths = function_file_by_name(symbols)
    hidden: set[str] = set()
    for name, path in paths.items():
        if is_private_python_name(name, path):
            hidden.add(name)
    for claim in claims or []:
        subject = getattr(claim, "subject", None)
        text = getattr(claim, "text", None) or ""
        path = paths.get(subject) if subject else None
        if not path and claim_file is not None:
            path = claim_file(claim)
        if subject and is_private_python_name(subject, path):
            hidden.add(subject)
        hidden.update(private_python_names_in_text(text, path_hint=path))
    return hidden


def hidden_explain_names(symbols) -> set[str]:
    """Python private helpers, and names whose only function definitions are in test files."""
    private: set[str] = set()
    public: set[str] = set()
    only_test: dict[str, bool] = {}
    for symbol in symbols or []:
        if getattr(symbol, "kind", None) != "function":
            continue
        name = getattr(symbol, "name", None)
        path = getattr(symbol, "file_path", None) or ""
        if not name:
            continue
        if is_private_python_name(name, path):
            private.add(name)
        if text_mentions_test_path(path) or is_test_path(path):
            only_test.setdefault(name, True)
            continue
        only_test[name] = False
        if not is_private_python_name(name, path) and not is_dunder_name(name):
            public.add(name)
    test_only = {name for name, flag in only_test.items() if flag}
    return (private - public) | (test_only - public)


_REACHED_SYMBOLS = re.compile(r"changed symbol (.+?), so its behavior")


def without_hidden_symbols(text: str, hidden: set[str]) -> str | None:
    """Drop private and test-only names from a stored sentence. Empty means omit the line."""
    if not text:
        return None
    if not hidden:
        return text
    match = _REACHED_SYMBOLS.search(text)
    if match:
        parts = [part.strip() for part in match.group(1).split(",") if part.strip()]
        kept = [part for part in parts if part not in hidden]
        if not kept:
            return None
        text = text[: match.start(1)] + ", ".join(kept) + text[match.end(1) :]
    for name in sorted(hidden, key=len, reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)
    text = text.strip(" ,")
    return text or None


def text_mentions_test_path(text: str | None) -> bool:
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


def detail_is_test_path(detail: str | None) -> bool:
    if not detail:
        return False
    path = detail.split(":", 1)[0]
    return text_mentions_test_path(path) or is_test_path(path)


def text_mentions_hidden_symbol(text: str | None, symbols) -> bool:
    if not text:
        return False
    for symbol in symbols or []:
        if getattr(symbol, "kind", None) != "function":
            continue
        name = getattr(symbol, "name", None)
        path = getattr(symbol, "file_path", None)
        if name and explain_skip_symbol(name, path) and name in text:
            return True
    return False
