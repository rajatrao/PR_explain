"""Review signals: facts a reviewer would otherwise dig up by hand after reading the diff.

All read from the head snapshot, the compare patch, and the edges analysis already built:

* ``call_context``         for each call into a changed function: whether the call sits inside
                           a try block in its caller, and whether the caller checks the result
                           for None / null / falsy right after the call,
* ``resolution_coverage``  how many call sites in the changed files resolved to exactly one
                           function, so "no other consumers" can be said only when it is true,
* ``stale_test``           a test that exercises a changed function but is not in the diff,
* ``config_undocumented``  an environment variable newly read by the code that is not listed in
                           any env example or compose file in the repository.
"""

from __future__ import annotations

import posixpath
import re

from app.analyzer.parse import is_test_path
from app.analyzer.surface import find_surfaces

_PY_TRY = re.compile(r"^\s*try\s*:")
_PY_BOUNDARY = re.compile(r"^\s*(?:async\s+)?def\s|^\s*class\s")
_BRACE_TRY = re.compile(r"\btry\s*\{")
_ASSIGNED = re.compile(r"^\s*(?:(?:const|let|var|final|val)\s+)?([A-Za-z_]\w*)\s*(?::[^=]+)?(?::=|=)(?!=)")
_ENV_DOC_FILES = (".env.example", ".env.sample", ".env.template", ".env.dist")


def review_signals(
    snapshot,
    *,
    functions,
    resolved_calls,
    unresolved_sites,
    relationships,
    changed_ids: set[str],
    change_lines: dict,
    add_claim,
    add_evidence,
    slug,
) -> None:
    by_id = {symbol.id: symbol for symbol in functions}
    _call_context(snapshot, resolved_calls, changed_ids, add_claim, add_evidence, slug)
    _coverage(resolved_calls, unresolved_sites, change_lines, add_claim)
    _stale_tests(relationships, changed_ids, change_lines, by_id, add_claim, slug)
    _undocumented_config(snapshot, add_claim, add_evidence, slug)


def _call_context(snapshot, resolved_calls, changed_ids, add_claim, add_evidence, slug) -> None:
    seen: set[tuple[str, int]] = set()
    for site, callee, caller in resolved_calls:
        if callee.id not in changed_ids or is_test_path(site.file_path):
            continue
        key = (site.file_path, site.line)
        if key in seen:
            continue
        seen.add(key)
        text = snapshot.files.get(site.file_path)
        if not text:
            continue
        lines = text.splitlines()
        start = caller.start_line if caller else 1
        handled = _inside_try(lines, site.line, start, site.file_path)
        checks = _checks_result(lines, site.line)
        caller_name = caller.name if caller else site.file_path
        parts = [f"{caller_name} calls {callee.name}"]
        parts.append("inside a try block" if handled else "without a surrounding try block")
        if checks:
            parts.append(f"and checks the result with `{checks}`")
        evidence = add_evidence(
            id=f"ev_ctx_{slug(site.file_path)}_{site.line}",
            type="source_span",
            repo=snapshot.repository,
            commit_sha=snapshot.head_sha,
            file=site.file_path,
            start_line=site.line,
            end_line=site.line,
            symbol=callee.name,
            description=f"context|{caller_name}|{callee.name}|{'handled' if handled else 'unhandled'}|{checks or ''}",
            snippet=lines[site.line - 1].strip()[:160] if 0 < site.line <= len(lines) else None,
        )
        add_claim(
            id=f"cl_ctx_{slug(site.file_path)}_{site.line}",
            epistemic="FACT",
            kind="call_context",
            text=" ".join(parts) + ".",
            subject=callee.name,
            evidence_ids=[evidence.id],
        )


