"""System Flow Change: how the request/call flows of the system behave differently.

This is the reviewer-lead view. It is organised by flow, not by file:

1. Each changed function is placed on the flows that reach it. A flow starts at
   an entry point (a stored caller with no further callers, or an exported
   function) and follows stored CALLS edges at the head commit down to the
   changed code.
2. Changed functions reached from the same set of entry points form one flow
   family, so ``login``, ``googleCallback`` and ``refreshToken`` into
   ``createSession`` read as one session-creation flow.
3. For each family, the base and head statements of the diff (stored
   ``behavior_changed`` claims and their evidence) are turned into flow
   effects: contract changes checked against the head call sites, outbound
   calls gained or dropped, new or changed failure modes, early exits,
   changed results, changed values and decisions, removed or new functions.
4. Blast radius, unaffected files, partial-view notes and review focus come
   from the same stored facts (reach, TESTS edges, ``behavior_unchanged``,
   ``fanout_truncated`` and ``ambiguous_call`` claims).

Nothing is inferred beyond those facts. Statement changes that fit none of
these shapes are counted, not paraphrased.
"""

from __future__ import annotations

import re

from app.analyzer.behavior import _language, _params, call_names, error_target
from app.explanation.behavior_flow import _header_label

FAMILY_CAP = 8
EFFECT_CAP = 8
PATH_CAP = 4
FOCUS_CAP = 6
EXPR_CAP = 70


def build_system_flow(comparison: dict, *, relationships, evidences, claims) -> dict:
    items = comparison.get("items") or []
    empty = {
        "headline": "",
        "flows": [],
        "blast_radius": "",
        "unaffected": "",
        "partial": "",
        "focus": [],
        "not_summarized": 0,
    }
    if not items:
        return empty
    evidence_by_id = {_id(item): item for item in evidences or []}
    call_sites = _call_sites(relationships, evidence_by_id)

    families: dict[tuple[str, ...], dict] = {}
    order: list[tuple[str, ...]] = []
    skipped = 0
    focus: list[str] = []
    for item in items:
        entries, paths = _entries_and_paths(item)
        key = tuple(sorted(entries)) if entries else (f"~{item['display_name']}",)
        if key not in families:
            families[key] = {
                "entries": entries,
                "paths": [],
                "changed": [],
                "effects": [],
                "tests": [],
                "untested": [],
                "removed_only": True,
            }
            order.append(key)
        family = families[key]
        for path in paths:
            if path not in family["paths"]:
                family["paths"].append(path)
        if item["display_name"] not in family["changed"]:
            family["changed"].append(item["display_name"])
        tests = item["reach"].get("tests") or []
        for test in tests:
            if test not in family["tests"]:
                family["tests"].append(test)
        if not tests and not item["removed"]:
            family["untested"].append(item["display_name"])
        if not item["removed"]:
            family["removed_only"] = False
        effects, dropped, item_focus = _effects(item, call_sites.get(item.get("symbol_id"), []))
        skipped += dropped
        for effect in effects:
            if effect not in family["effects"]:
                family["effects"].append(effect)
        for line in item_focus:
            if line not in focus:
                focus.append(line)

    flows = []
    for key in order[:FAMILY_CAP]:
        family = families[key]
        if not family["effects"]:
            continue
        effects = sorted(family["effects"], key=lambda effect: _EFFECT_ORDER.get(effect["kind"], 9))
        if len(effects) > EFFECT_CAP:
            skipped += len(effects) - EFFECT_CAP
            effects = effects[:EFFECT_CAP]
        flows.append(
            {
                "title": _family_title(family),
                "entries": family["entries"],
                "paths": family["paths"][:PATH_CAP],
                "more_paths": max(0, len(family["paths"]) - PATH_CAP),
                "changed": family["changed"],
                "effects": [{"kind": e["kind"], "label": _EFFECT_LABEL[e["kind"]], "before": e["before"], "after": e["after"]} for e in effects],
                "tests": family["tests"],
                "untested": family["untested"],
                "removed_only": family["removed_only"],
            }
        )
        for name in family["untested"]:
            risky = [e for e in effects if e["kind"] in {"failure", "contract", "exit", "result"}]
            if risky:
                line = f"No stored test references {_code(name)}, and this flow's {_EFFECT_LABEL[risky[0]['kind']].lower()} changes there."
                if line not in focus:
                    focus.append(line)
    skipped += sum(len(families[key]["effects"]) for key in order[FAMILY_CAP:])

    unchanged = sum(1 for claim in claims or [] if _kind(claim) == "behavior_unchanged")
    truncated = [claim for claim in claims or [] if _kind(claim) in {"fanout_truncated", "ambiguous_call"}]
    return {
        "headline": _headline(flows),
        "flows": flows,
        "blast_radius": _blast_radius(items, flows),
        "unaffected": (
            f"{unchanged} other production file{'s have' if unchanged != 1 else ' has'} no stored call or "
            "import path into the changed code, so their flows are unchanged."
            if unchanged
            else ""
        ),
        "partial": (
            f"The caller view is partial: {len(truncated)} call site{'s were' if len(truncated) != 1 else ' was'} "
            "not resolved to a single function or was truncated, so some flows may be missing."
            if truncated
            else ""
        ),
        "focus": focus[:FOCUS_CAP],
        "not_summarized": skipped,
    }


