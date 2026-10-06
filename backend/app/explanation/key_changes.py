"""Key Changes and Callers outside the diff, for the Details tab.

Key Changes: one row per changed function, ranked by what a reviewer must check first
(removed, then changed inputs, then changed failures, then changed results, then the rest).
Each row says what changed for a caller of that function, how many callers outside this
diff still call it, and whether any test references it.

Callers outside the diff: one row per file that the diff does not touch but that calls a
changed function. Each row shows the call as written at the head commit and the facts that
decide whether that call still fits: a newly required input it does not pass, a changed
failure it does not catch, a changed result it receives.

Every statement comes from stored facts (behavior claims, CALLS edges with their source
line, call_context claims). Nothing is inferred beyond counting the arguments of a call.
"""

from __future__ import annotations

import re

from app.analyzer.behavior import CATEGORY_ORDER
from app.explanation.behavior_comparison import build_behavior_comparison
from app.explanation.behavior_facts import call_expression, param_delta
from app.explanation.explain_view import explain_skip_symbol
from app.analyzer.parse import is_test_path

KEY_LIMIT = 6
OUTSIDE_LIMIT = 12

# Categories that change what a caller sees, in the order a reviewer should check them.
_CALLER_FACING = ("removed_function", "signature", "error", "return")
_CATEGORY_WORD = {
    "removed_function": "removed",
    "signature": "inputs",
    "error": "failures",
    "return": "result",
    "condition": "branching",
    "value": "values",
    "call": "downstream calls",
    "logging": "logging",
    "logic": "logic",
}


# --- shared facts ---------------------------------------------------------------------------


class _Facts:
    """Facts the Key Changes and outside-caller rows are built from: the behavior comparison per changed function, files in the diff, call contexts, removed functions still called, and stored CALLS edges."""

    def __init__(self, *, symbols, relationships, claims, evidences, repo, sha) -> None:
        comparison = build_behavior_comparison(
            symbols=symbols or [],
            relationships=relationships or [],
            claims=claims or [],
            evidences=evidences or [],
            repo=repo,
            head_sha=sha,
        )
        self.items = {item["name"]: item for item in comparison.get("items") or [] if item.get("name")}
        self.evidence_by_id = {_id(e): e for e in evidences or []}
        self.repo, self.sha = repo, sha
        self.diff_files = {
            getattr(c, "subject", None) for c in claims or [] if getattr(c, "kind", None) == "file_changed"
        }
        self.outside_files = {
            getattr(c, "subject", None)
            for c in claims or []
            if getattr(c, "kind", None) in {"file_reason", "reaches_changed"}
        }
        self.contexts = _contexts(claims, self.evidence_by_id)
        self.relationships = relationships or []
        self.base_sha = _base_sha(evidences)
        # Head call sites that still call a function this pull request removes.
        self.dangling: dict[str, list[dict]] = {}
        for claim in claims or []:
            if getattr(claim, "kind", None) != "dangling_call":
                continue
            for evidence_id in getattr(claim, "evidence_public_ids", None) or getattr(claim, "evidence_ids", None) or []:
                evidence = self.evidence_by_id.get(evidence_id)
                if evidence is None:
                    continue
                self.dangling.setdefault(getattr(claim, "subject", "") or "", []).append(
                    {
                        "location": f"{evidence.file}:{evidence.start_line}",
                        "href": _blob(repo, sha, evidence.file, evidence.start_line),
                    }
                )

    def calls_into(self, name: str) -> list[dict]:
        """Stored CALLS edges into ``name`` from files outside the diff, with the call as written."""
        out: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for rel in self.relationships:
            if getattr(rel, "type", None) != "CALLS" or getattr(rel, "target_name", None) != name:
                continue
            source_file = getattr(rel, "source_file", None)
            if not source_file or source_file in self.diff_files:
                continue
            caller = getattr(rel, "source_name", "") or ""
            if (source_file, caller) in seen:
                continue
            seen.add((source_file, caller))
            evidence = self.evidence_by_id.get(getattr(rel, "evidence_public_id", None) or getattr(rel, "evidence_id", None))
            call = call_expression(evidence.snippet, name) if evidence is not None and evidence.snippet else None
            line = getattr(evidence, "start_line", None) if evidence is not None else None
            out.append(
                {
                    "file": source_file,
                    "caller": caller,
                    "call": call,
                    "line": line,
                    "href": _blob(self.repo, self.sha, source_file, line),
                }
            )
        return out


# --- Key Changes ---------------------------------------------------------------------------


