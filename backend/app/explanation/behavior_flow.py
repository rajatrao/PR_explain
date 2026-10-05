"""Old flow and new flow diagrams for the Explain tab.

Both diagrams are drawn from stored facts only:

* the changed functions that have ``behavior_changed`` claims,
* their callers up to the entry points (stored CALLS edges at the head commit),
* their callees: head CALLS edges, adjusted per side with the calls that the
  base and head statements of the diff name (a call only on ``-`` lines is an
  old-only edge, a call only on ``+`` lines is a new-only edge),
* changed outcomes (raise/throw, return, branch) read from the same statements.

Old-only parts are red in the old diagram and new-only parts are green in the
new diagram, so a reviewer can compare the two flows side by side.
"""

from __future__ import annotations

import re

from app.analyzer.behavior import call_names

ITEM_CAP = 6
CALLER_CAP = 4
CALLEE_CAP = 6
OUTCOME_CAP = 3
LABEL_CAP = 48
_OUTCOMES = ("error", "return", "condition")


def build_behavior_flows(comparison: dict, *, symbols, relationships) -> dict:
    items = [item for item in (comparison.get("items") or []) if item.get("changes")][:ITEM_CAP]
    if not items:
        return {"before": "", "after": "", "legend": ""}
    functions = {
        (getattr(s, "name", None), getattr(s, "file_path", None)): s
        for s in symbols or []
        if getattr(s, "kind", None) == "function"
    }
    by_id = {_id(s): s for s in symbols or [] if getattr(s, "kind", None) == "function"}
    callees_of: dict[str, list[str]] = {}
    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        source = _rel_source(rel)
        target = by_id.get(_rel_target(rel))
        name = getattr(target, "name", None) or getattr(rel, "target_name", None)
        if source and name and name not in callees_of.setdefault(source, []):
            callees_of[source].append(name)

    before = _Diagram()
    after = _Diagram()
    for index, item in enumerate(items):
        symbol = functions.get((item["name"], item["file"]))
        _draw_item(index, item, symbol, callees_of, before, after)
    return {
        "before": before.render("Old flow (base)", side="before"),
        "after": after.render("New flow (head)", side="after"),
        "legend": (
            "Red marks calls, errors, returns and branches that appear only on removed (-) lines of the diff. "
            "Green marks those that appear only on added (+) lines. Callers and unchanged callees are stored "
            "call edges at the head commit."
        ),
    }


def render_behavior_flows_markdown(flows: dict) -> str:
    if not flows.get("before") and not flows.get("after"):
        return ""
    parts = ["### Old flow vs New flow", "", flows.get("legend") or ""]
    for title, key in (("Old flow (base)", "before"), ("New flow (head)", "after")):
        chart = (flows.get(key) or "").replace("```", "'''")
        if not chart:
            continue
        parts.extend(["", f"**{title}**", "", "```mermaid", chart, "```"])
    return "\n".join(parts).strip()


def _draw_item(index: int, item: dict, symbol, callees_of, before: "_Diagram", after: "_Diagram") -> None:
    changes = item["changes"]
    focus = f"f{index}"
    signature = next((c for c in changes if c["category"] == "signature"), None)
    name = item["display_name"]
    before_label = _header_label(signature["before"], name, item["file"]) if signature and signature["before"] else name
    after_label = _header_label(signature["after"], name, item["file"]) if signature and signature["after"] else name

    if item.get("removed"):
        before.node(focus, before_label, "gone")
        return
    before.node(focus, before_label, "focus")
    after.node(focus, after_label, "focus")

    # Upstream chain: every stored caller up to the entry points, each edge pointing at what it calls.
    callers = item["reach"]["callers"][:CALLER_CAP * 2]
    node_of: dict[tuple[int, str], str] = {}
    for caller in callers:
        node_of[(caller["depth"], caller["name"])] = "c_" + _slug(f"{caller['file']}_{caller['name']}")
    for caller in callers:
        caller_id = node_of[(caller["depth"], caller["name"])]
        target = focus if caller["depth"] == 1 else node_of.get((caller["depth"] - 1, caller["via"]))
        if target is None:
            continue
        css = "entry" if caller.get("entry_point") else "caller"
        for diagram in (before, after):
            diagram.node(caller_id, caller["name"], css)
            diagram.edge(caller_id, target, "-->", None)

    old_calls: list[str] = []
    new_calls: list[str] = []
    for change in changes:
        if change["category"] == "signature":
            continue
        for call in call_names(change["before"] or ""):
            if call not in old_calls:
                old_calls.append(call)
        for call in call_names(change["after"] or ""):
            if call not in new_calls:
                new_calls.append(call)
    old_short = {_short(c) for c in old_calls}
    new_short = {_short(c) for c in new_calls}
    gained = [c for c in new_calls if _short(c) not in old_short]
    lost = [c for c in old_calls if _short(c) not in new_short]
    gained_short = {_short(c) for c in gained}

    head = list(callees_of.get(_id(symbol), [])) if symbol is not None else []
    after_callees = _unique(head + new_calls)
    before_callees = _unique([c for c in head if _short(c) not in gained_short] + old_calls)
    for callee in before_callees[:CALLEE_CAP]:
        node_id = "k_" + _slug(_short(callee))
        is_lost = _short(callee) in {_short(c) for c in lost}
        before.node(node_id, callee, "gone" if is_lost else "callee")
        before.edge(focus, node_id, "-->", "gone" if is_lost else None)
    for callee in after_callees[:CALLEE_CAP]:
        node_id = "k_" + _slug(_short(callee))
        is_new = _short(callee) in gained_short
        after.node(node_id, callee, "new" if is_new else "callee")
        after.edge(focus, node_id, "-->", "new" if is_new else None)

    outcomes = [c for c in changes if c["category"] in _OUTCOMES][:OUTCOME_CAP]
    for n, change in enumerate(outcomes):
        shape = "decision" if change["category"] == "condition" else "outcome"
        if change["before"]:
            node_id = f"o{index}_{n}_b"
            before.node(node_id, change["before"], "gone", shape=shape)
            before.edge(focus, node_id, "-.->", "gone")
        if change["after"]:
            node_id = f"o{index}_{n}_a"
            after.node(node_id, change["after"], "new", shape=shape)
            after.edge(focus, node_id, "-.->", "new")


