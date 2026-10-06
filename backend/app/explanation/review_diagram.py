"""Change-impact diagram for the Explain tab and the PR comment.

One Mermaid flowchart that a reviewer who has never seen the code can read:

* upstream: the entry points and callers that reach the changed code (stored CALLS edges at
  head), with the arguments each direct caller passes on the arrow,
* the change: changed functions (orange, CHANGED), new functions (green, NEW), removed
  functions (red, REMOVED), grouped by module,
* downstream: what the changed code calls at head; calls that appear only on added lines are
  green NEW edges, calls only on removed lines are red dashed REMOVED edges,
* failure paths: errors raised by the change with their condition (ERROR), and RISK when no
  resolved caller wraps the call in a try block, or a rule-derived high finding names it,
* outside the code: HTTP routes declared in changed files, tables and columns the change
  touches, and calls to well-known external clients (EXTERNAL).

Every node and edge comes from stored analysis facts. Private helpers are left out unless no
public function changed. Sizes are capped so the picture stays readable.
"""

from __future__ import annotations

import re

from app.analyzer.behavior import call_names, error_target
from app.explanation.behavior_comparison import build_behavior_comparison
from app.explanation.behavior_flow import _text
from app.explanation.explain_view import explain_skip_symbol

REVIEW_DIAGRAM_LEGEND = (
    "Orange: changed by this PR (thick red border = RISK). Green: new. Red dashed: removed or a failure path (ERROR). "
    "Blue: routes, data stores, and external systems (EXTERNAL). Neutral: existing callers and dependencies; "
    "arrow labels show what a caller passes."
)
CHANGED_CAP = 4
CALLER_CAP = 4
CALLEE_CAP = 4
ERROR_CAP = 2
ARG_CAP = 28
_EXTERNAL_ROOTS = {
    "requests", "httpx", "aiohttp", "urllib", "fetch", "axios", "boto3", "s3", "sqs", "sns", "redis",
    "kafka", "producer", "publisher", "smtplib", "stripe", "openai", "anthropic", "github", "octokit",
}
_WHAT = {
    "signature": "inputs",
    "error": "errors",
    "return": "result",
    "condition": "branching",
    "value": "values",
    "call": "calls",
    "logging": "logging",
}
_DB_CALL = re.compile(r"(?:^|\.)(?:session|db|conn|cursor|engine)\.(?:add|delete|commit|execute|merge|flush|query|scalars?)$")


