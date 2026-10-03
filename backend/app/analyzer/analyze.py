from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Callable

from app.analyzer.diff import added_lines, overlaps
from app.analyzer.parse import (
    CallSite,
    ImportBinding,
    is_code_path,
    parse_file,
)
from app.analyzer.types import (
    AnalysisResult,
    Claim,
    Evidence,
    FileChange,
    Relationship,
    Snapshot,
    Symbol,
)

SKIP_DIRS = {"node_modules", "dist", "build", "coverage", ".git"}
SNIPPET_CAP = 160
_DEP_LINE = re.compile(r'^\+\s*"([^"]+)":\s*"([^"]+)"')


def analyze(
    snapshot: Snapshot,
    *,
    fanout_cap: int = 50,
    max_changed_symbols: int = 80,
    on_stage: Callable[[str, str, str, dict | None], None] | None = None,
) -> AnalysisResult:
    evidences: list[Evidence] = []
    claims: list[Claim] = []
    relationships: list[Relationship] = []
    notes: list[str] = []
    evidence_ids: set[str] = set()
    claim_ids: set[str] = set()

    def add_evidence(**kwargs) -> Evidence:
        base = kwargs.pop("id")
        eid = base
        n = 2
        while eid in evidence_ids:
            eid = f"{base}_{n}"
            n += 1
        evidence_ids.add(eid)
        item = Evidence(id=eid, **kwargs)
        evidences.append(item)
        return item

    def add_claim(**kwargs) -> Claim:
        base = kwargs.pop("id")
        cid = base
        n = 2
        while cid in claim_ids:
            cid = f"{base}_{n}"
            n += 1
        claim_ids.add(cid)
        item = Claim(id=cid, **kwargs)
        claims.append(item)
        return item

    def add_rel(**kwargs) -> Relationship:
        relationships.append(Relationship(**kwargs))
        return relationships[-1]

    change_lines = _change_lines(snapshot.changes)
    _emit(
        on_stage,
        "diff_analysis",
        "succeeded",
        "Read the compare diff",
        {"change_count": len(snapshot.changes), "changed_file_count": len(change_lines)},
    )

    scoped, file_only = _scope_files(snapshot)
    symbols: list[Symbol] = []
    imports: list[ImportBinding] = []
    calls: list[CallSite] = []
    file_symbols: dict[str, Symbol] = {}

    for path in scoped:
        file_symbol = Symbol(
            id=f"sym_file_{_slug(path)}",
            name=posixpath.basename(path),
            kind="file",
            file_path=path,
            start_line=1,
            end_line=max(1, len(snapshot.files[path].splitlines())),
            exported=False,
        )
        file_symbols[path] = file_symbol
        symbols.append(file_symbol)
        parsed_symbols, parsed_imports, parsed_calls = parse_file(path, snapshot.files[path])
        for symbol in parsed_symbols:
            symbols.append(symbol)
            add_rel(
                id=f"rel_contains_{_slug(path)}_{_slug(symbol.name)}_{symbol.start_line}",
                type="FILE_CONTAINS_SYMBOL",
                source_id=file_symbol.id,
                target_id=symbol.id,
                source_name=path,
                target_name=symbol.name,
                source_file=path,
                target_file=path,
            )
        imports.extend(parsed_imports)
        calls.extend(parsed_calls)

    _emit(
        on_stage,
        "symbol_analysis",
        "succeeded",
        "Parsed symbols from the snapshot",
        {"symbol_count": len(symbols), "file_count": len(scoped)},
    )

    language_coverage = "ts" if scoped else "diff_only"
    by_id = {symbol.id: symbol for symbol in symbols}
    functions = [symbol for symbol in symbols if symbol.kind == "function"]
    by_name: dict[str, list[Symbol]] = {}
    for symbol in functions:
        by_name.setdefault(symbol.name, []).append(symbol)

    module_index = _module_index(snapshot.files)

    for binding in imports:
        target_file = _resolve_module(binding.file_path, binding.module, module_index)
        if target_file is None:
            continue
        matches = [
            symbol
            for symbol in functions
            if symbol.file_path == target_file and symbol.name == binding.imported_name
        ]
        if binding.imported_name == "default":
            matches = [
                symbol
                for symbol in functions
                if symbol.file_path == target_file and symbol.exported
            ]
        if len(matches) != 1:
            continue
        target = matches[0]
        source = file_symbols.get(binding.file_path)
        evidence = add_evidence(
            id=f"ev_import_{_slug(binding.file_path)}_{_slug(binding.imported_name)}_{binding.line}",
            type="source_span",
            repo=snapshot.repository,
            commit_sha=snapshot.head_sha,
            file=binding.file_path,
            start_line=binding.line,
            end_line=binding.line,
            symbol=binding.imported_name,
            description=(
                f"{binding.file_path} imports {binding.imported_name} from {binding.module}."
            ),
            snippet=_snippet(snapshot.files, binding.file_path, binding.line, binding.line),
        )
        add_rel(
            id=f"rel_imports_{_slug(binding.file_path)}_{target.id}",
            type="IMPORTS",
            source_id=source.id if source else None,
            target_id=target.id,
            source_name=binding.file_path,
            target_name=target.name,
            source_file=binding.file_path,
            target_file=target.file_path,
            evidence_id=evidence.id,
        )

    resolved_calls: list[tuple[CallSite, Symbol, Symbol | None]] = []
    for site in calls:
        if site.member:
            candidates = by_name.get(site.callee, [])
            if len(candidates) == 1:
                add_claim(
                    id=f"cl_ambiguous_{_slug(site.file_path)}_{_slug(site.callee)}_{site.line}",
                    epistemic="UNKNOWN",
                    kind="ambiguous_call",
                    text=(
                        f"Call to {site.callee} at {site.file_path}:{site.line} is a member "
                        "call and was not resolved to a function edge."
                    ),
                    subject=site.callee,
                    evidence_ids=[],
                )
            continue
        candidates = by_name.get(site.callee, [])
        if len(candidates) == 0:
            continue
        if len(candidates) > 1:
            evidence = add_evidence(
                id=f"ev_ambiguous_{_slug(site.file_path)}_{_slug(site.callee)}_{site.line}",
                type="source_span",
                repo=snapshot.repository,
                commit_sha=snapshot.head_sha,
                file=site.file_path,
                start_line=site.line,
                end_line=site.line,
                symbol=site.callee,
                description=(
                    f"The name {site.callee} matches {len(candidates)} functions, so no call edge was created."
                ),
                snippet=_snippet(snapshot.files, site.file_path, site.line, site.line),
            )
            add_claim(
                id=f"cl_ambiguous_{_slug(site.file_path)}_{_slug(site.callee)}_{site.line}",
                epistemic="UNKNOWN",
                kind="ambiguous_call",
                text=(
                    f"Call to {site.callee} at {site.file_path}:{site.line} is ambiguous "
                    f"across {len(candidates)} definitions."
                ),
                subject=site.callee,
                evidence_ids=[evidence.id],
            )
            continue
        callee = candidates[0]
        caller = _enclosing_function(functions, site.file_path, site.line)
        resolved_calls.append((site, callee, caller))

    callers_of: dict[str, list[tuple[CallSite, Symbol | None]]] = {}
    for site, callee, caller in resolved_calls:
        callers_of.setdefault(callee.id, []).append((site, caller))

    kept_pairs: set[tuple[str, str]] = set()
    for callee_id, sites in callers_of.items():
        callee = by_id[callee_id]
        ordered = sorted(sites, key=lambda item: (item[0].file_path, item[0].line, item[0].callee))
        omitted = 0
        if len(ordered) > fanout_cap:
            omitted = len(ordered) - fanout_cap
            ordered = ordered[:fanout_cap]
        for site, caller in ordered:
            caller_id = caller.id if caller else file_symbols[site.file_path].id
            caller_name = caller.name if caller else site.file_path
            pair = (caller_id, callee.id)
            if pair in kept_pairs:
                continue
            kept_pairs.add(pair)
            evidence = add_evidence(
                id=f"ev_call_{_slug(site.file_path)}_{_slug(caller_name)}_{_slug(callee.name)}_{site.line}",
                type="source_span",
                repo=snapshot.repository,
                commit_sha=snapshot.head_sha,
                file=site.file_path,
                start_line=site.line,
                end_line=site.line,
                symbol=callee.name,
                description=f"{caller_name} calls {callee.name} in {site.file_path}.",
                snippet=_snippet(snapshot.files, site.file_path, site.line, site.line),
            )
            add_rel(
                id=f"rel_calls_{_slug(caller_name)}_{callee.id}_{site.line}",
                type="CALLS",
                source_id=caller_id,
                target_id=callee.id,
                source_name=caller_name,
                target_name=callee.name,
                source_file=site.file_path,
                target_file=callee.file_path,
                evidence_id=evidence.id,
            )
        if omitted:
            note = f"{omitted} further callers of {callee.name} omitted."
            notes.append(note)
            add_claim(
                id=f"cl_fanout_{callee.id}",
                epistemic="UNKNOWN",
                kind="fanout_truncated",
                text=note,
                subject=callee.name,
                evidence_ids=[],
            )

    _emit(
        on_stage,
        "change_graph",
        "succeeded",
        "Built the change graph",
        {"relationship_count": len(relationships)},
    )

    changed_functions = []
    for symbol in functions:
        lines = change_lines.get(symbol.file_path)
        if symbol.file_path not in change_lines:
            continue
        if overlaps(symbol.start_line, symbol.end_line, lines):
            symbol.changed = True
            changed_functions.append(symbol)
    for path, file_symbol in file_symbols.items():
        if path in change_lines:
            file_symbol.changed = True

    selected_changed = _select_changed(changed_functions, change_lines, max_changed_symbols)
    if len(changed_functions) > len(selected_changed):
        omitted = len(changed_functions) - len(selected_changed)
        note = f"{omitted} changed symbols omitted from the detailed claim set."
        notes.append(note)
        add_claim(
            id="cl_changed_cap",
            epistemic="UNKNOWN",
            kind="changed_symbol_cap",
            text=note,
            subject=None,
            evidence_ids=[],
        )
    selected_ids = {symbol.id for symbol in selected_changed}

    for symbol in selected_changed:
        evidence = add_evidence(
            id=f"ev_changed_{symbol.id}",
            type="diff_hunk",
            repo=snapshot.repository,
            commit_sha=snapshot.head_sha,
            file=symbol.file_path,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            symbol=symbol.name,
            description=f"{symbol.name} overlaps the diff in {symbol.file_path}.",
            snippet=_snippet(snapshot.files, symbol.file_path, symbol.start_line, symbol.end_line),
        )
        file_symbol = file_symbols.get(symbol.file_path)
        add_rel(
            id=f"rel_changed_{symbol.id}",
            type="CHANGED_IN_PR",
            source_id=symbol.id,
            target_id=file_symbol.id if file_symbol else None,
            source_name=symbol.name,
            target_name=symbol.file_path,
            source_file=symbol.file_path,
            target_file=symbol.file_path,
            evidence_id=evidence.id,
        )
        add_claim(
            id=f"cl_changed_{symbol.id}",
            epistemic="FACT",
            kind="symbol_changed",
            text=f"{symbol.name} changed in {symbol.file_path}.",
            subject=symbol.name,
            evidence_ids=[evidence.id],
        )
        if symbol.exported:
            add_rel(
                id=f"rel_api_{symbol.id}",
                type="DEFINES_API",
                source_id=file_symbol.id if file_symbol else symbol.id,
                target_id=symbol.id,
                source_name=symbol.file_path,
                target_name=symbol.name,
                source_file=symbol.file_path,
                target_file=symbol.file_path,
                evidence_id=evidence.id,
            )
            add_claim(
                id=f"cl_api_{symbol.id}",
                epistemic="FACT",
                kind="defines_api",
                text=f"{symbol.name} is an exported API in {symbol.file_path}.",
                subject=symbol.name,
                evidence_ids=[evidence.id],
            )

    for path in sorted(change_lines):
        if path not in snapshot.files and not any(change.path == path for change in snapshot.changes):
            continue
        evidence = add_evidence(
            id=f"ev_file_changed_{_slug(path)}",
            type="diff_hunk",
            repo=snapshot.repository,
            commit_sha=snapshot.head_sha,
            file=path,
            start_line=min(change_lines[path]) if change_lines[path] else None,
            end_line=max(change_lines[path]) if change_lines[path] else None,
            symbol=None,
            description=f"{path} is part of the compare diff.",
            snippet=None,
        )
        add_claim(
            id=f"cl_file_changed_{_slug(path)}",
            epistemic="FACT",
            kind="file_changed",
            text=f"{path} is changed in this pull request.",
            subject=path,
            evidence_ids=[evidence.id],
        )

    for path in file_only:
        evidence = add_evidence(
            id=f"ev_file_only_{_slug(path)}",
            type="diff_hunk",
            repo=snapshot.repository,
            commit_sha=snapshot.head_sha,
            file=path,
            start_line=None,
            end_line=None,
            symbol=None,
            description=f"{path} is outside the TypeScript projects touched by this pull request.",
            snippet=None,
        )
        add_claim(
            id=f"cl_file_only_{_slug(path)}",
            epistemic="FACT",
            kind="file_changed",
            text=(
                f"{path} changed outside the TypeScript projects that contain a changed file. "
                "Only the file-level diff is included."
            ),
            subject=path,
            evidence_ids=[evidence.id],
        )
        notes.append(f"{path} contributed file-level diff facts only.")

    test_targets: set[str] = set()
    for rel in list(relationships):
        if rel.type != "IMPORTS":
            continue
        if not rel.source_file or not _is_test(rel.source_file):
            continue
        if not rel.target_id:
            continue
        _add_test_edge(
            add_rel,
            add_evidence,
            snapshot,
            source_file=rel.source_file,
            target=by_id[rel.target_id],
            line=_line_from_evidence(evidences, rel.evidence_id),
        )
        test_targets.add(rel.target_id)

    for site, callee, _caller in resolved_calls:
        if not _is_test(site.file_path):
            continue
        if callee.id in test_targets:
            continue
        _add_test_edge(
            add_rel,
            add_evidence,
            snapshot,
            source_file=site.file_path,
            target=callee,
            line=site.line,
        )
        test_targets.add(callee.id)

    for rel in [item for item in relationships if item.type == "TESTS"]:
        add_claim(
            id=f"cl_tests_{_slug(rel.source_file or '')}_{_slug(rel.target_name)}",
            epistemic="FACT",
            kind="tests",
            text=f"{rel.source_file} tests {rel.target_name}.",
            subject=rel.target_name,
            evidence_ids=[rel.evidence_id] if rel.evidence_id else [],
        )

    changed_id_set = {symbol.id for symbol in selected_changed}
    direct_callers = [
        rel
        for rel in relationships
        if rel.type == "CALLS" and rel.target_id in changed_id_set
    ]
    for rel in direct_callers:
        add_claim(
            id=f"cl_calls_{_slug(rel.source_name)}_{_slug(rel.target_name)}",
            epistemic="FACT",
            kind="calls",
            text=f"{rel.source_name} calls {rel.target_name}.",
            subject=rel.target_name,
            evidence_ids=[rel.evidence_id] if rel.evidence_id else [],
        )
        if rel.source_file and rel.source_file not in change_lines:
            add_claim(
                id=f"cl_reason_{_slug(rel.source_file)}_{_slug(rel.source_name)}",
                epistemic="FACT",
                kind="file_reason",
                text=(
                    f"{rel.source_file} is not in the diff and matters because "
                    f"{rel.source_name} calls {rel.target_name}."
                ),
                subject=rel.source_file,
                evidence_ids=[rel.evidence_id] if rel.evidence_id else [],
            )

    interesting: dict[str, Symbol] = {symbol.id: symbol for symbol in selected_changed}
    for rel in direct_callers:
        if rel.source_id and rel.source_id in by_id and by_id[rel.source_id].kind == "function":
            interesting[rel.source_id] = by_id[rel.source_id]
    for symbol in interesting.values():
        if symbol.id in test_targets:
            continue
        if _is_test(symbol.file_path):
            continue
        add_claim(
            id=f"cl_missing_test_{symbol.id}",
            epistemic="UNKNOWN",
            kind="missing_test",
            text=f"No test references {symbol.name}.",
            subject=symbol.name,
            evidence_ids=[],
        )

    _dependency_claims(snapshot, change_lines, add_claim, add_rel, add_evidence, symbols)

    if language_coverage == "diff_only":
        add_claim(
            id="cl_diff_only",
            epistemic="UNKNOWN",
            kind="diff_only",
            text=(
                "No TypeScript or JavaScript sources were analyzed for this revision; "
                "facts are limited to the diff."
            ),
            subject=None,
            evidence_ids=[],
        )

    _emit(
        on_stage,
        "evidence",
        "succeeded",
        "Collected evidence",
        {"evidence_count": len(evidences)},
    )

    adj: dict[str, list[str]] = {}
    for rel in relationships:
        if rel.type in {"CALLS", "IMPORTS"} and rel.source_id and rel.target_id:
            adj.setdefault(rel.source_id, []).append(rel.target_id)

    production_files = [
        path
        for path in file_symbols
        if path not in change_lines and not _is_test(path)
    ]
    for path in sorted(production_files):
        file_symbol = file_symbols[path]
        owned = [symbol.id for symbol in symbols if symbol.file_path == path]
        absent = add_evidence(
            id=f"ev_absent_{_slug(path)}",
            type="absent_file",
            repo=snapshot.repository,
            commit_sha=snapshot.head_sha,
            file=path,
            start_line=None,
            end_line=None,
            symbol=None,
            description=f"{path} does not appear in the compare diff.",
            snippet=None,
        )
        absent_claim = add_claim(
            id=f"cl_absent_{_slug(path)}",
            epistemic="FACT",
            kind="file_absent",
            text=f"{path} is absent from the diff.",
            subject=path,
            evidence_ids=[absent.id],
        )
        reached = _reached_changed(owned, changed_id_set, adj, by_id)
        if reached:
            names = ", ".join(sorted({item.name for item in reached}))
            add_claim(
                id=f"cl_reaches_{_slug(path)}",
                epistemic="FACT",
                kind="reaches_changed",
                text=(
                    f"{path} is absent from the diff, but a call or import path reaches "
                    f"changed symbol {names}, so its behavior is not unchanged."
                ),
                subject=path,
                evidence_ids=[absent.id],
                support_ids=[absent_claim.id],
            )
        else:
            add_claim(
                id=f"cl_unchanged_{_slug(path)}",
                epistemic="INFERENCE",
                kind="behavior_unchanged",
                text=(
                    f"{path} behavior is unchanged because no call or import path "
                    "reaches a changed symbol."
                ),
                subject=path,
                evidence_ids=[absent.id],
                support_ids=[absent_claim.id],
            )

    impact_count = sum(1 for claim in claims if claim.kind in {"reaches_changed", "behavior_unchanged"})
    _emit(
        on_stage,
        "impact",
        "succeeded",
        "Traced impact beyond the diff",
        {"impact_count": impact_count},
    )

    return AnalysisResult(
        language_coverage=language_coverage,
        symbols=symbols,
        relationships=relationships,
        evidences=evidences,
        claims=claims,
        context_notes=notes,
    )


