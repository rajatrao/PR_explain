"""System Behavior Change: what the system does differently after this pull request.

Every sentence is assembled from stored facts and nothing else:

* ``behavior_changed`` claims and their base/head evidence (the ``-`` and ``+``
  statements of the diff, plus the condition that encloses each one on the
  same side),
* stored CALLS edges and their call-site evidence at the head commit (who
  reaches the changed code and what they pass),
* stored TESTS edges, and ``behavior_unchanged`` claims for files no call path
  reaches.

Statements are phrased per changed function as Before / Now pairs. Statement
changes that do not fit a known shape (plain logic lines) are counted, not
paraphrased.
"""

from __future__ import annotations

import re

from app.analyzer.behavior import call_names, error_target
from app.explanation.behavior_flow import _header_label

FUNCTION_CAP = 12
ROW_CAP = 8
CALL_SITE_CAP = 6
EXPR_CAP = 70


def build_system_behavior(comparison: dict, *, relationships, evidences, claims) -> dict:
    items = comparison.get("items") or []
    if not items:
        return {"headline": "", "changes": [], "unaffected": "", "not_summarized": 0}
    evidence_by_id = {_id(item): item for item in evidences or []}
    call_sites = _call_sites(relationships, evidence_by_id)

    changes: list[dict] = []
    skipped = 0
    for item in items[:FUNCTION_CAP]:
        rows, dropped = _rows(item)
        skipped += dropped
        if not rows:
            continue
        reach = item.get("reach") or {}
        changes.append(
            {
                "subject": item["display_name"],
                "file": item["file"],
                "exported": item["exported"],
                "removed": item["removed"],
                "rows": rows,
                "entry_points": _unique(reach.get("entry_points") or []),
                "reached_through": _unique([c["name"] for c in reach.get("callers") or [] if c["depth"] == 1]),
                "call_sites": call_sites.get(item.get("symbol_id"), [])[:CALL_SITE_CAP],
                "tests": reach.get("tests") or [],
            }
        )
    skipped += sum(len(item["changes"]) for item in items[FUNCTION_CAP:])
    unchanged = sum(1 for claim in claims or [] if getattr(claim, "kind", None) == "behavior_unchanged")
    return {
        "headline": _headline(changes),
        "changes": changes,
        "unaffected": (
            f"No stored call or import path reaches the changed code from {unchanged} other "
            f"file{'s' if unchanged != 1 else ''}, so their behavior is unchanged."
            if unchanged
            else ""
        ),
        "not_summarized": skipped,
    }


def render_system_behavior_markdown(summary: dict) -> str:
    changes = summary.get("changes") or []
    if not changes:
        return ""
    lines = ["### System Behavior Change", "", summary.get("headline") or ""]
    for change in changes:
        lines.append("")
        title = f"#### {_code(change['subject'])} · {change['file']}"
        if change["removed"]:
            title += " · removed"
        lines.append(title)
        lines.append("")
        lines.append("| Before this PR | After this PR |")
        lines.append("|---|---|")
        for row in change["rows"]:
            lines.append(f"| {_cell(row['before'])} | {_cell(row['now'])} |")
        reach = _reach_sentence(change)
        if reach:
            lines.append("")
            lines.append(reach)
    tail = []
    if summary.get("unaffected"):
        tail.append(summary["unaffected"])
    if summary.get("not_summarized"):
        n = summary["not_summarized"]
        tail.append(f"{n} other statement change{'s are' if n != 1 else ' is'} in the diff but not summarized here.")
    if tail:
        lines.extend(["", " ".join(tail)])
    return "\n".join(lines).strip()


# --- rows ------------------------------------------------------------------------


def _rows(item: dict) -> tuple[list[dict], int]:
    subject = _code(item["display_name"])
    changes = item["changes"]
    guards = {c.get("after_when") for c in changes} | {c.get("before_when") for c in changes}
    rows: list[dict] = []
    dropped = 0
    for change in changes:
        row = _row(subject, item, change, guards)
        if row is None:
            dropped += 1
            continue
        if row not in rows:
            rows.append(row)
    if len(rows) > ROW_CAP:
        dropped += len(rows) - ROW_CAP
        rows = rows[:ROW_CAP]
    return rows, dropped