def render_system_flow_markdown(summary: dict) -> str:
    flows = summary.get("flows") or []
    if not flows:
        return ""
    lines = ["### System Flow Change", "", summary.get("headline") or ""]
    for flow in flows:
        lines.extend(["", f"#### {flow['title']}"])
        if flow["paths"]:
            lines.append("")
        for path in flow["paths"]:
            lines.append(f"- {path}")
        if flow["more_paths"]:
            lines.append(f"- … and {flow['more_paths']} more path{'s' if flow['more_paths'] != 1 else ''}")
        lines.extend(["", "| | Before this PR | After this PR |", "|---|---|---|"])
        for effect in flow["effects"]:
            lines.append(f"| {effect['label']} | {_cell(effect['before'])} | {_cell(effect['after'])} |")
        if not flow.get("removed_only"):
            tests = ", ".join(flow["tests"][:4]) if flow["tests"] else "no stored test references the changed code"
            lines.extend(["", f"Tests on this flow: {tests}."])
    extra = [text for text in (summary.get("blast_radius"), summary.get("unaffected"), summary.get("partial")) if text]
    if extra:
        lines.extend(["", "**Blast radius:** " + " ".join(extra)])
    focus = summary.get("focus") or []
    if focus:
        lines.extend(["", "**Review focus**", ""])
        lines.extend(f"- {line}" for line in focus)
    if summary.get("not_summarized"):
        n = summary["not_summarized"]
        lines.extend(["", f"_{n} other statement change{'s are' if n != 1 else ' is'} in the diff but not summarized here._"])
    return "\n".join(lines).strip()


# --- flows -----------------------------------------------------------------------


def _entries_and_paths(item: dict) -> tuple[list[str], list[str]]:
    """Entry points that reach this function, and each entry's call path down to it."""
    callers = item["reach"].get("callers") or []
    by_key = {(caller["depth"], caller["name"]): caller for caller in callers}
    target = item["display_name"]
    entries: list[str] = []
    paths: list[str] = []
    for caller in callers:
        if not caller.get("entry_point"):
            continue
        chain = [caller["name"]]
        current = caller
        while current["depth"] > 1:
            parent = by_key.get((current["depth"] - 1, current["via"]))
            if parent is None:
                break
            chain.append(parent["name"])
            current = parent
        chain.append(target)
        if caller["name"] not in entries:
            entries.append(caller["name"])
        path = " → ".join(_code(name) for name in chain)
        if path not in paths:
            paths.append(path)
    if not entries and item["exported"] and not item["removed"]:
        paths.append(f"{_code(target)} (exported; no stored caller inside the repository)")
    elif not entries and not item["removed"]:
        paths.append(f"{_code(target)} (no stored caller reaches it)")
    return entries, paths


