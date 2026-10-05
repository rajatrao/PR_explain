"""Change flow for the Details tab: one entry per flow a reviewer should trace.

A flow is an entry point and the call path from it to changed code (stored CALLS edges at
the head commit). For each flow the rows say:

* What changes   the before/after summaries of the changed function on that path,
* Call at head   the call expression the nearest caller writes, linked to its line,
* Errors         a new or changed failure on the path, and whether the caller wraps the call
                 in a try block,
* Tests          the tests that reference the changed function, or that none do.

Changed functions that no stored caller reaches are listed once at the end. Private helpers
are folded into the flow of the public function that calls them. Everything comes from
stored facts; nothing is inferred.
"""

from __future__ import annotations

from app.explanation.behavior_comparison import build_behavior_comparison
from app.explanation.explain_view import explain_skip_symbol

FLOW_CAP = 8
SUMMARY_CAP = 3


def build_flow_rows(*, symbols, relationships, claims, evidences, repo: str | None, sha: str | None) -> list[dict]:
    comparison = build_behavior_comparison(
        symbols=symbols, relationships=relationships, claims=claims, evidences=evidences, repo=repo, head_sha=sha
    )
    items = [item for item in comparison.get("items") or [] if item.get("changes") and item.get("name")]
    if not items:
        return [_row("Change flow", "No behavior change was found in the changed functions.", None)]
    evidence_by_id = {_id(e): e for e in evidences or []}
    call_sites = _call_sites(relationships, evidence_by_id, repo, sha)
    contexts = _contexts(claims, evidence_by_id)

    public = [item for item in items if not explain_skip_symbol(item["name"], item["file"])]
    private = [item for item in items if item not in public]
    rows: list[dict] = []
    unreached: list[str] = []
    flows = 0
    for item in public:
        callers = [c for c in item["reach"]["callers"] if not c.get("private")]
        entries = [c for c in callers if c.get("entry_point")]
        if not entries:
            unreached.append(item["name"])
            continue
        by_key = {(c["depth"], c["name"]): c for c in callers}
        for entry in entries:
            if flows >= FLOW_CAP:
                break
            chain = [entry["name"]]
            current = entry
            while current["depth"] > 1:
                parent = by_key.get((current["depth"] - 1, current["via"]))
                if parent is None:
                    break
                chain.append(parent["name"])
                current = parent
            chain.append(item["name"])
            label = " → ".join(chain)
            direct = chain[-2]
            flows += 1
            rows.extend(_flow(label, item, direct, call_sites, contexts, private))
    if unreached:
        rows.append(
            _row(
                "Not reached from a stored caller",
                ", ".join(unreached[:10]) + (f" and {len(unreached) - 10} more" if len(unreached) > 10 else ""),
                None,
            )
        )
    hidden = sum(1 for item in public for c in item["reach"]["callers"] if c.get("entry_point")) - flows
    if hidden > 0:
        rows.append(_row("More flows", f"{hidden} further flow{'s' if hidden != 1 else ''} not shown.", None))
    return rows or [_row("Change flow", "No behavior change was found in the changed functions.", None)]


def _flow(label: str, item: dict, direct: str, call_sites, contexts, private) -> list[dict]:
    rows: list[dict] = []
    # Error changes get their own row below, so they are not repeated here.
    summaries = [c["summary"] for c in item["changes"] if c.get("summary") and c.get("category") != "error"]
    what = " ".join(_unique(summaries)[:SUMMARY_CAP])
    more = len(_unique(summaries)) - SUMMARY_CAP
    if more > 0:
        what += f" ({more} more change{'s' if more != 1 else ''}.)"
    helpers = [p["name"] for p in private if any(c["name"] == item["name"] for c in p["reach"]["callers"])]
    if helpers:
        what += f" Internal helpers it calls also changed: {len(helpers)}."
    rows.append(_row(label, f"What changes: {what or 'statement-level changes with no summarized outcome.'}", item.get("href")))

    site = call_sites.get((direct, item["name"]))
    if site:
        rows.append(_row(label, f"Call at head: {site['call']} ({site['location']})", site["href"]))

    errors = [c for c in item["changes"] if c["category"] == "error"]
    if errors:
        handled = contexts.get((direct, item["name"]))
        wrap = "" if handled is None else (" The caller wraps the call in a try block." if handled else " The caller does not wrap the call in a try block.")
        rows.append(_row(label, f"Errors: {errors[0]['summary']}{wrap}", errors[0].get("after_href") or errors[0].get("before_href")))

    tests = item["reach"].get("tests") or []
    rows.append(_row(label, "Tests: " + (", ".join(tests[:3]) if tests else "no test references the changed function."), None))
    return rows


def _call_sites(relationships, evidence_by_id, repo, sha) -> dict[tuple[str, str], dict]:
    from app.explanation.behavior_facts import call_expression

    out: dict[tuple[str, str], dict] = {}
    for rel in relationships or []:
        if getattr(rel, "type", None) != "CALLS":
            continue
        evidence = evidence_by_id.get(getattr(rel, "evidence_public_id", None) or getattr(rel, "evidence_id", None))
        target = getattr(rel, "target_name", "") or ""
        if evidence is None or not evidence.snippet or not target:
            continue
        call = call_expression(evidence.snippet, target)
        if not call:
            continue
        location = f"{evidence.file}:{evidence.start_line}" if evidence.start_line else evidence.file
        href = f"https://github.com/{repo}/blob/{sha}/{evidence.file}#L{evidence.start_line}" if repo and sha and evidence.start_line else None
        out.setdefault((getattr(rel, "source_name", ""), target), {"call": call, "location": location, "href": href})
    return out


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


def _row(label: str, value: str, href: str | None) -> dict:
    return {"label": label, "value": value, "href": href}


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _id(item):
    return getattr(item, "public_id", None) or getattr(item, "id", None)