def build_key_rows(*, symbols, relationships, claims, evidences, repo, sha, fallback_rows: list[dict]) -> list[dict]:
    """Rows for Key Changes. ``fallback_rows`` are the plain changed-symbol rows (name → location),
    already filtered for tests, private helpers, and dunders; functions with no behavior fact keep
    that row so nothing changed disappears from the section."""
    facts = _Facts(symbols=symbols, relationships=relationships, claims=claims, evidences=evidences, repo=repo, sha=sha)
    exported = {getattr(c, "subject", None) for c in claims or [] if getattr(c, "kind", None) == "defines_api"}
    ranked: list[tuple[tuple, dict]] = []
    covered: set[str] = set()
    for row in fallback_rows:
        name = row.get("label") or ""
        item = facts.items.get(name)
        if item is None or not item.get("changes"):
            continue
        covered.add(name)
        ranked.append((_rank(item, name in exported), _key_row(item, facts, row.get("href"))))
    for name, item in facts.items.items():
        # Private helpers and dunder methods are left out of Key Changes, removed or not.
        if explain_skip_symbol(name, item.get("file")) or is_test_path(item.get("file") or ""):
            continue
        if item.get("removed") and name not in covered:
            covered.add(name)
            item = {**item, "dangling": bool(facts.dangling.get(name))}
            ranked.append((_rank(item, True), _key_row(item, facts, item.get("href"))))
    ranked.sort(key=lambda pair: pair[0])
    rows = [row for _, row in ranked]
    for row in fallback_rows:
        if row.get("label") in covered or row.get("value") == "none found":
            continue
        rows.append(
            {
                "label": row["label"],
                "value": f"Changed at {row.get('value')}; the diff shows no caller-visible behavior change.",
                "href": None,
                "label_href": row.get("href"),
            }
        )
    if not rows:
        return [{"label": "Changed", "value": "none found", "href": None}]
    hidden = len(rows) - KEY_LIMIT
    rows = rows[:KEY_LIMIT]
    if hidden > 0:
        rows.append({"label": "More", "value": f"{hidden} more changed function{'s' if hidden != 1 else ''}.", "href": None})
    return rows


def _rank(item: dict, public: bool) -> tuple:
    best = min((CATEGORY_ORDER.get(c["category"], 9) for c in item["changes"]), default=9)
    if item.get("removed"):
        best = -1
    if item.get("dangling"):
        best = -2
    outside = item["reach"].get("outside_diff", 0) if item.get("reach") else 0
    return (best, not public, -outside, item["name"])


def _removed_row(item: dict, facts: _Facts) -> dict:
    """What a reviewer needs about a removed function: what it was, and whether anything still calls it."""
    name = item["name"]
    change = next((c for c in item["changes"] if c["category"] == "removed_function"), None)
    header = (change or {}).get("before") or ""
    line = None
    if change and change.get("before_location") and ":" in change["before_location"]:
        line = change["before_location"].rsplit(":", 1)[1]
    parts = [f"Removed from {item.get('file')}" + (f" (base line {line})" if line else "") + "."]
    if header:
        parts.append(f"It was `{header.rstrip(' {:')}`.")
    dangling = facts.dangling.get(name, [])
    if dangling:
        where = ", ".join(site["location"] for site in dangling[:3]) + (f" and {len(dangling) - 3} more" if len(dangling) > 3 else "")
        parts.append(
            f"Still called at the head commit in {where}; "
            f"{'that call no longer resolves' if len(dangling) == 1 else 'those calls no longer resolve'} to a definition."
        )
    else:
        parts.append("No plain call to it remains in the analyzed files at the head commit.")
    href = None
    if line and facts.repo and facts.base_sha:
        href = _blob(facts.repo, facts.base_sha, item.get("file"), int(line))
    return {
        "label": f"{name} (removed)",
        "value": " ".join(parts),
        "href": dangling[0]["href"] if dangling else None,
        "label_href": href,
    }


def _key_row(item: dict, facts: _Facts, href: str | None) -> dict:
    name = item["name"]
    if item.get("removed"):
        return _removed_row(item, facts)
    label = f"{name} (removed)" if item.get("removed") else f"{name} (new)" if _is_new(item) else name
    parts: list[str] = []
    categories = _ordered_categories(item)
    if not item.get("removed") and not _is_new(item):
        parts.append("Changes its " + _join([_CATEGORY_WORD[c] for c in categories]) + ".")
    lead = "Removed by this pull request." if item.get("removed") else _lead(item)
    if lead:
        parts.append(lead)
    calls = facts.calls_into(name)
    files = sorted({call["file"] for call in calls})
    if calls:
        contract = item.get("removed") or "signature" in categories
        verb = "still call it and are not part of this diff" if contract else "call it from outside this diff"
        parts.append(f"{len(calls)} caller{'s' if len(calls) != 1 else ''} in {len(files)} file{'s' if len(files) != 1 else ''} {verb}.")
    elif not _is_new(item):
        parts.append("No stored caller outside this diff.")
    if not item.get("removed"):
        tests = (item.get("reach") or {}).get("tests") or []
        parts.append(f"Tested by {_join(tests[:2])}." if tests else "No test references it.")
    return {"label": label, "value": " ".join(parts), "href": None, "label_href": href or item.get("href")}