def _family_title(family: dict) -> str:
    changed = _names(family["changed"], 3)
    if family["entries"]:
        return f"Flow: {_names(family['entries'], 4)} → {changed}"
    if family["removed_only"]:
        return f"Removed from the system: {changed}"
    return f"Not reached from any stored caller: {changed}"


_EFFECT_ORDER = {
    "removed": 0,
    "contract": 1,
    "failure": 2,
    "exit": 3,
    "result": 4,
    "outbound": 5,
    "decision": 6,
    "value": 7,
    "new": 8,
}
_EFFECT_LABEL = {
    "removed": "Removed step",
    "contract": "Contract",
    "failure": "Failure mode",
    "exit": "Early exit",
    "result": "Result",
    "outbound": "Outbound call",
    "decision": "Decision",
    "value": "Value",
    "new": "New step",
}


def _effects(item: dict, sites: list[dict]) -> tuple[list[dict], int, list[str]]:
    name = _code(item["display_name"])
    changes = item["changes"]
    guards = {c.get("after_when") for c in changes} | {c.get("before_when") for c in changes}
    effects: list[dict] = []
    focus: list[str] = []
    dropped = 0

    def add(kind: str, before: str, after: str) -> None:
        effect = {"kind": kind, "before": before, "after": after}
        if effect not in effects:
            effects.append(effect)

    for change in changes:
        category = change["category"]
        before, after = change.get("before"), change.get("after")
        b_when, a_when = change.get("before_when"), change.get("after_when")

        if category == "removed_function":
            add("removed", f"{name} is defined in the base commit.", f"{name} is not defined at the head commit.")
            continue

        if category == "signature":
            if before and after:
                old = _header_label(before, item["display_name"], item["file"])
                new = _header_label(after, item["display_name"], item["file"])
                if old == new:
                    defaults = _defaults(before, after, item["file"]).strip()
                    if defaults:
                        add("contract", f"{name} keeps the parameters {_code(old)}.", defaults)
                    else:
                        dropped += 1
                    continue
                add(
                    "contract",
                    f"Callers enter through {_code(old)}.",
                    f"Callers enter through {_code(new)}.{_param_delta(before, after, item['file'])}"
                    f"{_defaults(before, after, item['file'])}{_readiness(after, item, sites, focus)}",
                )
            elif after:
                where = "and is reached from " + _names(_entry_names(item), 3) if _entry_names(item) else "with no stored caller yet"
                add("new", f"There is no {name} step.", f"{name} is a new step {where}.")
            else:
                dropped += 1
            continue

        if category == "error":
            if before and after:
                add("failure", f"The flow fails at {name} with {error_target(before)}{_when(b_when)}.", f"It fails there with {error_target(after)}{_when(a_when)}.")
            elif after:
                add("failure", f"{name} does not raise {error_target(after)}.", f"The flow can now fail at {name} with {error_target(after)}{_when(a_when)}.")
            else:
                add("failure", f"The flow can fail at {name} with {error_target(before)}{_when(b_when)}.", f"{name} no longer raises {error_target(before)}.")
            continue

        if category == "return":
            old_expr = _return_expr(before) if before else None
            new_expr = _return_expr(after) if after else None
            if before and after:
                add("result", f"{name} hands {_code(old_expr)} back up the flow{_when(b_when)}.", f"It hands {_code(new_expr)} back up the flow{_when(a_when)}.")
            elif after:
                kind = "exit" if a_when else "result"
                add(kind, f"{name} does not return {_code(new_expr)}{_when(a_when)}.", f"{name} returns {_code(new_expr)} to its caller{_when(a_when)}, and the rest of it is skipped.")
            else:
                add("exit" if b_when else "result", f"{name} returns {_code(old_expr)}{_when(b_when)}.", "That return is removed, so execution continues past it.")
            continue

        if category == "condition":
            old_cond = _condition(before) if before else None
            new_cond = _condition(after) if after else None
            if (old_cond is None or old_cond in guards) and (new_cond is None or new_cond in guards):
                continue  # Already stated as the condition of a failure or exit above.
            if before and after:
                add("decision", f"{name} branches on {_code(old_cond)}.", f"It branches on {_code(new_cond)}.")
            elif after:
                add("decision", f"{name} has no check on {_code(new_cond)}.", f"It branches on {_code(new_cond)}.")
            else:
                add("decision", f"{name} branches on {_code(old_cond)}.", "That check is removed.")
            continue

        if category in {"call", "logging"}:
            old_calls = call_names(before or "")
            new_calls = call_names(after or "")
            gained = [c for c in new_calls if c not in old_calls]
            lost = [c for c in old_calls if c not in new_calls]
            if gained:
                add("outbound", f"The flow does not reach {_names(gained)} from {name}.", f"{name} now calls {_names(gained)}{_when(a_when)}.")
            if lost:
                add("outbound", f"{name} calls {_names(lost)}{_when(b_when)}.", f"The flow no longer reaches {_names(lost)} from {name}.")
            if not gained and not lost:
                shared = [c for c in new_calls if c in old_calls]
                if shared and before and after:
                    target = shared[0]
                    add("outbound", f"{name} calls {_code(target)} with {_code(_args(before, target))}.", f"It passes {_code(_args(after, target))}.")
                else:
                    dropped += 1
            continue

        if category == "value":
            key = _value_key(after or before)
            if not key:
                dropped += 1
                continue
            if before and after:
                add("value", f"In {name}, {_code(key)} is {_code(_rhs(before))}{_when(b_when)}.", f"{_code(key)} is {_code(_rhs(after))}{_when(a_when)}.")
            elif after:
                add("value", f"{name} does not set {_code(key)}.", f"{name} sets {_code(key)} to {_code(_rhs(after))}{_when(a_when)}.")
            else:
                add("value", f"{name} sets {_code(key)} to {_code(_rhs(before))}{_when(b_when)}.", f"{name} no longer sets {_code(key)}.")
            continue

        dropped += 1
    return effects, dropped, focus