def _inside_try(lines: list[str], line_no: int, start: int, path: str) -> bool:
    if not (0 < line_no <= len(lines)):
        return False
    if path.endswith(".py"):
        current = _indent(lines[line_no - 1])
        for index in range(line_no - 2, max(start - 2, -1), -1):
            row = lines[index]
            if not row.strip() or row.strip().startswith("#"):
                continue
            indent = _indent(row)
            if indent < current:
                if _PY_TRY.match(row):
                    return True
                if _PY_BOUNDARY.match(row):
                    return False
                current = indent
        return False
    depth = 0
    for index in range(line_no - 2, max(start - 2, -1), -1):
        row = lines[index]
        depth += row.count("}") - row.count("{")
        if depth < 0:
            if _BRACE_TRY.search(row):
                return True
            depth = 0
    return False


def _checks_result(lines: list[str], line_no: int) -> str | None:
    if not (0 < line_no <= len(lines)):
        return None
    match = _ASSIGNED.match(lines[line_no - 1])
    if not match:
        return None
    name = re.escape(match.group(1))
    pattern = re.compile(
        rf"\bif\s*\(?\s*(?:not\s+{name}\b|!\s*{name}\b|{name}\s+is\s+(?:not\s+)?None\b|{name}\s*[!=]==?\s*(?:null|undefined|None|nil)\b)"
    )
    for row in lines[line_no : line_no + 4]:
        found = pattern.search(row)
        if found:
            return " ".join(found.group(0).split())
    return None


def _indent(row: str) -> int:
    expanded = row.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def _coverage(resolved_calls, unresolved_sites, change_lines, add_claim) -> None:
    changed_files = {path for path in change_lines if not is_test_path(path)}
    resolved = sum(1 for site, _c, _r in resolved_calls if site.file_path in changed_files)
    unresolved = sum(1 for site in unresolved_sites if site.file_path in changed_files)
    total = resolved + unresolved
    if not total:
        return
    add_claim(
        id="cl_resolution_coverage",
        epistemic="FACT",
        kind="resolution_coverage",
        text=f"{resolved} of {total} calls to repository functions in the changed files resolved to a single function.",
        subject=f"coverage:{resolved}/{total}",
        evidence_ids=[],
    )


def _stale_tests(relationships, changed_ids, change_lines, by_id, add_claim, slug) -> None:
    seen: set[tuple[str, str]] = set()
    for rel in relationships:
        if rel.type != "TESTS" or rel.target_id not in changed_ids:
            continue
        source = rel.source_file or ""
        if not source or source in change_lines:
            continue
        key = (source, rel.target_name)
        if key in seen:
            continue
        seen.add(key)
        add_claim(
            id=f"cl_stale_{slug(source)}_{slug(rel.target_name)}",
            epistemic="FACT",
            kind="stale_test",
            text=f"{source} exercises {rel.target_name}, which changed, and the test is not changed in this pull request.",
            subject=rel.target_name,
            evidence_ids=[rel.evidence_id] if rel.evidence_id else [],
        )


def _undocumented_config(snapshot, add_claim, add_evidence, slug) -> None:
    documented = "\n".join(
        text
        for path, text in snapshot.files.items()
        if posixpath.basename(path).lower() in _ENV_DOC_FILES
        or posixpath.basename(path).lower().startswith(("docker-compose", "compose."))
    )
    for finding in find_surfaces(snapshot.changes, skip_path=is_test_path):
        if finding.level != "config" or finding.kind != "added" or "read by the code" not in finding.detail:
            continue
        if re.search(rf"\b{re.escape(finding.name)}\b", documented):
            continue
        evidence = add_evidence(
            id=f"ev_config_doc_{slug(finding.name)}",
            type="diff_hunk",
            repo=snapshot.repository,
            commit_sha=snapshot.head_sha,
            file=finding.file_path,
            start_line=finding.line,
            end_line=finding.line,
            symbol=None,
            description=f"config|undocumented|{finding.name}",
            snippet=finding.snippet[:160] or None,
        )
        add_claim(
            id=f"cl_config_doc_{slug(finding.name)}",
            epistemic="FACT",
            kind="config_undocumented",
            text=f"{finding.name} is newly read by the code and is not listed in any env example or compose file.",
            subject=finding.name,
            evidence_ids=[evidence.id],
        )
