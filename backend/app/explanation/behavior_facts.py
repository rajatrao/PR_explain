"""Behavior facts for the explanation packet and the Behavioral Changes section.

Each changed function becomes one fact block: its name (public or not), the
entry points that reach it through stored CALLS edges at the head commit, the
call expressions those callers use at head, its tests, and its before/after
statement pairs from the diff (stored ``behavior_changed`` claims and their
``behavior_before`` / ``behavior_after`` evidence, with the enclosing
condition on each side). A signature change is also checked against the head
call sites by argument count. Nothing else goes in.
"""

from __future__ import annotations

import re

from app.analyzer.behavior import _language, _params
from app.explanation.behavior_comparison import build_behavior_comparison
from app.explanation.explain_view import explain_skip_symbol
from app.explanation.schema import BehaviorChangeFact, BehaviorFunctionFact

FUNCTION_CAP = 24
CHANGE_CAP = 8
CALL_SITE_CAP = 5
CHAR_BUDGET = 14000
TEXT_CAP = 160


def build_behavior_facts(*, symbols, relationships, claims, evidences) -> list[BehaviorFunctionFact]:
    comparison = build_behavior_comparison(
        symbols=symbols, relationships=relationships, claims=claims, evidences=evidences
    )
    evidence_by_id = {_id(item): item for item in evidences or []}
    sites = _call_sites(relationships, evidence_by_id)
    items = sorted(
        comparison.get("items") or [],
        key=lambda item: (
            not _is_public(item),
            -len(item["reach"].get("entry_points") or []),
            -len(item["changes"]),
        ),
    )
    facts: list[BehaviorFunctionFact] = []
    counter = 0
    used = 0
    for item in items[:FUNCTION_CAP]:
        changes: list[BehaviorChangeFact] = []
        for change in item["changes"][:CHANGE_CAP]:
            counter += 1
            changes.append(
                BehaviorChangeFact(
                    id=f"b{counter}",
                    claim_id=change["claim_id"],
                    kind=change["category"],
                    before=_cap(change.get("before")),
                    after=_cap(change.get("after")),
                    before_when=_cap(change.get("before_when")),
                    after_when=_cap(change.get("after_when")),
                )
            )
        if not changes:
            continue
        head_sites = sites.get(item.get("symbol_id"), [])[:CALL_SITE_CAP]
        notes = _contract_notes(item, head_sites)
        fact = BehaviorFunctionFact(
            function=item["name"] or item["file"],
            file=item["file"],
            public=_is_public(item),
            removed=item["removed"],
            reached_from=_unique(item["reach"].get("entry_points") or [])[:8],
            callers_at_head=[f"{site['caller']} → {site['call']}" for site in head_sites],
            tests=(item["reach"].get("tests") or [])[:4],
            notes=notes,
            changes=changes,
        )
        size = len(fact.model_dump_json())
        if facts and used + size > CHAR_BUDGET:
            break
        used += size
        facts.append(fact)
    return facts


def _is_public(item: dict) -> bool:
    name = item.get("name")
    return bool(name) and not explain_skip_symbol(name, item.get("file"))


def _contract_notes(item: dict, sites: list[dict]) -> list[str]:
    """Argument counts at head call sites compared with the new signature's parameters."""
    signature = next((c for c in item["changes"] if c["category"] == "signature" and c.get("after")), None)
    if signature is None or not sites:
        return []
    specs = _param_specs(signature["after"], item["file"])
    if specs is None:
        return []
    required = sum(1 for _raw, has_default, variadic in specs if not has_default and not variadic)
    variadic = any(v for _r, _d, v in specs)
    mismatched = []
    for site in sites:
        count = _arg_count(site["call"])
        if count is None:
            return []
        if count < required or (not variadic and count > len(specs)):
            mismatched.append(f"{site['caller']} passes {count} argument{'s' if count != 1 else ''}")
    name = item["name"]
    if not mismatched:
        return [f"All {len(sites)} head call site{'s' if len(sites) != 1 else ''} of {name} pass a matching number of arguments."]
    return [f"{name} now declares {required} required parameter{'s' if required != 1 else ''}; " + "; ".join(mismatched) + "."]


def param_delta(before: str, after: str, path: str) -> tuple[list[tuple[str, bool]], list[str], list[tuple[str, str, str]]]:
    """(added params with optional flag, removed params, changed defaults) between two header lines."""
    language = _language(path)
    old = _params(before, language)
    new = _params(after, language)
    specs = [spec for spec in (_param_specs(after, path) or [])]
    old_names = [name for name, _ in old]
    new_names = [name for name, _ in new]
    added = []
    for index, (name, _default) in enumerate(new):
        if name in old_names:
            continue
        optional = index < len(specs) and (specs[index][1] or specs[index][2])
        added.append((name, bool(optional)))
    removed = [name for name in old_names if name not in new_names]
    old_defaults = dict(old)
    defaults = [
        (name, old_defaults[name] or "none", default or "none")
        for name, default in new
        if name in old_defaults and old_defaults[name] != default and (default or old_defaults[name])
    ]
    return added, removed, defaults


# --- call sites --------------------------------------------------------------------


def _call_sites(relationships, evidence_by_id) -> dict[str, list[dict]]:
    sites: dict[str, list[dict]] = {}
    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        target = getattr(rel, "target_public_id", None) or getattr(rel, "target_id", None)
        evidence_id = getattr(rel, "evidence_public_id", None) or getattr(rel, "evidence_id", None)
        evidence = evidence_by_id.get(evidence_id)
        if not target or evidence is None or not evidence.snippet:
            continue
        call = call_expression(evidence.snippet, getattr(rel, "target_name", "") or "")
        if not call:
            continue
        entry = {"caller": getattr(rel, "source_name", None) or "", "call": call}
        if entry not in sites.setdefault(target, []):
            sites[target].append(entry)
    return sites


def call_expression(text: str, name: str) -> str | None:
    """`name(...)` as written on the line, with balanced parentheses."""
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
        if not piece or piece in {"self", "cls", "/", "*"}:
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
    if inner.startswith("*") or "..." in inner:
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


def _cap(text: str | None) -> str | None:
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= TEXT_CAP else text[: TEXT_CAP - 1] + "…"


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _id(item):
    return getattr(item, "public_id", None) or getattr(item, "id", None)