def _param_delta(before: str, after: str, path: str) -> str:
    """Parameters added or removed between the two header lines, with whether a new one has a default."""
    old_specs = _param_specs(before, path) or []
    new_specs = _param_specs(after, path) or []
    language = _language(path)
    old_names = [name for name, _ in _params(before, language)]
    new_params = _params(after, language)
    parts: list[str] = []
    added = []
    for (name, _default), (_raw, has_default, variadic) in zip(new_params, [spec for spec in new_specs if spec[0] not in {"*"}]):
        if name in old_names:
            continue
        added.append(f"{_code(name)} ({'optional' if has_default or variadic else 'required'})")
    removed = [_code(name) for name in old_names if name not in [n for n, _ in new_params]]
    if added:
        parts.append(f"New parameter{'s' if len(added) != 1 else ''}: {', '.join(added)}")
    if removed:
        parts.append(f"Removed parameter{'s' if len(removed) != 1 else ''}: {', '.join(removed)}")
    del old_specs
    return (" " + ". ".join(parts) + ".") if parts else ""


def _defaults(before: str, after: str, path: str) -> str:
    """Default values that differ between the base and head signatures, read from the two header lines."""
    language = _language(path)
    old = dict(_params(before, language))
    changed = []
    for param, default in _params(after, language):
        if param in old and old[param] != default and (default or old[param]):
            changed.append(f"default of {_code(param)} changes from {_code(old[param] or 'none')} to {_code(default or 'none')}")
    if not changed:
        return ""
    text = "; ".join(changed)
    return " " + text[0].upper() + text[1:] + "."