def _row(subject: str, item: dict, change: dict, guards: set) -> dict | None:
    category = change["category"]
    before, after = change.get("before"), change.get("after")
    b_when, a_when = change.get("before_when"), change.get("after_when")
    name = item["display_name"]

    if category == "removed_function":
        return {"before": f"{subject} is defined in {item['file']}.", "now": f"{subject} is no longer defined."}

    if category == "signature":
        if not before or not after:
            return None
        old = _header_label(before, name, item["file"])
        new = _header_label(after, name, item["file"])
        if old == new:
            return {"before": f"{subject} has the signature `{_short(before)}`.", "now": f"Its signature is `{_short(after)}`."}
        return {"before": f"Callers invoke {_code(old)}.", "now": f"Callers must invoke {_code(new)}."}

    if category == "error":
        if before and after:
            return {
                "before": f"{subject} raises {error_target(before)}{_when(b_when)}.",
                "now": f"It raises {error_target(after)}{_when(a_when)}.",
            }
        if after:
            return {"before": f"{subject} does not raise {error_target(after)}.", "now": f"It raises {error_target(after)}{_when(a_when)}."}
        return {"before": f"{subject} raises {error_target(before)}{_when(b_when)}.", "now": f"It no longer raises {error_target(before)}."}

    if category == "return":
        old_expr = _return_expr(before) if before else None
        new_expr = _return_expr(after) if after else None
        if before and after:
            return {
                "before": f"{subject} returns {_code(old_expr)}{_when(b_when)}.",
                "now": f"It returns {_code(new_expr)}{_when(a_when)}.",
            }
        if after:
            return {
                "before": f"{subject} has no return of {_code(new_expr)}{_when(a_when)}.",
                "now": f"It returns {_code(new_expr)}{_when(a_when)}.",
            }
        return {"before": f"{subject} returns {_code(old_expr)}{_when(b_when)}.", "now": "That return statement is removed."}

    if category == "condition":
        old_cond = _condition(before) if before else None
        new_cond = _condition(after) if after else None
        # A condition that only guards a raise/return above is already stated there.
        if (old_cond is None or old_cond in guards) and (new_cond is None or new_cond in guards):
            return None
        if before and after:
            return {"before": f"{subject} checks {_code(old_cond)}.", "now": f"It checks {_code(new_cond)}."}
        if after:
            return {"before": f"{subject} has no check on {_code(new_cond)}.", "now": f"It checks {_code(new_cond)}."}
        return {"before": f"{subject} checks {_code(old_cond)}.", "now": "That check is removed."}

    if category in {"call", "logging"}:
        old_calls = call_names(before or "")
        new_calls = call_names(after or "")
        gained = [c for c in new_calls if c not in old_calls]
        lost = [c for c in old_calls if c not in new_calls]
        if gained and not lost:
            return {
                "before": f"{subject} does not call {_names(gained)}{_when(a_when)}.",
                "now": f"It calls {_names(gained)}{_when(a_when)}.",
            }
        if lost and not gained:
            return {"before": f"{subject} calls {_names(lost)}{_when(b_when)}.", "now": f"It no longer calls {_names(lost)}."}
        if gained and lost:
            return {"before": f"{subject} calls {_names(lost)}{_when(b_when)}.", "now": f"It calls {_names(gained)} instead{_when(a_when)}."}
        shared = [c for c in new_calls if c in old_calls]
        if shared and before and after:
            target = shared[0]
            return {
                "before": f"{subject} calls {_code(target)} with {_code(_args(before, target))}.",
                "now": f"It calls {_code(target)} with {_code(_args(after, target))}.",
            }
        return None

    if category == "value":
        key = _value_key(after or before)
        if not key:
            return None
        if before and after:
            return {"before": f"{_code(key)} is {_code(_rhs(before))}{_when(b_when)}.", "now": f"{_code(key)} is {_code(_rhs(after))}{_when(a_when)}."}
        if after:
            return {"before": f"{subject} does not set {_code(key)} here.", "now": f"{_code(key)} is set to {_code(_rhs(after))}{_when(a_when)}."}
        return {"before": f"{_code(key)} is set to {_code(_rhs(before))}{_when(b_when)}.", "now": f"{subject} no longer sets {_code(key)}."}

    return None


# --- reach ---------------------------------------------------------------------------


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
        entry = {
            "caller": getattr(rel, "source_name", None) or "",
            "file": getattr(rel, "source_file", None) or "",
            "line": evidence.start_line,
            "call": call,
        }
        if entry not in sites.setdefault(target, []):
            sites[target].append(entry)
    return sites


def _reach_sentence(change: dict) -> str:
    parts: list[str] = []
    entries = change.get("entry_points") or []
    through = change.get("reached_through") or []
    if entries:
        parts.append(f"**Reached from:** {_names(entries, limit=6)} (entry point{'s' if len(entries) != 1 else ''}).")
    elif through:
        parts.append(f"**Reached from:** {_names(through, limit=6)}.")
    elif not change.get("removed"):
        parts.append("**Reached from:** no stored caller.")
    sites = change.get("call_sites") or []
    if sites:
        shown = "; ".join(f"{_code(site['caller'])} → {_code(site['call'])}" for site in sites)
        parts.append(f"**Callers at head:** {shown}.")
    if not change.get("removed"):
        tests = change.get("tests") or []
        parts.append(f"**Tests:** {', '.join(tests[:4])}." if tests else "**Tests:** none reference it.")
    return " ".join(parts)


def _headline(changes: list[dict]) -> str:
    if not changes:
        return ""
    functions = len(changes)
    entries = _unique([name for change in changes for name in change["entry_points"]])
    removed = sum(1 for change in changes if change["removed"])
    untested = sum(1 for change in changes if not change["removed"] and not change["tests"])
    text = (
        "This pull request changes what 1 function does"
        if functions == 1
        else f"This pull request changes what {functions} functions do"
    )
    if entries:
        text += f", and that behavior is reached from {len(entries)} entry point{'s' if len(entries) != 1 else ''}: {_names(entries, limit=6)}"
    text += "."
    if removed:
        text += f" {removed} function{'s are' if removed != 1 else ' is'} removed."
    if untested:
        text += f" {untested} changed function{'s have' if untested != 1 else ' has'} no stored test reference."
    return text


# --- text helpers ----------------------------------------------------------------------


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


def _call_expression(text: str, name: str) -> str | None:
    """`name(...)` as written on the line, with balanced parentheses."""
    if not name:
        return None
    match = re.search(rf"(?:[A-Za-z_][\w]*\.)*{re.escape(name)}\s*\(", text)
    if not match:
        return None
    depth = 0
    for index in range(match.end() - 1, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return _short(text[match.start() : index + 1])
    return None


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


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _id(item):
    return getattr(item, "public_id", None) or getattr(item, "id", None)