def _emit(on_stage, stage: str, status: str, message: str, detail: dict | None = None) -> None:
    if on_stage is None:
        return
    try:
        on_stage(stage, status, message, detail)
    except Exception:
        return


def _scope_files(snapshot: Snapshot) -> tuple[list[str], list[str]]:
    code_files = [
        path
        for path in sorted(snapshot.files)
        if is_code_path(path) and not _skipped(path)
    ]
    tsconfigs = [path for path in snapshot.files if posixpath.basename(path) == "tsconfig.json"]
    changed_paths = {change.path for change in snapshot.changes if change.status != "removed"}
    if len(tsconfigs) <= 1:
        return code_files, []
    project_dirs = [posixpath.dirname(path) or "." for path in tsconfigs]

    def project_of(path: str) -> str | None:
        matches = [directory for directory in project_dirs if path == directory or path.startswith(directory + "/")]
        if not matches:
            return None
        return max(matches, key=len)

    changed_projects = {project_of(path) for path in changed_paths}
    changed_projects.discard(None)
    scoped: list[str] = []
    file_only: list[str] = []
    for path in code_files:
        project = project_of(path)
        if project in changed_projects:
            scoped.append(path)
        elif path in changed_paths:
            file_only.append(path)
    return scoped, file_only


def _skipped(path: str) -> bool:
    return any(part in SKIP_DIRS for part in path.split("/"))