def _entry_names(item: dict) -> list[str]:
    return [c["name"] for c in item["reach"].get("callers") or [] if c.get("entry_point")]


def _readiness(header: str, item: dict, sites: list[dict], focus: list[str]) -> str:
    """Compare head call sites with the new signature: argument count against declared parameters."""
    if not sites:
        return ""
    params = _param_specs(header, item["file"])
    if params is None:
        return ""
    required = sum(1 for _name, has_default, variadic in params if not has_default and not variadic)
    total = len(params)
    variadic = any(v for _n, _d, v in params)
    short: list[dict] = []
    for site in sites:
        count = _arg_count(site["call"])
        if count is None:
            return ""
        if count < required or (not variadic and count > total):
            short.append({**site, "count": count})
    name = _code(item["display_name"])
    if not short:
        return f" All {len(sites)} call site{'s' if len(sites) != 1 else ''} at head pass a matching number of arguments."
    for site in short:
        line = (
            f"{_code(site['caller'])} calls {_code(site['call'])} with {site['count']} argument"
            f"{'s' if site['count'] != 1 else ''}; {name} now declares {required} required."
        )
        if line not in focus:
            focus.append(line)
    return f" {len(short)} of {len(sites)} call site{'s' if len(sites) != 1 else ''} at head pass a different number of arguments."


def _param_specs(header: str, path: str) -> list[tuple[str, bool, bool]] | None:
    start = header.find("(")
    if start < 0:
        return None
    depth = 0
    end = None
    for index in range(start, len(header)):
        if header[index] in "([{<":
            depth += 1
        elif header[index] in ")]}>":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end is None:
        return None
    specs = []
    for raw in _split_top(header[start + 1 : end]):
        piece = raw.strip()
        if not piece or piece in {"self", "cls", "/"}:
            continue
        if piece == "*":
            continue
        variadic = piece.startswith("*") or "..." in piece
        has_default = "=" in piece or (path.endswith((".ts", ".tsx")) and "?" in piece.split(":")[0])
        specs.append((piece, has_default, variadic))
    return specs


def _arg_count(call: str) -> int | None:
    start = call.find("(")
    if start < 0 or not call.endswith(")"):
        return None
    inner = call[start + 1 : -1].strip()
    if not inner:
        return 0
    if "*" in inner.split(",")[0][:2] or "..." in inner:
        return None
    return len([part for part in _split_top(inner) if part.strip()])


def _split_top(text: str) -> list[str]:
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
    return parts


def _headline(flows: list[dict]) -> str:
    if not flows:
        return ""
    entries = _unique([name for flow in flows for name in flow["entries"]])
    kinds = _unique([effect["kind"] for flow in flows for effect in flow["effects"]])
    flow_count = len(flows)
    text = f"This pull request changes {flow_count} system flow{'s' if flow_count != 1 else ''}"
    if entries:
        text += f", entered from {len(entries)} entry point{'s' if len(entries) != 1 else ''} ({_names(entries, 5)})"
    text += "."
    if kinds:
        text += " Across them it changes the " + _join([_EFFECT_LABEL[kind].lower() for kind in kinds]) + "."
    return text


def _blast_radius(items: list[dict], flows: list[dict]) -> str:
    entries = _unique([name for flow in flows for name in flow["entries"]])
    files = _unique([path for item in items for path in item["reach"].get("files") or []])
    outside = _unique([c["name"] for item in items for c in item["reach"].get("callers") or [] if c.get("outside_diff")])
    untested = _unique([name for flow in flows for name in flow["untested"]])
    parts = [
        f"{len(entries)} entry point{'s' if len(entries) != 1 else ''}",
        f"{len(files)} calling file{'s' if len(files) != 1 else ''}",
        f"{len(outside)} caller{'s' if len(outside) != 1 else ''} outside this diff",
    ]
    text = "Reaches " + ", ".join(parts) + "."
    if untested:
        text += f" {len(untested)} changed function{'s have' if len(untested) != 1 else ' has'} no stored test reference."
    return text