def build_review_diagram(*, symbols, relationships, claims, evidences) -> str:
    comparison = build_behavior_comparison(symbols=symbols, relationships=relationships, claims=claims, evidences=evidences)
    items = [item for item in comparison.get("items") or [] if item.get("changes") and item.get("name")]
    public = [item for item in items if not explain_skip_symbol(item["name"], item["file"])]
    chosen = (public or items)[:CHANGED_CAP]
    if not chosen:
        return ""

    by_id = {_id(s): s for s in symbols or [] if getattr(s, "kind", None) == "function"}
    callees_of: dict[str, list[str]] = {}
    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        source = getattr(rel, "source_public_id", None) or getattr(rel, "source_id", None)
        target = by_id.get(getattr(rel, "target_public_id", None) or getattr(rel, "target_id", None))
        name = getattr(target, "name", None) or getattr(rel, "target_name", None)
        if source and name and name not in callees_of.setdefault(source, []):
            callees_of[source].append(name)
    evidence_by_id = {_id(e): e for e in evidences or []}
    contexts = _contexts(claims, evidence_by_id)
    call_args = _call_args(relationships, evidence_by_id)
    surfaces = _surfaces(claims, evidence_by_id)
    risky_names = _high_risk_names(claims, evidences, symbols, relationships, [item["name"] for item in chosen])

    g = _Graph()
    changed_ids: dict[str, str] = {}
    for index, item in enumerate(chosen):
        node = f"c{index}"
        changed_ids[item["name"]] = node
        status = "removed" if item["removed"] else "new" if _is_new(item) else "changed"
        tags = [{"removed": "REMOVED", "new": "NEW", "changed": "CHANGED"}[status]]
        if status == "changed":
            what = _unique([_WHAT.get(c["category"], "") for c in item["changes"]])
            if what:
                tags[0] = "CHANGED: " + ", ".join(what[:4])
        if _unhandled_failure(item, contexts) or item["name"] in risky_names:
            tags.append("RISK")
        g.node(node, item["name"], " · ".join(tags), "risk" if "RISK" in tags and status == "changed" else status, group=_module(item["file"]))

    for index, item in enumerate(chosen):
        node = changed_ids[item["name"]]
        # Upstream: the caller chain to the entry points, each edge pointing at what it calls.
        callers = [c for c in item["reach"]["callers"] if not c.get("private")][: CALLER_CAP * 2]
        ids = {(c["depth"], c["name"]): "u_" + _slug(c["name"]) for c in callers}
        for caller in callers:
            target = node if caller["depth"] == 1 else ids.get((caller["depth"] - 1, caller["via"]))
            if target is None:
                continue
            uid = ids[(caller["depth"], caller["name"])]
            g.node(uid, caller["name"], "entry point" if caller.get("entry_point") else "", "entry" if caller.get("entry_point") else "neutral")
            label = call_args.get((caller["name"], item["name"])) if caller["depth"] == 1 else None
            g.edge(uid, target, label or "", "plain")

        # Downstream: head callees, plus calls only on added (NEW) or only on removed (REMOVED) lines.
        old_calls, new_calls = [], []
        for change in item["changes"]:
            if change["category"] == "signature":
                continue
            old_calls += [c for c in call_names(change.get("before") or "") if c not in old_calls]
            new_calls += [c for c in call_names(change.get("after") or "") if c not in new_calls]
        short = lambda name: name.split(".")[-1]  # noqa: E731
        gained = [c for c in new_calls if short(c) not in {short(o) for o in old_calls}]
        lost = [c for c in old_calls if short(c) not in {short(n) for n in new_calls}]
        head = callees_of.get(item.get("symbol_id") or "", [])
        gained_short = {short(c) for c in gained}
        lost_short = {short(c) for c in lost}
        shown = 0
        seen_short: set[str] = set()
        # Prefer the spelling written in the diff (audit.record) over the resolved name (record).
        for callee in _unique(gained + lost + head):
            if shown >= CALLEE_CAP or short(callee) in seen_short:
                continue
            seen_short.add(short(callee))
            if callee in changed_ids:
                g.edge(node, changed_ids[callee], "", "plain")
                continue
            style = "new" if short(callee) in gained_short else "removed" if short(callee) in lost_short else "plain"
            external = _external(callee)
            did = ("x_" if external else "d_") + _slug(callee)
            kind = "external" if external else ("new" if style == "new" else "removed" if style == "removed" else "neutral")
            tag = "EXTERNAL" if external else ""
            if style != "plain":
                tag = (tag + " · " if tag else "") + {"new": "NEW", "removed": "REMOVED"}[style]
            g.node(did, callee, tag, kind, group="External systems" if external else None)
            g.edge(node, did, {"new": "NEW call", "removed": "REMOVED call"}.get(style, ""), style)
            shown += 1

        # New early exits: a return that only exists on added lines, under a condition.
        for n, change in enumerate([c for c in item["changes"] if c["category"] == "return" and c.get("after") and not c.get("before") and c.get("after_when")][:ERROR_CAP]):
            rid = f"r{index}_{n}"
            value = re.sub(r"^(?:return|yield)\s*", "", change["after"]).rstrip(";").strip() or "nothing"
            g.node(rid, f"returns {value} when {change['after_when']}", "NEW early exit", "new")
            g.edge(node, rid, "", "new")

        # Failure paths.
        errors = [c for c in item["changes"] if c["category"] == "error"][:ERROR_CAP]
        for n, change in enumerate(errors):
            eid = f"e{index}_{n}"
            if change.get("after"):
                when = f" when {change['after_when']}" if change.get("after_when") else ""
                was = f" (was {error_target(change['before'])})" if change.get("before") else ""
                tag = "ERROR · CHANGED" if change.get("before") else "ERROR · NEW"
                g.node(eid, f"{error_target(change['after'])}{when}{was}", tag, "error")
                g.edge(node, eid, "raises", "error")
            else:
                when = f" when {change['before_when']}" if change.get("before_when") else ""
                g.node(eid, f"{error_target(change['before'])}{when}", "ERROR · REMOVED", "removed")
                g.edge(node, eid, "no longer raises", "removed")

        # Outside the code: routes and data the change touches in the same file.
        for level, change, name, path in surfaces:
            if path != item["file"]:
                continue
            sid = "s_" + _slug(name)
            if level == "api":
                tag = {"added": "EXTERNAL · NEW", "removed": "EXTERNAL · REMOVED"}.get(change, "EXTERNAL · CHANGED")
                g.node(sid, name, tag, "external" if change != "added" else "new", group="External systems")
                g.edge(sid, node, "HTTP request", "new" if change == "added" else "removed" if change == "removed" else "plain")
            elif level == "data":
                tag = {"added": "NEW", "removed": "REMOVED"}.get(change, "CHANGED")
                g.node(sid, name, f"DATABASE · {tag}", "external", group="External systems")
                g.edge(node, sid, "reads / writes", "new" if change == "added" else "removed" if change == "removed" else "plain")

    return g.render()