def _ordered_categories(item: dict) -> list[str]:
    seen: list[str] = []
    for change in sorted(item["changes"], key=lambda c: CATEGORY_ORDER.get(c["category"], 9)):
        if change["category"] in _CATEGORY_WORD and change["category"] not in seen:
            seen.append(change["category"])
    return seen


def _lead(item: dict) -> str:
    """The first sentence of the most caller-facing change, as the analyzer worded it."""
    for category in _CALLER_FACING:
        change = next((c for c in item["changes"] if c["category"] == category and c.get("summary")), None)
        if change:
            return _first_sentence(change["summary"])
    change = next((c for c in item["changes"] if c.get("summary")), None)
    return _first_sentence(change["summary"]) if change else ""


# --- Callers outside the diff -----------------------------------------------------------------


def outside_calls(*, symbols, relationships, claims, evidences, repo, sha) -> tuple[list[dict], list[str]]:
    """Every stored call from a file outside the diff into a changed function, with its findings.

    Returns (calls sorted most-severe first, files that reach changed code only indirectly).
    Each call: file, line, caller, callee, call, href, notes, severity (0 = check first), flags, item.
    """
    facts = _Facts(symbols=symbols, relationships=relationships, claims=claims, evidences=evidences, repo=repo, sha=sha)
    calls: list[dict] = []
    direct_files: set[str] = set()
    for name, item in facts.items.items():
        if not item.get("changes"):
            continue
        for call in facts.calls_into(name):
            direct_files.add(call["file"])
            flags: dict = {}
            notes, severity = _call_notes(item, call, facts, flags)
            calls.append({**call, "callee": name, "notes": notes, "severity": severity, "flags": flags, "item": item})
    calls.sort(key=lambda c: (c["severity"], c["file"], c["line"] or 0))
    indirect = sorted(path for path in facts.outside_files if path and path not in direct_files)
    return calls, indirect


def build_outside_rows(*, symbols, relationships, claims, evidences, repo, sha) -> list[dict]:
    calls, indirect = outside_calls(
        symbols=symbols, relationships=relationships, claims=claims, evidences=evidences, repo=repo, sha=sha
    )
    ranked: list[tuple[tuple, dict]] = []
    for call in calls:
        name, notes, severity = call["callee"], call["notes"], call["severity"]
        shown = f"`{call['call']}`" if call["call"] else name
        value = f"{call['caller'] or 'Code here'} calls {shown}" + (f" at line {call['line']}." if call["line"] else ".")
        if notes:
            value += " " + " ".join(notes)
        ranked.append(
            (
                (severity, call["file"], call["line"] or 0),
                {"label": call["file"], "value": value, "href": None, "label_href": call["href"]},
            )
        )
    ranked.sort(key=lambda pair: pair[0])
    rows = [row for _, row in ranked[:OUTSIDE_LIMIT]]
    if len(ranked) > OUTSIDE_LIMIT:
        extra = len(ranked) - OUTSIDE_LIMIT
        rows.append({"label": "More", "value": f"{extra} more call{'s' if extra != 1 else ''} from outside the diff.", "href": None})
    if indirect:
        shown = ", ".join(indirect[:5]) + (f" and {len(indirect) - 5} more" if len(indirect) > 5 else "")
        rows.append(
            {
                "label": "Indirect",
                "value": f"{len(indirect)} more file{'s' if len(indirect) != 1 else ''} reach the changed code through another function: {shown}.",
                "href": None,
            }
        )
    return rows or [{"label": "Outside the diff", "value": "none found", "href": None}]