# --- call sites -----------------------------------------------------------------------


def _call_sites(relationships, evidence_by_id) -> dict[str, list[dict]]:
    """Head-commit call expressions into each function, read from the stored call evidence."""
    sites: dict[str, list[dict]] = {}
    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        target = getattr(rel, "target_public_id", None) or getattr(rel, "target_id", None)
        evidence_id = getattr(rel, "evidence_public_id", None) or getattr(rel, "evidence_id", None)
        evidence = evidence_by_id.get(evidence_id)
        if not target or evidence is None or not evidence.snippet:
            continue
        call = _call_expression(evidence.snippet, getattr(rel, "target_name", "") or "")
        if not call:
            continue
        entry = {"caller": getattr(rel, "source_name", None) or "", "call": call}
        if entry not in sites.setdefault(target, []):
            sites[target].append(entry)
    return sites


def _call_expression(text: str, name: str) -> str | None:
    if not name:
        return None
    match = re.search(rf"(?:[A-Za-z_]\w*\.)*{re.escape(name)}\s*\(", text)
    if not match:
        return None
    depth = 0
    for index in range(match.end() - 1, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return " ".join(text[match.start() : index + 1].split())
    return None


# --- text helpers ---------------------------------------------------------------------


def _when(guard: str | None) -> str:
    return f" when {_code(guard)}" if guard else ""


def _return_expr(text: str) -> str:
    return _short(re.sub(r"^(?:return|yield)\b\s*", "", text).rstrip(";").strip() or "nothing")


def _condition(text: str) -> str:
    cleaned = re.sub(r"^(?:\}\s*)?(?:else\s+if|elif|if|while|for|switch|case|match|when|unless|guard)\b\s*", "", text)
    cleaned = cleaned.rstrip("{:").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1].strip()
    return _short(cleaned)


_ASSIGN = re.compile(r"^(?:(?:const|let|var|final|val)\s+)?(?P<lhs>[A-Za-z_][\w.\[\]'\"]*)\s*(?::[^=]+)?(?::=|(?<![=!<>])=(?!=))\s*(?P<rhs>.+)$")
_KEYED = re.compile(r"^[\"']?(?P<lhs>[A-Za-z_][\w-]*)[\"']?\s*:\s*(?P<rhs>.+)$")


def _value_key(text: str | None) -> str | None:
    if not text:
        return None
    match = _ASSIGN.match(text.rstrip(";,")) or _KEYED.match(text.rstrip(";,"))
    return match.group("lhs") if match else None


def _rhs(text: str) -> str:
    match = _ASSIGN.match(text.rstrip(";,")) or _KEYED.match(text.rstrip(";,"))
    return _short(match.group("rhs").strip()) if match else _short(text)


def _args(text: str, name: str) -> str:
    expr = _call_expression(text, name.split(".")[-1])
    if not expr:
        return "different arguments"
    start = expr.find("(")
    return _short(expr[start + 1 : -1].strip() or "no arguments")


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= EXPR_CAP else text[: EXPR_CAP - 1] + "…"


def _names(names: list[str], limit: int = 3) -> str:
    shown = [_code(name) for name in names[:limit]]
    text = ", ".join(shown)
    if len(names) > limit:
        text += f" and {len(names) - limit} more"
    return text


def _code(text: str | None) -> str:
    if not text:
        return ""
    fence = "``" if "`" in text else "`"
    pad = " " if fence == "``" else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _kind(claim) -> str:
    return getattr(claim, "kind", "") or ""


def _id(item):
    return getattr(item, "public_id", None) or getattr(item, "id", None)