# --- graph ------------------------------------------------------------------------------


class _Graph:
    """Builds the Explain tab's change-impact Mermaid diagram: nodes with review markers, edges, and class styles."""

    def __init__(self) -> None:
        self.nodes: dict[str, tuple[str, str, str, str | None]] = {}
        self.edges: list[tuple[str, str, str, str]] = []

    def node(self, node_id: str, name: str, tag: str, kind: str, group: str | None = None) -> None:
        """Add a node once, with its label and style class."""
        if node_id in self.nodes and self.nodes[node_id][2] in {"changed", "risk", "new", "removed", "error"}:
            return
        self.nodes[node_id] = (name, tag, kind, group)

    def edge(self, source: str, target: str, label: str, style: str) -> None:
        """Add a labeled edge between two nodes."""
        key = (source, target, label, style)
        if key not in self.edges and source != target:
            self.edges.append(key)

    def render(self) -> str:
        """Return the diagram as Mermaid text."""
        lines = ["flowchart LR"]
        grouped: dict[str | None, list[str]] = {}
        for node_id, (_name, _tag, _kind, group) in self.nodes.items():
            grouped.setdefault(group, []).append(node_id)
        for index, (group, ids) in enumerate(grouped.items()):
            if group:
                lines.append(f'  subgraph g{index}["{_text(group)}"]')
            for node_id in ids:
                name, tag, kind, _group = self.nodes[node_id]
                label = _text(name) + (f"<br/><b>{_text(tag)}</b>" if tag else "")
                shape = ('(["', '"])') if kind == "error" else ('[("', '")]') if "DATABASE" in tag else ('["', '"]')
                lines.append(f"  {'  ' if group else ''}{node_id}{shape[0]}{label}{shape[1]}")
            if group:
                lines.append("  end")
        styles: dict[str, list[int]] = {}
        for index, (source, target, label, style) in enumerate(self.edges):
            arrow = "-.->" if style in {"error", "removed"} else "-->"
            lines.append(f'  {source} {arrow}|"{_text(label)}"| {target}' if label else f"  {source} {arrow} {target}")
            styles.setdefault(style, []).append(index)
        colours = {"new": "#1e8449", "removed": "#c0392b", "error": "#b03a2e"}
        for style, indexes in styles.items():
            if style in colours:
                dash = ",stroke-dasharray:4 3" if style in {"removed", "error"} else ""
                lines.append(f"  linkStyle {','.join(map(str, indexes))} stroke:{colours[style]},stroke-width:2px{dash}")
        lines += [
            "  classDef changed fill:#fdebd0,stroke:#d35400,stroke-width:2px,color:#1e1a16",
            "  classDef risk fill:#fdebd0,stroke:#c0392b,stroke-width:4px,color:#1e1a16",
            "  classDef new fill:#e9f7ef,stroke:#1e8449,stroke-width:2px,color:#145a32",
            "  classDef removed fill:#fdecea,stroke:#c0392b,color:#7b241c,stroke-dasharray:4 3",
            "  classDef error fill:#fdecea,stroke:#b03a2e,color:#7b241c",
            "  classDef external fill:#eaf2f8,stroke:#2e86c1,color:#1b4f72",
            "  classDef entry fill:#f3efe6,stroke:#1e1a16,stroke-width:1.5px,color:#1e1a16",
            "  classDef neutral fill:#ffffff,stroke:#8a8172,color:#1e1a16",
        ]
        by_kind: dict[str, list[str]] = {}
        for node_id, (_name, _tag, kind, _group) in self.nodes.items():
            by_kind.setdefault(kind, []).append(node_id)
        for kind, ids in by_kind.items():
            lines.append(f"  class {','.join(ids)} {kind}")
        return "\n".join(lines)