def _call_notes(item: dict, call: dict, facts: _Facts, flags: dict | None = None) -> tuple[list[str], int]:
    """Facts that decide whether this call still fits, and a sort key (lower = check first).

    ``flags`` (when passed) receives the structured findings: missing, passed, optional,
    defaults, removed, error, handled, returns.
    """
    flags = flags if flags is not None else {}
    notes: list[str] = []
    severity = 5
    name = item["name"]
    signature = next((c for c in item["changes"] if c["category"] == "signature" and c.get("before") and c.get("after")), None)
    if signature and call["call"]:
        added, removed, defaults = param_delta(signature["before"], signature["after"], item.get("file") or "")
        missing = _missing_required(call["call"], signature["after"], added, item.get("file") or "")
        flags["missing"] = missing
        if missing:
            notes.append(f"{name} now requires {_join(missing)}; this call does not pass {'it' if len(missing) == 1 else 'them'}.")
            severity = min(severity, 0)
        passed = [param for param, optional in added if not optional and param not in missing and _countable(call["call"])]
        flags["passed"] = passed
        if passed:
            notes.append(f"{name} now requires {_join(passed)}; this call passes {'it' if len(passed) == 1 else 'them'}.")
            severity = min(severity, 4)
        optional = [param for param, opt in added if opt]
        flags["optional"] = optional
        if optional:
            notes.append(f"{_join(optional)} {'is' if len(optional) == 1 else 'are'} optional, so this call gets the default.")
            severity = min(severity, 3)
        for param, old, new in defaults:
            if _countable(call["call"]) and not _passes_param(call["call"], signature["after"], param, item.get("file") or ""):
                notes.append(f"The default of {param} changes from {old} to {new}, and this call relies on it.")
                flags.setdefault("defaults", []).append((param, old, new))
                severity = min(severity, 2)
        if removed:
            flags["removed"] = removed
            notes.append(f"{name} no longer takes {_join(removed)}.")
            severity = min(severity, 1)
    error = next((c for c in item["changes"] if c["category"] == "error" and c.get("summary")), None)
    if error:
        handled = facts.contexts.get((call["caller"], name))
        flags["error"] = _first_sentence(error["summary"])
        flags["handled"] = handled
        wrap = "" if handled is None else (" The call is inside a try block." if handled else " The call is not inside a try block.")
        notes.append(_first_sentence(error["summary"]) + wrap)
        severity = min(severity, 1 if handled is False else 2)
    if any(c["category"] == "return" for c in item["changes"]):
        flags["returns"] = True
        notes.append(f"The value {name} returns changed.")
        severity = min(severity, 3)
    if not notes:
        words = [_CATEGORY_WORD[c] for c in _ordered_categories(item)]
        if words:
            notes.append(f"{name} changes its {_join(words)}; the call itself is unchanged.")
    return notes, severity


def _missing_required(call: str, header: str, added: list[tuple[str, bool]], path: str) -> list[str]:
    """Newly required parameters this call does not pass, positionally or by keyword.

    Returns [] when the call spreads arguments, since the count is then unknown.
    """
    required = [param for param, optional in added if not optional]
    if not required:
        return []
    args = _arguments(call)
    if args is None or any(arg.startswith(("*", "...")) for arg in args):
        return []
    return [param for param in required if not _passes_param(call, header, param, path)]


def _passes_param(call: str, header: str, param: str, path: str) -> bool:
    """True when the call passes ``param`` by keyword or by position."""
    if _passes(call, param):
        return True
    from app.analyzer.behavior import _language, _params

    order = [name for name, _ in _params(header, _language(path))]
    positional = [arg for arg in _arguments(call) or [] if not re.match(r"^\w+\s*=(?!=)", arg)]
    return param in order and len(positional) > order.index(param)


def _countable(call: str) -> bool:
    args = _arguments(call)
    return args is not None and not any(arg.startswith(("*", "...")) for arg in args)


def _passes(call: str, param: str) -> bool:
    return re.search(rf"[(,]\s*{re.escape(param)}\s*[=:](?!=)", call) is not None


def _arguments(call: str) -> list[str] | None:
    start = call.find("(")
    if start < 0 or not call.endswith(")"):
        return None
    inner = call[start + 1 : -1]
    args: list[str] = []
    depth = 0
    current = ""
    quote = None
    for char in inner:
        if quote:
            current += char
            if char == quote:
                quote = None
            continue
        if char in "'\"`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        args.append(current.strip())
    return args


# --- helpers ----------------------------------------------------------------------------------


def _is_new(item: dict) -> bool:
    signature = next((c for c in item["changes"] if c["category"] == "signature"), None)
    return bool(signature and signature.get("after") and not signature.get("before"))


def _contexts(claims, evidence_by_id) -> dict[tuple[str, str], bool]:
    out: dict[tuple[str, str], bool] = {}
    for claim in claims or []:
        if getattr(claim, "kind", None) != "call_context":
            continue
        ids = getattr(claim, "evidence_public_ids", None) or getattr(claim, "evidence_ids", None) or []
        for evidence_id in ids:
            evidence = evidence_by_id.get(evidence_id)
            parts = (evidence.description or "").split("|") if evidence is not None else []
            if len(parts) == 5:
                out[(parts[1], parts[2])] = parts[3] == "handled"
    return out


def _first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return match.group(1) if match else text


def _join(items: list[str]) -> str:
    items = [item for item in items if item]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _base_sha(evidences) -> str | None:
    return next((e.commit_sha for e in evidences or [] if getattr(e, "type", None) == "behavior_before"), None)


def _blob(repo, sha, path, line) -> str | None:
    if not (repo and sha and path):
        return None
    return f"https://github.com/{repo}/blob/{sha}/{path}" + (f"#L{line}" if line else "")


def _id(item):
    return getattr(item, "public_id", None) or getattr(item, "id", None)