def _is_test(path: str) -> bool:
    base = posixpath.basename(path)
    return ".test." in base or ".spec." in base


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug or "item"


def _snippet(files: dict[str, str], path: str | None, start: int | None, end: int | None) -> str | None:
    if not path or path not in files or start is None:
        return None
    rows = files[path].splitlines()
    stop = end or start
    chunk = "\n".join(rows[start - 1 : stop])
    if len(chunk) > SNIPPET_CAP:
        return chunk[: SNIPPET_CAP - 3] + "..."
    return chunk


def _module_index(files: dict[str, str]) -> set[str]:
    return set(files)


def _resolve_module(importer: str, module: str, files: set[str]) -> str | None:
    if not module.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), module))
    candidates = [
        base,
        f"{base}.ts",
        f"{base}.tsx",
        f"{base}.js",
        f"{base}.jsx",
        f"{base}/index.ts",
        f"{base}/index.tsx",
        f"{base}/index.js",
    ]
    for candidate in candidates:
        if candidate in files:
            return candidate
    return None


def _enclosing_function(functions: list[Symbol], path: str, line: int) -> Symbol | None:
    matches = [
        symbol
        for symbol in functions
        if symbol.file_path == path and symbol.start_line <= line <= symbol.end_line
    ]
    if not matches:
        return None
    return min(matches, key=lambda symbol: (symbol.end_line - symbol.start_line, symbol.start_line))