# --- facts ------------------------------------------------------------------------------


def _is_new(item: dict) -> bool:
    signature = next((c for c in item["changes"] if c["category"] == "signature"), None)
    return bool(signature and signature.get("after") and not signature.get("before"))


def _unhandled_failure(item: dict, contexts: dict[str, list[bool]]) -> bool:
    has_new_error = any(c["category"] == "error" and c.get("after") for c in item["changes"])
    handled = contexts.get(item["name"], [])
    return has_new_error and bool(handled) and not any(handled)


def _contexts(claims, evidence_by_id) -> dict[str, list[bool]]:
    out: dict[str, list[bool]] = {}
    for claim in claims or []:
        if getattr(claim, "kind", None) != "call_context":
            continue
        for evidence_id in _evidence_ids(claim):
            evidence = evidence_by_id.get(evidence_id)
            parts = (evidence.description or "").split("|") if evidence is not None else []
            if len(parts) == 5:
                out.setdefault(parts[2], []).append(parts[3] == "handled")
    return out


def _call_args(relationships, evidence_by_id) -> dict[tuple[str, str], str]:
    """(caller, callee) → the arguments written at the head call site, shortened."""
    out: dict[tuple[str, str], str] = {}
    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        evidence = evidence_by_id.get(getattr(rel, "evidence_public_id", None) or getattr(rel, "evidence_id", None))
        target = getattr(rel, "target_name", "") or ""
        if evidence is None or not evidence.snippet or not target:
            continue
        match = re.search(rf"\b{re.escape(target)}\s*\((.*)", evidence.snippet)
        if not match:
            continue
        depth, end = 1, None
        for i, ch in enumerate(match.group(1)):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0:
                end = i
                break
        args = " ".join(match.group(1)[:end].split()) if end is not None else ""
        if args:
            out.setdefault((getattr(rel, "source_name", ""), target), args if len(args) <= ARG_CAP else args[: ARG_CAP - 1] + "…")
    return out


def _surfaces(claims, evidence_by_id) -> list[tuple[str, str, str, str]]:
    out = []
    for claim in claims or []:
        if getattr(claim, "kind", None) != "surface_changed":
            continue
        for evidence_id in _evidence_ids(claim):
            evidence = evidence_by_id.get(evidence_id)
            parts = (evidence.description or "").split("|", 3) if evidence is not None else []
            if len(parts) == 4 and parts[0] in {"api", "data"}:
                out.append((parts[0], parts[1], parts[2], evidence.file or ""))
    return out


def _high_risk_names(claims, evidences, symbols, relationships, names: list[str]) -> set[str]:
    """Changed functions named in a high-severity Impact finding (for example a caller that no longer
    matches the new signature)."""
    from app.explanation.behavior_facts import build_behavior_facts
    from app.explanation.impact import build_impact

    facts = build_behavior_facts(symbols=symbols, relationships=relationships, claims=claims, evidences=evidences)
    section = build_impact(claims=claims, evidences=evidences, behavior_facts=facts)
    risky = set()
    for item in section["attention"]:
        if item["severity"] != "high":
            continue
        text = f"{item['title']} {item['why']}"
        risky.update(name for name in names if re.search(rf"\b{re.escape(name)}\b", text))
    return risky


def _external(name: str) -> bool:
    root = name.split(".")[0].lower()
    return root in _EXTERNAL_ROOTS or bool(_DB_CALL.search(name))


def _module(path: str) -> str:
    parts = path.split("/")
    return "/".join(parts[-2:]) if len(parts) > 1 else path


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value) or "x"


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _evidence_ids(claim) -> list[str]:
    ids = getattr(claim, "evidence_public_ids", None)
    if ids is None:
        ids = getattr(claim, "evidence_ids", None)
    return list(ids or [])


def _id(item):
    return getattr(item, "public_id", None) or getattr(item, "id", None)
