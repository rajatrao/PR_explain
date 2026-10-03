"""Top-to-bottom change story from stored symbols and direct edges.

Changed functions come first. A line that says "calls" is a stored CALLS
edge. A caller whose file is outside the diff is listed under
"Reached from outside this diff". TESTS edges that name a changed symbol
are listed under "Tests". The mermaid diagram draws changed symbols,
stored CALLS edges that touch them, and stored IMPORTS edges into them.
Nothing is inferred.
"""

from __future__ import annotations

import re
from collections import Counter

from app.analyzer.parse import is_dunder_name, is_package_marker, is_test_path


def build_change_flow(symbols, relationships, evidences=None) -> dict:
    sections = _sections(symbols, relationships, evidences or [])
    return {
        "sections": sections,
        "text": render_change_flow(sections),
        "mermaid": render_mermaid(symbols, relationships),
    }


def render_change_flow(sections) -> str:
    blocks: list[str] = []
    for section in sections:
        lines = [section["heading"]]
        for item in section["items"]:
            lines.append(f"  {item['text']}")
            detail = item.get("detail")
            if detail:
                lines.append(f"    {detail}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_mermaid(symbols, relationships) -> str:
    """Flowchart of changed functions and the stored edges that touch them.

    A changed function is a node even when it has no stored call. A calls
    arrow is a CALLS row whose source or target is a changed function. An
    imports arrow is an IMPORTS row whose target is a changed function.
    Other rows are omitted. Nothing is inferred.
    """
    changed = _changed_functions(symbols)
    if not changed:
        return ""
    by_id = {}
    for symbol in symbols:
        symbol_id = _symbol_id(symbol)
        if symbol_id:
            by_id[symbol_id] = symbol
    changed_ids = {symbol_id for symbol_id in (_symbol_id(symbol) for symbol in changed) if symbol_id}
    nodes: dict[str, dict] = {}
    edges: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_node(node_id: str, symbol, fallback_name, fallback_file) -> None:
        kind = getattr(symbol, "kind", None) if symbol is not None else None
        file_path = getattr(symbol, "file_path", None) if symbol is not None else None
        name = getattr(symbol, "name", None) if symbol is not None else None
        if is_dunder_name(name) or is_dunder_name(fallback_name):
            return
        if kind == "file":
            label = file_path or fallback_file or fallback_name or node_id
        else:
            label = name or fallback_name or node_id
        current = nodes.get(node_id)
        if current is None:
            nodes[node_id] = {
                "label": label,
                "file": file_path or fallback_file or "",
                "changed": node_id in changed_ids,
            }
            return
        if node_id in changed_ids:
            current["changed"] = True

    for symbol in changed:
        if _hidden_file(getattr(symbol, "file_path", None)):
            continue
        symbol_id = _symbol_id(symbol)
        if symbol_id:
            add_node(symbol_id, symbol, symbol.name, symbol.file_path)

    for rel in relationships:
        if _hidden_file(getattr(rel, "source_file", None)) or _hidden_file(getattr(rel, "target_file", None)):
            continue
        if _hidden_name(getattr(rel, "source_name", None)) or _hidden_name(getattr(rel, "target_name", None)):
            continue
        rel_type = getattr(rel, "type", None)
        source = _rel_end(rel, "source")
        target = _rel_end(rel, "target")
        if rel_type == "CALLS":
            if not source or not target:
                continue
            if source not in changed_ids and target not in changed_ids:
                continue
            arrow = "calls"
        elif rel_type == "IMPORTS":
            if not target or target not in changed_ids:
                continue
            if not source:
                source_file = getattr(rel, "source_file", None) or getattr(rel, "source_name", None)
                if not source_file:
                    continue
                source = f"file:{source_file}"
            arrow = "imports"
        else:
            continue
        add_node(source, by_id.get(source), getattr(rel, "source_name", None), getattr(rel, "source_file", None))
        add_node(target, by_id.get(target), getattr(rel, "target_name", None), getattr(rel, "target_file", None))
        key = (source, target, arrow)
        if key in seen:
            continue
        seen.add(key)
        edges.append(key)

    counts = Counter(node["label"] for node in nodes.values())
    for node in nodes.values():
        file_path = node["file"]
        if counts[node["label"]] > 1 and file_path and file_path not in node["label"]:
            node["label"] = f"{node['label']} ({file_path})"

    order = sorted(
        nodes,
        key=lambda node_id: (
            not nodes[node_id]["changed"],
            nodes[node_id]["file"],
            nodes[node_id]["label"],
            node_id,
        ),
    )
    id_map = _mermaid_ids(order)
    lines = ["flowchart TD"]
    for node_id in order:
        lines.append(f'  {id_map[node_id]}["{_mermaid_text(nodes[node_id]["label"])}"]')
    for source, target, arrow in sorted(
        edges,
        key=lambda item: (item[2], nodes[item[0]]["label"], nodes[item[1]]["label"], item[0], item[1]),
    ):
        lines.append(f"  {id_map[source]} -->|{arrow}| {id_map[target]}")
    changed_refs = [id_map[node_id] for node_id in order if nodes[node_id]["changed"]]
    if changed_refs:
        lines.append("  classDef changed fill:#1e1a16,stroke:#1e1a16,color:#f3efe6")
        lines.append("  class " + ",".join(changed_refs) + " changed")
    return "\n".join(lines)


def _mermaid_ids(node_ids: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for raw in node_ids:
        base = re.sub(r"[^A-Za-z0-9_]", "_", raw) or "node"
        if not base[0].isalpha():
            base = "n" + base
        base = base[:48]
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        mapping[raw] = candidate
    return mapping


def _mermaid_text(label: str) -> str:
    text = " ".join(str(label).split())
    return (
        text.replace('"', "#quot;")
        .replace("[", "(")
        .replace("]", ")")
        .replace("{", "(")
        .replace("}", ")")
    )


def _sections(symbols, relationships, evidences) -> list[dict]:
    changed = _changed_functions(symbols)
    if not changed:
        return []
    changed_ids = {_symbol_id(symbol) for symbol in changed}
    changed_files = {
        symbol.file_path
        for symbol in symbols
        if getattr(symbol, "changed", False) and getattr(symbol, "file_path", None)
    }
    evidence_by_id = _evidence_map(evidences)
    calls = _call_edges(relationships, changed_ids, changed_files, evidence_by_id)

    sections = [
        {
            "heading": "Changed",
            "items": [{"text": symbol.name, "detail": None} for symbol in changed],
        }
    ]
    outside: list[dict] = []
    for symbol in changed:
        symbol_id = _symbol_id(symbol)
        outgoing = [edge for edge in calls if edge["source"] == symbol_id]
        incoming = [
            edge
            for edge in calls
            if edge["target"] == symbol_id and edge["source"] not in changed_ids and edge["in_diff"]
        ]
        items: list[dict] = []
        for edge in sorted(outgoing, key=lambda item: (item["target_name"], item["detail"] or "", item["source_name"])):
            items.append({"text": f"calls {edge['target_name']}", "detail": edge["detail"]})
        for edge in sorted(incoming, key=lambda item: (item["caller"], item["detail"] or "")):
            items.append({"text": f"{edge['caller']} calls {symbol.name}", "detail": edge["detail"]})
        if not items:
            items.append({"text": "no direct call found", "detail": None})
        sections.append({"heading": symbol.name, "items": items})
        for edge in calls:
            if edge["target"] == symbol_id and edge["source"] not in changed_ids and not edge["in_diff"]:
                outside.append(
                    {
                        "text": f"{edge['caller']} calls {symbol.name}",
                        "detail": edge["detail"],
                    }
                )
    if outside:
        sections.append({"heading": "Reached from outside this diff", "items": _dedupe_items(outside)})
    sections.append({"heading": "Tests", "items": _test_items(relationships, changed_ids, evidence_by_id)})
    return sections


def _hidden_file(path: str | None) -> bool:
    return bool(path) and (is_test_path(path) or is_package_marker(path))


def _hidden_name(name: str | None) -> bool:
    return is_dunder_name(name)


def _changed_functions(symbols) -> list:
    rows = []
    for symbol in symbols:
        if getattr(symbol, "kind", None) != "function" or not getattr(symbol, "changed", False):
            continue
        if _hidden_file(getattr(symbol, "file_path", None)) or _hidden_name(getattr(symbol, "name", None)):
            continue
        if not _symbol_id(symbol):
            continue
        rows.append(symbol)
    rows.sort(
        key=lambda symbol: (
            symbol.file_path or "",
            getattr(symbol, "start_line", 0) or 0,
            symbol.name,
            _symbol_id(symbol),
        )
    )
    return rows


def _call_edges(relationships, changed_ids: set[str], changed_files: set[str], evidence_by_id: dict) -> list[dict]:
    edges = []
    seen: set[tuple] = set()
    for rel in relationships:
        if getattr(rel, "type", None) != "CALLS":
            continue
        if _hidden_file(getattr(rel, "source_file", None)) or _hidden_file(getattr(rel, "target_file", None)):
            continue
        if _hidden_name(getattr(rel, "source_name", None)) or _hidden_name(getattr(rel, "target_name", None)):
            continue
        source = _rel_end(rel, "source")
        target = _rel_end(rel, "target")
        if not source or not target:
            continue
        if source not in changed_ids and target not in changed_ids:
            continue
        evidence = evidence_by_id.get(_evidence_id(rel) or "")
        detail = _location(evidence)
        key = (source, target, detail)
        if key in seen:
            continue
        seen.add(key)
        source_file = getattr(rel, "source_file", None)
        edges.append(
            {
                "source": source,
                "target": target,
                "source_name": rel.source_name,
                "target_name": rel.target_name,
                "caller": _display_name(rel.source_name),
                "detail": detail,
                "in_diff": bool(source_file) and source_file in changed_files,
            }
        )
    return edges


def _test_items(relationships, changed_ids: set[str], evidence_by_id: dict) -> list[dict]:
    items = []
    seen: set[tuple] = set()
    for rel in relationships:
        if getattr(rel, "type", None) != "TESTS":
            continue
        target = _rel_end(rel, "target")
        if not target or target not in changed_ids:
            continue
        evidence = evidence_by_id.get(_evidence_id(rel) or "")
        detail = _location(evidence)
        name = _display_name(getattr(rel, "source_name", None) or getattr(rel, "source_file", None) or "test")
        key = (name, detail, rel.target_name)
        if key in seen:
            continue
        seen.add(key)
        items.append({"text": name, "detail": detail, "target": rel.target_name})
    if not items:
        return [{"text": "none found for these symbols", "detail": None}]
    items.sort(key=lambda item: (item["text"], item["detail"] or "", item["target"]))
    return [{"text": item["text"], "detail": item["detail"]} for item in items]


def _dedupe_items(items: list[dict]) -> list[dict]:
    kept: list[dict] = []
    seen: set[tuple] = set()
    for item in sorted(items, key=lambda row: (row["text"], row.get("detail") or "")):
        key = (item["text"], item.get("detail"))
        if key in seen:
            continue
        seen.add(key)
        kept.append({"text": item["text"], "detail": item.get("detail")})
    return kept


def _display_name(name: str) -> str:
    if name and "/" in name:
        return name.rstrip("/").rsplit("/", 1)[-1]
    return name


def _evidence_map(evidences) -> dict:
    found = {}
    for item in evidences:
        evidence_id = getattr(item, "public_id", None) or getattr(item, "id", None)
        if evidence_id:
            found[evidence_id] = item
    return found


def _location(evidence) -> str | None:
    if evidence is None:
        return None
    file = getattr(evidence, "file", None)
    start = getattr(evidence, "start_line", None)
    end = getattr(evidence, "end_line", None)
    if not file or not start:
        return None
    if end and end != start:
        return f"{file}:{start}-{end}"
    return f"{file}:{start}"


def _symbol_id(symbol) -> str | None:
    public_id = getattr(symbol, "public_id", None)
    if public_id:
        return public_id
    symbol_id = getattr(symbol, "id", None)
    return symbol_id or None


def _rel_end(rel, end: str) -> str | None:
    public_id = getattr(rel, f"{end}_public_id", None)
    if public_id:
        return public_id
    symbol_id = getattr(rel, f"{end}_id", None)
    return symbol_id or None


def _evidence_id(rel) -> str | None:
    public_id = getattr(rel, "evidence_public_id", None)
    if public_id:
        return public_id
    evidence_id = getattr(rel, "evidence_id", None)
    return evidence_id or None