def _change_lines(changes: list[FileChange]) -> dict[str, set[int] | None]:
    result: dict[str, set[int] | None] = {}
    for change in changes:
        if change.status == "removed":
            result[change.path] = set()
            continue
        result[change.path] = added_lines(change.patch)
    return result


def _select_changed(
    changed: list[Symbol],
    change_lines: dict[str, set[int] | None],
    limit: int,
) -> list[Symbol]:
    if len(changed) <= limit:
        return list(changed)

    def rank(symbol: Symbol) -> tuple[int, int, str]:
        lines = change_lines.get(symbol.file_path)
        if not lines:
            overlap = symbol.end_line - symbol.start_line + 1
        else:
            overlap = sum(1 for line in lines if symbol.start_line <= line <= symbol.end_line)
        return (1 if symbol.exported else 0, overlap, symbol.id)

    ordered = sorted(changed, key=rank, reverse=True)
    return ordered[:limit]


def _line_from_evidence(evidences: list[Evidence], evidence_id: str | None) -> int:
    for evidence in evidences:
        if evidence.id == evidence_id and evidence.start_line:
            return evidence.start_line
    return 1


def _add_test_edge(add_rel, add_evidence, snapshot: Snapshot, *, source_file: str, target: Symbol, line: int) -> None:
    evidence = add_evidence(
        id=f"ev_test_{_slug(source_file)}_{_slug(target.name)}_{line}",
        type="source_span",
        repo=snapshot.repository,
        commit_sha=snapshot.head_sha,
        file=source_file,
        start_line=line,
        end_line=line,
        symbol=target.name,
        description=f"{source_file} references {target.name}.",
        snippet=_snippet(snapshot.files, source_file, line, line),
    )
    add_rel(
        id=f"rel_tests_{_slug(source_file)}_{target.id}",
        type="TESTS",
        source_id=f"sym_file_{_slug(source_file)}",
        target_id=target.id,
        source_name=source_file,
        target_name=target.name,
        source_file=source_file,
        target_file=target.file_path,
        evidence_id=evidence.id,
    )


