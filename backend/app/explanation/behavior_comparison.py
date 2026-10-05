"""Behavior facts grouped per changed function, plus who reaches them.

Built only from stored facts. ``behavior_changed`` claims carry the summary,
their ``behavior_before`` / ``behavior_after`` evidence carry the base and head
code, and stored CALLS / TESTS relationships say which callers, entry points,
and tests now run the new behavior.
"""

from __future__ import annotations

from app.analyzer.behavior import CATEGORY_LABEL, CATEGORY_ORDER
from app.analyzer.parse import is_test_path
from app.explanation.explain_view import explain_skip_symbol

REACH_DEPTH = 4
REACH_CAP = 12


def build_behavior_comparison(
    *,
    symbols,
    relationships,
    claims,
    evidences,
    repo: str | None = None,
    base_sha: str | None = None,
    head_sha: str | None = None,
) -> dict:
    evidence_by_id = {_id(item): item for item in evidences or []}
    functions = [item for item in symbols or [] if getattr(item, "kind", None) == "function"]
    by_id = {_id(item): item for item in functions}
    by_key = {(item.name, item.file_path): item for item in functions}
    changed_files = {
        item.file_path for item in symbols or [] if getattr(item, "changed", False) and getattr(item, "file_path", None)
    }
    callers = _callers_index(relationships)
    tests = _tests_index(relationships)

    groups: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for claim in claims or []:
        if getattr(claim, "kind", None) != "behavior_changed":
            continue
        before = after = None
        for evidence_id in _evidence_ids(claim):
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                continue
            if evidence.type == "behavior_before":
                before = evidence
            elif evidence.type == "behavior_after":
                after = evidence
        anchor = after or before
        if anchor is None or not anchor.file:
            continue
        path = anchor.file
        name = anchor.symbol
        category, _guard = _split_description(anchor.description)
        key = (path, name or "")
        if key not in groups:
            symbol = by_key.get((name, path)) if name else None
            groups[key] = _group(symbol, name, path, category, repo, head_sha)
            order.append(key)
        group = groups[key]
        group["changes"].append(
            {
                "claim_id": _id(claim),
                "category": category,
                "label": CATEGORY_LABEL.get(category, "Logic"),
                "before_when": _split_description(before.description)[1] if before else None,
                "after_when": _split_description(after.description)[1] if after else None,
                "summary": _summary_text(claim, name or path, path),
                "before": before.snippet if before else None,
                "after": after.snippet if after else None,
                "before_location": _location(before),
                "after_location": _location(after),
                "before_href": _href(repo, base_sha, before),
                "after_href": _href(repo, head_sha, after),
            }
        )

    items: list[dict] = []
    for key in order:
        group = groups[key]
        group["changes"].sort(key=lambda change: CATEGORY_ORDER.get(change["category"], 9))
        symbol = group.pop("_symbol")
        group["reach"] = _reach(symbol, by_id, callers, tests, changed_files) if symbol else _empty_reach()
        items.append(group)
    items.sort(key=lambda item: (-len(item["reach"]["callers"]), not item["exported"], item["file"], item["name"]))
    return {"summary": _overall_summary(items), "items": items}


# --- grouping ----------------------------------------------------------------


def _group(symbol, name: str | None, path: str, category: str, repo, head_sha) -> dict:
    private = bool(name) and explain_skip_symbol(name, path)
    if name is None:
        display = "module level"
    elif private:
        display = "internal helper"
    else:
        display = name
    line = getattr(symbol, "start_line", None) if symbol else None
    return {
        "name": name or "",
        "display_name": display,
        "file": path,
        "line": line,
        "location": f"{path}:{line}" if line else path,
        "href": f"https://github.com/{repo}/blob/{head_sha}/{path}#L{line}" if repo and head_sha and line else None,
        "exported": bool(getattr(symbol, "exported", False)) if symbol else False,
        "removed": category == "removed_function",
        "changes": [],
        "symbol_id": _id(symbol) if symbol is not None else None,
        "_symbol": symbol,
    }


def _summary_text(claim, owner: str, path: str) -> str:
    text = getattr(claim, "text", "") or ""
    prefix = f"{owner} in {path}: "
    return text[len(prefix) :] if text.startswith(prefix) else text


def _location(evidence) -> str | None:
    if evidence is None or not evidence.file:
        return None
    return f"{evidence.file}:{evidence.start_line}" if evidence.start_line else evidence.file


def _href(repo, sha, evidence) -> str | None:
    if not repo or not sha or evidence is None or not evidence.file:
        return None
    anchor = f"#L{evidence.start_line}" if evidence.start_line else ""
    return f"https://github.com/{repo}/blob/{sha}/{evidence.file}{anchor}"


# --- reach ---------------------------------------------------------------------


