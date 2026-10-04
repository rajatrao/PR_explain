"""Facts for Explain behavioral and system-impact text.

Every field is taken from stored symbols, relationships, claims, or an
optional unified diff. Nothing here invents a caller, a test, or a blast radius.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.analyzer.parse import is_test_path
from app.explanation.explain_view import (
    explain_skip_symbol,
    hidden_explain_names,
    text_mentions_test_path,
    without_hidden_symbols,
)

_MAX_LINES = 3
_LINE_LIMIT = 100
_SIG = re.compile(
    r"(?:async\s+)?(?:function|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>()]*>)?\s*\(([^)]*)\)"
)
_RETURN = re.compile(r"^return\s+(.+)$")
_ASSIGN = re.compile(r"^(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")
_CALLS_CLAIM = re.compile(r"^(\S+)\s+calls\s+(\S+?)\.?$")
_FILE_REASON = re.compile(
    r"^(\S+)\s+is not in the diff and matters because\s+(\S+)\s+calls\s+(\S+?)\.?$"
)
_REACHED = re.compile(r"changed symbol\s+(.+?)(?:,\s+so its behavior|\.|$)")
_DEP_CHANGES = re.compile(r"\bchanges\s+(.+?)\.")
_PATHISH = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
_BARE_FILE = re.compile(r"(?<![A-Za-z0-9_/])[A-Za-z0-9_.-]+\.[A-Za-z0-9]+")

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


@dataclass(frozen=True)
class FnFact:
    name: str
    path: str
    exported: bool
    old_params: tuple[str, ...] | None
    new_params: tuple[str, ...] | None
    context_params: tuple[str, ...] | None
    removed_lines: tuple[str, ...]
    added_lines: tuple[str, ...]
    removed_extra: int
    added_extra: int


@dataclass(frozen=True)
class CallerFact:
    name: str
    path: str
    target: str
    outside: bool


@dataclass(frozen=True)
class ImportFact:
    path: str
    target: str
    outside: bool


@dataclass(frozen=True)
class FileEdit:
    path: str
    removed_lines: tuple[str, ...]
    added_lines: tuple[str, ...]
    removed_extra: int
    added_extra: int


@dataclass(frozen=True)
class ChangeFacts:
    functions: tuple[FnFact, ...]
    callers: tuple[CallerFact, ...]
    imports: tuple[ImportFact, ...]
    reaches: tuple[tuple[str, tuple[str, ...], bool], ...]
    missing_tests: tuple[tuple[str, str], ...]
    tested: tuple[tuple[str, str], ...]
    dependencies: tuple[str, ...]
    dependency_files: tuple[str, ...]
    config_files: tuple[str, ...]
    changed_files: tuple[str, ...]
    other_files: tuple[FileEdit, ...]
    hidden: frozenset[str]


def collect_change_facts(*, symbols, relationships, claims, patches=None) -> ChangeFacts:
    hidden = frozenset(hidden_explain_names(symbols))
    paths = _function_paths(symbols)
    changed_files = _changed_files(symbols, claims)
    functions = _functions(symbols, claims, paths, hidden, patches or {})
    changed_names = {item.name for item in functions}
    callers = _callers(relationships, claims, changed_names, changed_files, paths, hidden)
    call_files = {(_norm(item.path), item.target) for item in callers if item.path}
    imports = _imports(relationships, changed_names, changed_files, call_files, hidden)
    reaches = _reaches(claims, changed_names, changed_files, call_files, imports, hidden)
    interesting = changed_names | {item.name for item in callers}
    tested = _tested(claims, interesting, paths, hidden)
    tested_names = {name for name, _path in tested}
    missing = _missing(claims, interesting, paths, hidden, tested_names)
    dependencies, dependency_files = _dependencies(symbols, claims)
    config_files = tuple(path for path in changed_files if _is_config(path))
    function_files = {_norm(item.path) for item in functions}
    other_files = _other_file_edits(changed_files, function_files, patches or {}, hidden)
    return ChangeFacts(
        functions=functions,
        callers=callers,
        imports=imports,
        reaches=reaches,
        missing_tests=missing,
        tested=tested,
        dependencies=dependencies,
        dependency_files=dependency_files,
        config_files=config_files,
        changed_files=changed_files,
        other_files=other_files,
        hidden=hidden,
    )


def code(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    if "`" in cleaned:
        return f"`` {cleaned} ``"
    return f"`{cleaned}`"


def join_phrases(items, conj: str = "and") -> str:
    values = [item for item in items if item]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} {conj} {values[1]}"
    return ", ".join(values[:-1]) + f", {conj} {values[-1]}"


def guard(text: str, hidden: frozenset[str] | set[str]) -> str:
    cleaned = without_hidden_symbols(text, set(hidden)) or ""
    cleaned = _PATHISH.sub(lambda match: _drop_test_token(match.group(0)), cleaned)
    cleaned = _BARE_FILE.sub(lambda match: _drop_test_token(match.group(0)), cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    return cleaned.strip(" ,;")


def added_params(fn: FnFact) -> tuple[str, ...]:
    if fn.new_params is None:
        return ()
    previous = fn.old_params if fn.old_params is not None else fn.context_params
    if previous is None:
        return fn.new_params
    return tuple(name for name in fn.new_params if name not in previous)


def removed_params(fn: FnFact) -> tuple[str, ...]:
    if fn.old_params is None:
        return ()
    current = fn.new_params if fn.new_params is not None else fn.context_params
    if current is None:
        return ()
    return tuple(name for name in fn.old_params if name not in current)


def body_clauses(lines: tuple[str, ...], *, past: bool, extra: int) -> list[str]:
    clauses: list[str] = []
    for line in lines:
        assigned = _ASSIGN.match(line)
        returned = _RETURN.match(line)
        if assigned:
            verb = "set" if past else "sets"
            clauses.append(f"{verb} {code(assigned.group(1))} to {code(_strip_semi(assigned.group(2)))}")
        elif returned:
            verb = "returned" if past else "returns"
            clauses.append(f"{verb} {code(_strip_semi(returned.group(1)))}")
        else:
            label = "removed" if past else "added"
            clauses.append(f"{label} {code(line)}")
    if extra > 0:
        clauses.append(f"{extra} more changed lines are in the diff")
    return clauses


def _functions(symbols, claims, paths, hidden, patches) -> tuple[FnFact, ...]:
    exported = {
        _subject(claim)
        for claim in claims or []
        if _kind(claim) == "defines_api" and _subject(claim)
    }
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for symbol in symbols or []:
        if getattr(symbol, "kind", None) != "function" or not getattr(symbol, "changed", False):
            continue
        name = getattr(symbol, "name", None) or ""
        path = getattr(symbol, "file_path", None) or ""
        if not _visible_name(name, path, hidden):
            continue
        if name not in seen:
            seen.add(name)
            ordered.append((name, path))
    if not ordered:
        for claim in claims or []:
            if _kind(claim) != "symbol_changed":
                continue
            name = _subject(claim)
            path = paths.get(name) or _path_in_text(_text(claim)) or ""
            if not name or not _visible_name(name, path, hidden):
                continue
            if name not in seen:
                seen.add(name)
                ordered.append((name, path))
    edits = _symbol_edits(patches, {name for name, _path in ordered}, hidden)
    facts: list[FnFact] = []
    for name, path in ordered:
        symbol_exported = name in exported or _symbol_exported(symbols, name)
        removed, added, old_params, new_params, context_params, removed_extra, added_extra = edits.get(
            name,
            ((), (), None, None, None, 0, 0),
        )
        facts.append(
            FnFact(
                name=name,
                path=path,
                exported=symbol_exported,
                old_params=old_params,
                new_params=new_params,
                context_params=context_params,
                removed_lines=removed,
                added_lines=added,
                removed_extra=removed_extra,
                added_extra=added_extra,
            )
        )
    return tuple(facts)


def _symbol_edits(patches: dict, names: set[str], hidden: frozenset[str]):
    found: dict[str, tuple] = {}
    if not names or not patches:
        return found
    buckets: dict[str, dict] = {
        name: {
            "removed": [],
            "added": [],
            "old_params": None,
            "new_params": None,
            "context_params": None,
        }
        for name in names
    }
    for patch in patches.values():
        if not isinstance(patch, str) or not patch.strip():
            continue
        current: str | None = None
        for raw in patch.splitlines():
            if raw.startswith(("@@", "diff ", "---", "+++")):
                current = None
                continue
            if not raw:
                continue
            prefix = raw[0]
            if prefix not in "+- ":
                continue
            body = raw[1:].strip()
            if not body or _leaks(body, hidden) or text_mentions_test_path(body):
                continue
            signature = _SIG.search(body)
            if signature and signature.group(1) in names:
                current = signature.group(1)
                params = _param_names(signature.group(2))
                slot = {"-": "old_params", "+": "new_params", " ": "context_params"}[prefix]
                if buckets[current][slot] is None:
                    buckets[current][slot] = params
                continue
            if body.startswith(("import ", "from ", "export {", "export type")):
                continue
            if current is None or prefix == " " or _skippable_line(body):
                continue
            key = "removed" if prefix == "-" else "added"
            buckets[current][key].append(_short(body))
    for name, bucket in buckets.items():
        removed, removed_extra = _cap(bucket["removed"])
        added, added_extra = _cap(bucket["added"])
        if (
            not removed
            and not added
            and bucket["old_params"] is None
            and bucket["new_params"] is None
            and bucket["context_params"] is None
        ):
            continue
        found[name] = (
            removed,
            added,
            bucket["old_params"],
            bucket["new_params"],
            bucket["context_params"],
            removed_extra,
            added_extra,
        )
    return found


def _callers(relationships, claims, changed_names, changed_files, paths, hidden) -> tuple[CallerFact, ...]:
    if not changed_names:
        return ()
    found: list[CallerFact] = []
    seen: set[tuple[str, str, str]] = set()

    def add(name: str, path: str, target: str) -> None:
        if target not in changed_names or not name or _looks_like_path(name):
            return
        if not _visible_name(name, path, hidden):
            return
        if path and (_is_test(path) or text_mentions_test_path(path)):
            return
        key = (name, _norm(path), target)
        if key in seen:
            return
        seen.add(key)
        outside = bool(path) and _norm(path) not in changed_files
        found.append(CallerFact(name=name, path=path or "", target=target, outside=outside))

    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        target = getattr(rel, "target_name", None) or ""
        source = getattr(rel, "source_name", None) or ""
        source_file = getattr(rel, "source_file", None) or paths.get(source) or ""
        add(source, source_file, target)
    for claim in claims or []:
        kind = _kind(claim)
        text = _text(claim).strip()
        if kind == "calls":
            match = _CALLS_CLAIM.match(text)
            if match:
                caller, target = match.group(1), match.group(2)
                add(caller, paths.get(caller, ""), target)
        elif kind == "file_reason":
            match = _FILE_REASON.match(text)
            if match and not _is_test(match.group(1)):
                add(match.group(2), match.group(1), match.group(3))
    return tuple(found)


def _imports(relationships, changed_names, changed_files, call_files, hidden) -> tuple[ImportFact, ...]:
    if not changed_names:
        return ()
    found: list[ImportFact] = []
    seen: set[tuple[str, str]] = set()
    for rel in relationships or []:
        if getattr(rel, "type", None) != "IMPORTS":
            continue
        target = getattr(rel, "target_name", None) or ""
        path = getattr(rel, "source_file", None) or ""
        if target not in changed_names or not path or _is_test(path) or text_mentions_test_path(path):
            continue
        if explain_skip_symbol(target, None) or target in hidden:
            continue
        key = (_norm(path), target)
        if key in seen or key in call_files:
            continue
        seen.add(key)
        outside = _norm(path) not in changed_files
        found.append(ImportFact(path=path, target=target, outside=outside))
    return tuple(found)


def _reaches(claims, changed_names, changed_files, call_files, imports, hidden) -> tuple[tuple[str, tuple[str, ...], bool], ...]:
    covered = set(call_files) | {(_norm(item.path), item.target) for item in imports}
    found: list[tuple[str, tuple[str, ...], bool]] = []
    seen: set[str] = set()
    for claim in claims or []:
        if _kind(claim) != "reaches_changed":
            continue
        path = _subject(claim)
        if not path or _is_test(path) or text_mentions_test_path(path) or text_mentions_test_path(_text(claim)):
            continue
        if _norm(path) in seen:
            continue
        names = _reached_names(_text(claim), changed_names, hidden)
        if not names:
            continue
        if all((_norm(path), name) in covered for name in names):
            continue
        seen.add(_norm(path))
        found.append((path, names, _norm(path) not in changed_files))
    return tuple(found)


def _tested(claims, interesting, paths, hidden) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for claim in claims or []:
        if _kind(claim) != "tests":
            continue
        name = _subject(claim)
        path = paths.get(name, "")
        if not name or name not in interesting or name in seen or not _visible_name(name, path, hidden):
            continue
        seen.add(name)
        found.append((name, path))
    return tuple(found)


def _missing(claims, interesting, paths, hidden, tested_names) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for claim in claims or []:
        if _kind(claim) != "missing_test":
            continue
        name = _subject(claim)
        path = paths.get(name, "")
        if (
            not name
            or name in tested_names
            or name in seen
            or name not in interesting
            or not _visible_name(name, path, hidden)
        ):
            continue
        seen.add(name)
        found.append((name, path))
    return tuple(found)


def _dependencies(symbols, claims) -> tuple[tuple[str, ...], tuple[str, ...]]:
    names: list[str] = []
    files: list[str] = []
    for symbol in symbols or []:
        if getattr(symbol, "kind", None) != "dependency":
            continue
        name = getattr(symbol, "name", None) or ""
        path = getattr(symbol, "file_path", None) or ""
        if name and name not in names and name not in {"name", "version", "private", "dependencies"}:
            names.append(name)
        if path and path not in files:
            files.append(path)
    for claim in claims or []:
        if _kind(claim) not in {"dependency_changed", "dependency"}:
            continue
        subject = _subject(claim)
        if subject and subject not in files and not _is_test(subject):
            files.append(subject)
        match = _DEP_CHANGES.search(_text(claim))
        if not match:
            continue
        for part in match.group(1).split(","):
            label = part.strip()
            if label and label not in names and label != "dependencies":
                names.append(label)
    return tuple(names), tuple(files)


def _other_file_edits(changed_files, function_files, patches, hidden) -> tuple[FileEdit, ...]:
    edits: list[FileEdit] = []
    for path in changed_files:
        if _norm(path) in function_files:
            continue
        patch = _patch_for(patches, path)
        removed: list[str] = []
        added: list[str] = []
        if patch:
            for raw in patch.splitlines():
                if not raw or raw[0] not in "+-" or raw.startswith(("+++", "---")):
                    continue
                body = raw[1:].strip()
                if not body or _skippable_line(body) or _leaks(body, hidden) or text_mentions_test_path(body):
                    continue
                if raw[0] == "-":
                    removed.append(_short(body))
                else:
                    added.append(_short(body))
        removed_kept, removed_extra = _cap(removed)
        added_kept, added_extra = _cap(added)
        if removed_kept or added_kept or _is_config(path) or _is_dependency_file(path):
            edits.append(
                FileEdit(
                    path=path,
                    removed_lines=removed_kept,
                    added_lines=added_kept,
                    removed_extra=removed_extra,
                    added_extra=added_extra,
                )
            )
    return tuple(edits)


def _changed_files(symbols, claims) -> tuple[str, ...]:
    files: list[str] = []
    seen: set[str] = set()

    def add(path: str | None) -> None:
        if not path or _is_test(path) or text_mentions_test_path(path):
            return
        key = _norm(path)
        if key in seen:
            return
        seen.add(key)
        files.append(path)

    for symbol in symbols or []:
        if getattr(symbol, "changed", False):
            add(getattr(symbol, "file_path", None))
    for claim in claims or []:
        if _kind(claim) == "file_changed":
            add(_subject(claim))
    return tuple(files)


def _function_paths(symbols) -> dict[str, str]:
    found: dict[str, str] = {}
    for symbol in symbols or []:
        if getattr(symbol, "kind", None) != "function":
            continue
        name = getattr(symbol, "name", None)
        path = getattr(symbol, "file_path", None)
        if name and path and name not in found:
            found[name] = path
    return found


def _symbol_exported(symbols, name: str) -> bool:
    for symbol in symbols or []:
        if getattr(symbol, "kind", None) == "function" and getattr(symbol, "name", None) == name:
            return bool(getattr(symbol, "exported", False))
    return False


def _reached_names(text: str, changed_names: set[str], hidden: frozenset[str]) -> tuple[str, ...]:
    match = _REACHED.search(text or "")
    if not match:
        return ()
    names = []
    for part in match.group(1).split(","):
        name = part.strip().rstrip(".")
        if not name or name in hidden or explain_skip_symbol(name, None):
            continue
        if changed_names and name not in changed_names:
            continue
        if name not in names:
            names.append(name)
    return tuple(names)


def _visible_name(name: str, path: str, hidden: frozenset[str]) -> bool:
    if not name or name in hidden or explain_skip_symbol(name, path or None):
        return False
    if path and (_is_test(path) or text_mentions_test_path(path)):
        return False
    return True


def _param_names(raw: str) -> tuple[str, ...]:
    names: list[str] = []
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        piece = piece.split("=")[0].strip()
        piece = piece.split(":")[0].strip().lstrip("*")
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", piece)
        if not match:
            continue
        name = match.group(0)
        if name in {"self", "cls"}:
            continue
        names.append(name)
    return tuple(names)


def _short(line: str) -> str:
    if len(line) <= _LINE_LIMIT:
        return line
    return line[: _LINE_LIMIT - 1].rstrip() + "…"


def _cap(lines: list[str]) -> tuple[tuple[str, ...], int]:
    if len(lines) <= _MAX_LINES:
        return tuple(lines), 0
    return tuple(lines[:_MAX_LINES]), len(lines) - _MAX_LINES


def _skippable_line(body: str) -> bool:
    if body in {"{", "}", "});", ")", "]", "[", "};", ");"}:
        return True
    if body.startswith(("//", "#", "/*", "*", "*/")):
        return True
    return False


def _leaks(text: str, hidden: frozenset[str]) -> bool:
    for name in hidden:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text):
            return True
    return False


def _strip_semi(text: str) -> str:
    cleaned = text.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    return cleaned


def _looks_like_path(name: str) -> bool:
    if "/" in name or "\\" in name:
        return True
    return name.endswith((".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".java", ".json"))


def _is_test(path: str) -> bool:
    return bool(path) and (is_test_path(path) or text_mentions_test_path(path))


def _is_config(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    base = lowered.rsplit("/", 1)[-1]
    return (
        base in _CONFIG_NAMES
        or base.startswith("tsconfig.")
        or base.startswith(".env")
        or lowered.endswith("/config.py")
        or base == "config.py"
    )


def _is_dependency_file(path: str) -> bool:
    base = path.replace("\\", "/").lower().rsplit("/", 1)[-1]
    return base in _DEPENDENCY_NAMES


def _patch_for(patches: dict, path: str) -> str:
    if path in patches and isinstance(patches[path], str):
        return patches[path]
    norm = _norm(path)
    for key, value in patches.items():
        if isinstance(key, str) and _norm(key) == norm and isinstance(value, str):
            return value
    return ""


def _path_in_text(text: str) -> str:
    match = re.search(r"\bin ([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)", text or "")
    if not match:
        return ""
    path = match.group(1)
    if _is_test(path):
        return ""
    return path


def _drop_test_token(token: str) -> str:
    if is_test_path(token) or text_mentions_test_path(token):
        return ""
    return token


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/")


def _kind(claim) -> str:
    return getattr(claim, "kind", "") or ""


def _subject(claim) -> str:
    return getattr(claim, "subject", None) or ""


def _text(claim) -> str:
    return getattr(claim, "text", None) or ""