def _reached_changed(
    start_ids: list[str],
    changed_ids: set[str],
    adj: dict[str, list[str]],
    by_id: dict[str, Symbol],
) -> list[Symbol]:
    starts = set(start_ids)
    seen = set(starts)
    stack = list(start_ids)
    found: list[Symbol] = []
    while stack:
        current = stack.pop()
        for nxt in adj.get(current, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            symbol = by_id.get(nxt)
            if symbol and symbol.id in changed_ids:
                found.append(symbol)
            stack.append(nxt)
    return found


def _dependency_claims(snapshot, change_lines, add_claim, add_rel, add_evidence, symbols) -> None:
    if "package.json" not in change_lines:
        return
    text = snapshot.files.get("package.json")
    patch = next((change.patch or "" for change in snapshot.changes if change.path == "package.json"), "")
    names = []
    for line in patch.splitlines():
        match = _DEP_LINE.match(line)
        if match and match.group(1) not in {"name", "version", "private"}:
            names.append(match.group(1))
    evidence = add_evidence(
        id="ev_package_json",
        type="diff_hunk",
        repo=snapshot.repository,
        commit_sha=snapshot.head_sha,
        file="package.json",
        start_line=None,
        end_line=None,
        symbol=None,
        description="package.json is in the compare diff.",
        snippet=None,
    )
    if not names and text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {}
        dep_names = list((parsed.get("dependencies") or {}).keys())
        if dep_names:
            names = dep_names
    label = ", ".join(names) if names else "dependencies"
    add_claim(
        id="cl_depends_package_json",
        epistemic="FACT",
        kind="dependency_changed",
        text=f"package.json changes {label}. No call graph was inferred from the manifest.",
        subject="package.json",
        evidence_ids=[evidence.id],
    )
    for name in names:
        dep_id = f"sym_dep_{_slug(name)}"
        symbols.append(
            Symbol(
                id=dep_id,
                name=name,
                kind="dependency",
                file_path="package.json",
                start_line=1,
                end_line=1,
                exported=False,
                changed=True,
            )
        )
        add_rel(
            id=f"rel_depends_{_slug(name)}",
            type="DEPENDS_ON",
            source_id=None,
            target_id=dep_id,
            source_name="package.json",
            target_name=name,
            source_file="package.json",
            target_file="package.json",
            evidence_id=evidence.id,
        )