def _callers_index(relationships) -> dict[str, list]:
    index: dict[str, list] = {}
    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        target = _rel_target(rel)
        if target:
            index.setdefault(target, []).append(rel)
    return index


def _tests_index(relationships) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for rel in relationships or []:
        if getattr(rel, "type", None) != "TESTS":
            continue
        target = _rel_target(rel)
        source = getattr(rel, "source_file", None) or getattr(rel, "source_name", None)
        if target and source and source not in index.setdefault(target, []):
            index[target].append(source)
    return index


def _reach(symbol, by_id, callers, tests, changed_files) -> dict:
    """Walk CALLS edges upstream from the changed function, nearest callers first."""
    start = _id(symbol)
    seen = {start}
    frontier = [(start, symbol.name)]
    found: list[dict] = []
    truncated = False
    for depth in range(1, REACH_DEPTH + 1):
        nxt = []
        for current, current_name in frontier:
            for rel in callers.get(current, []):
                source_id = _rel_source(rel)
                source_file = getattr(rel, "source_file", None) or ""
                if not source_id or source_id in seen or is_test_path(source_file):
                    continue
                seen.add(source_id)
                caller = by_id.get(source_id)
                name = caller.name if caller else (getattr(rel, "source_name", None) or source_file)
                private = caller is not None and explain_skip_symbol(caller.name, caller.file_path)
                if len(found) >= REACH_CAP:
                    truncated = True
                    continue
                upstream = [r for r in callers.get(source_id, []) if not is_test_path(getattr(r, "source_file", "") or "")]
                found.append(
                    {
                        "name": name,
                        "file": source_file,
                        "depth": depth,
                        "via": current_name,
                        "outside_diff": source_file not in changed_files,
                        "entry_point": caller is None or bool(caller.exported) or not upstream,
                        "private": private,
                    }
                )
                if caller is not None:
                    nxt.append((source_id, name))
        frontier = nxt
        if not frontier:
            break
    return {
        "callers": found,
        # Private helpers are never reported as entry points of the system.
        "entry_points": [item["name"] for item in found if item["entry_point"] and not item["private"]],
        "files": sorted({item["file"] for item in found if item["file"]}),
        "outside_diff": sum(1 for item in found if item["outside_diff"]),
        "tests": tests.get(start, []),
        "truncated": truncated,
    }


def _empty_reach() -> dict:
    return {"callers": [], "entry_points": [], "files": [], "outside_diff": 0, "tests": [], "truncated": False}


def _overall_summary(items: list[dict]) -> str:
    if not items:
        return ""
    changes = sum(len(item["changes"]) for item in items)
    callers = {(c["name"], c["file"]) for item in items for c in item["reach"]["callers"]}
    outside = {(c["name"], c["file"]) for item in items for c in item["reach"]["callers"] if c["outside_diff"]}
    entries = {(c["name"], c["file"]) for item in items for c in item["reach"]["callers"] if c["entry_point"]}
    files = {f for item in items for f in item["reach"]["files"]}
    untested = sum(1 for item in items if not item["reach"]["tests"] and not item["removed"])
    text = (
        f"{changes} behavior change{'s' if changes != 1 else ''} across {len(items)} "
        f"function{'s' if len(items) != 1 else ''}."
    )
    if callers:
        text += (
            f" The new behavior reaches {len(callers)} caller{'s' if len(callers) != 1 else ''} in "
            f"{len(files)} file{'s' if len(files) != 1 else ''}"
            f" ({len(outside)} outside this diff, {len(entries)} entry point{'s' if len(entries) != 1 else ''})."
        )
    else:
        text += " No stored call path reaches the changed functions from elsewhere in the repository."
    if untested:
        text += f" {untested} changed function{'s have' if untested != 1 else ' has'} no stored test reference."
    return text


# --- row/dataclass access ------------------------------------------------------


def _id(item) -> str:
    return getattr(item, "public_id", None) or getattr(item, "id", None)


def _evidence_ids(claim) -> list[str]:
    ids = getattr(claim, "evidence_public_ids", None)
    if ids is None:
        ids = getattr(claim, "evidence_ids", None)
    return list(ids or [])


def _rel_target(rel) -> str | None:
    return getattr(rel, "target_public_id", None) or getattr(rel, "target_id", None)


def _rel_source(rel) -> str | None:
    return getattr(rel, "source_public_id", None) or getattr(rel, "source_id", None)


def _split_description(description: str | None) -> tuple[str, str | None]:
    """Behavior evidence stores `category` or `category\\nwhen: <condition>`."""
    text = description or "logic"
    head, _, rest = text.partition("\n")
    guard = rest[len("when: "):].strip() if rest.startswith("when: ") else None
    return head.strip() or "logic", guard or None