class _Diagram:
    def __init__(self) -> None:
        self.nodes: dict[str, tuple[str, str, str]] = {}
        self.edges: list[tuple[str, str, str, str | None]] = []

    def node(self, node_id: str, label: str, css: str, *, shape: str = "box") -> None:
        current = self.nodes.get(node_id)
        if current and current[1] in {"focus", "gone", "new"} and css in {"caller", "callee", "entry"}:
            return
        self.nodes[node_id] = (label, css, shape)

    def edge(self, source: str, target: str, arrow: str, css: str | None) -> None:
        key = (source, target, arrow, css)
        if key not in self.edges:
            self.edges.append(key)

    def render(self, title: str, *, side: str) -> str:
        if not self.nodes:
            return ""
        lines = ["flowchart LR"]
        for node_id, (label, _css, shape) in self.nodes.items():
            text = _text(label)
            if shape == "decision":
                lines.append(f'  {node_id}{{"{text}"}}')
            elif shape == "outcome":
                lines.append(f'  {node_id}(["{text}"])')
            else:
                lines.append(f'  {node_id}["{text}"]')
        styled: list[int] = []
        for index, (source, target, arrow, css) in enumerate(self.edges):
            lines.append(f"  {source} {arrow} {target}")
            if css:
                styled.append(index)
        colour = "#c0392b" if side == "before" else "#1e8449"
        if styled:
            lines.append(f"  linkStyle {','.join(str(i) for i in styled)} stroke:{colour},stroke-width:2px")
        lines.append("  classDef focus fill:#1e1a16,stroke:#1e1a16,color:#f3efe6")
        lines.append("  classDef caller fill:#f3efe6,stroke:#8a8172,color:#1e1a16")
        lines.append("  classDef entry fill:#f3efe6,stroke:#1e1a16,stroke-width:2px,color:#1e1a16")
        lines.append("  classDef callee fill:#ffffff,stroke:#8a8172,color:#1e1a16")
        lines.append("  classDef gone fill:#fdecea,stroke:#c0392b,color:#7b241c,stroke-dasharray:4 3")
        lines.append("  classDef new fill:#e9f7ef,stroke:#1e8449,color:#145a32")
        groups: dict[str, list[str]] = {}
        for node_id, (_label, css, _shape) in self.nodes.items():
            groups.setdefault(css, []).append(node_id)
        for css, ids in groups.items():
            lines.append(f"  class {','.join(ids)} {css}")
        del title
        return "\n".join(lines)


def _header_label(header: str, fallback: str, path: str) -> str:
    match = re.search(r"([A-Za-z_]\w*)\s*\((.*)", header)
    if not match:
        return fallback
    inner = match.group(2)
    depth = 1
    end = len(inner)
    for i, char in enumerate(inner):
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth -= 1
            if depth == 0:
                end = i
                break
    java = path.endswith(".java")
    names = []
    for param in _split_params(inner[:end]):
        piece = param.split("=")[0]
        piece = piece.split(":")[0] if ":" in piece else piece
        tokens = re.findall(r"[A-Za-z_]\w*", piece)
        if not tokens or tokens[0] in {"self", "cls"}:
            continue
        names.append(tokens[-1] if java else tokens[0])
    return f"{fallback}({', '.join(names)})"


def _split_params(text: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in text:
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)
    return [part.strip() for part in parts if part.strip()]


_ENTITIES = {
    "#": "#35;",
    '"': "#quot;",
    "`": "#96;",
    "[": "#91;",
    "]": "#93;",
    "{": "#123;",
    "}": "#125;",
    "<": "#lt;",
    ">": "#gt;",
    "|": "#124;",
}


def _text(label: str) -> str:
    """Exact source text, escaped with Mermaid entity codes so the label shows the code unchanged."""
    text = " ".join(str(label).split())
    if len(text) > LABEL_CAP:
        text = text[: LABEL_CAP - 1] + "…"
    return "".join(_ENTITIES.get(char, char) for char in text)


def _short(name: str) -> str:
    return name.split(".")[-1]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = _short(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value) or "x"


def _id(item):
    return getattr(item, "public_id", None) or getattr(item, "id", None)


def _rel_target(rel):
    return getattr(rel, "target_public_id", None) or getattr(rel, "target_id", None)


def _rel_source(rel):
    return getattr(rel, "source_public_id", None) or getattr(rel, "source_id", None)

