from app.analyzer.analyze import _behavior_claims
from app.analyzer.behavior import extract_behavior_deltas
from app.analyzer.types import Claim, Evidence, FileChange, Relationship, Snapshot, Symbol
from app.explanation.behavior_comparison import build_behavior_comparison
from app.explanation.system_behavior import build_system_behavior, render_system_behavior_markdown
from app.explanation.behavior_flow import build_behavior_flows, render_behavior_flows_markdown

PATCH = """@@ -10,12 +10,15 @@ def charge(order, retries=3):
-def charge(order, retries=3):
+def charge(order, retries=5, *, currency="USD"):
     total = order.total
-    if total <= 0:
-        raise ValueError("empty order")
+    if total < 0:
+        raise InvalidOrder("negative total")
+    if total == 0:
+        return None
     fee = total * 0.02
-    gateway.charge(total + fee)
-    logger.info("charged")
+    gateway.charge(total + fee, currency=currency)
+    audit.record(order.id)
     return total + fee
@@ -40,3 +43,0 @@
-def legacy_refund(order):
-    return gateway.refund(order.total)
-
"""


def _fn(id, name, path, start, end, exported=True, changed=False):
    return Symbol(id=id, name=name, kind="function", file_path=path, start_line=start, end_line=end, exported=exported, changed=changed)


def _calls(id, source, target):
    return Relationship(
        id=id,
        type="CALLS",
        source_id=source.id,
        target_id=target.id,
        source_name=source.name,
        target_name=target.name,
        source_file=source.file_path,
        target_file=target.file_path,
    )


def _facts():
    charge = _fn("sym_charge", "charge", "app/billing.py", 10, 24, changed=True)
    checkout = _fn("sym_checkout", "checkout", "app/cart.py", 3, 12, exported=False)
    route = _fn("sym_route", "post_checkout", "app/routes.py", 5, 9)
    record = _fn("sym_record", "record", "app/audit.py", 1, 4)
    gateway = _fn("sym_gw", "charge_card", "app/gateway.py", 1, 4)
    symbols = [charge, checkout, route, record, gateway]
    relationships = [
        _calls("r1", checkout, charge),
        _calls("r2", route, checkout),
        _calls("r3", charge, record),
    ]
    snapshot = Snapshot(
        repository="acme/shop",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files={},
        changes=[FileChange(path="app/billing.py", status="modified", patch=PATCH)],
    )
    claims: list[Claim] = []
    evidences: list[Evidence] = []

    def add_claim(**kwargs):
        claims.append(Claim(**kwargs))
        return claims[-1]

    def add_evidence(**kwargs):
        evidences.append(Evidence(**kwargs))
        return evidences[-1]

    _behavior_claims(snapshot, [s for s in symbols], add_claim, add_evidence)
    return symbols, relationships, claims, evidences


def test_extracts_before_and_after_pairs_by_category():
    deltas = extract_behavior_deltas(
        [FileChange(path="app/billing.py", status="modified", patch=PATCH)],
        [_fn("c", "charge", "app/billing.py", 10, 24)],
    )
    by_category = {}
    for delta in deltas:
        by_category.setdefault(delta.category, []).append(delta)
    assert by_category["removed_function"][0].symbol == "legacy_refund"
    sig = by_category["signature"][0]
    assert "currency" in sig.summary and "default of retries changes from 3 to 5" in sig.summary
    error = by_category["error"][0]
    assert error.before.startswith("raise ValueError") and error.after.startswith("raise InvalidOrder")
    assert any(d.after == "if total == 0:" and d.before is None for d in by_category["condition"])
    assert any("audit.record" in d.summary for d in by_category["call"])
    assert by_category["logging"][0].after is None


def test_unchanged_reindented_line_is_not_a_behavior_change():
    patch = "@@ -1,3 +1,3 @@\n def f(x):\n-  return x + 1\n+    return x + 1\n"
    deltas = extract_behavior_deltas(
        [FileChange(path="m.py", status="modified", patch=patch)],
        [_fn("f", "f", "m.py", 1, 2)],
    )
    assert deltas == []


def test_return_through_local_variable_compares_like_for_like():
    patch = (
        "@@ -1,3 +1,4 @@\n"
        "-export function createSession(userId: string): string {\n"
        "-  return `sess_${userId}`;\n"
        "+export function createSession(userId: string, ttlMs: number): string {\n"
        "+  const token = `sess_${userId}_${ttlMs}`;\n"
        "+  return token;\n"
        " }\n"
    )
    deltas = extract_behavior_deltas(
        [FileChange(path="src/session.ts", status="modified", patch=patch)],
        [_fn("s", "createSession", "src/session.ts", 1, 4)],
    )
    returns = [d for d in deltas if d.category == "return"]
    assert returns[0].before == "return `sess_${userId}`;"
    assert returns[0].after == "return `sess_${userId}_${ttlMs}`"
    assert not any(d.category == "value" for d in deltas)
    signature = next(d for d in deltas if d.category == "signature")
    assert "now takes ttlMs" in signature.summary
    assert "Every existing caller must match" in signature.summary


def test_comparison_groups_changes_and_walks_callers_upstream():
    symbols, relationships, claims, evidences = _facts()
    assert all(claim.kind == "behavior_changed" for claim in claims)
    assert {e.type for e in evidences} == {"behavior_before", "behavior_after"}
    assert {e.commit_sha for e in evidences if e.type == "behavior_before"} == {"a" * 40}

    comparison = build_behavior_comparison(
        symbols=symbols,
        relationships=relationships,
        claims=claims,
        evidences=evidences,
        repo="acme/shop",
        base_sha="a" * 40,
        head_sha="b" * 40,
    )
    items = {item["name"]: item for item in comparison["items"]}
    charge = items["charge"]
    assert charge["changes"][0]["category"] == "signature"
    error = next(c for c in charge["changes"] if c["category"] == "error")
    assert error["before_href"] == f"https://github.com/acme/shop/blob/{'a' * 40}/app/billing.py#L13"
    callers = {c["name"]: c for c in charge["reach"]["callers"]}
    assert callers["checkout"]["depth"] == 1 and callers["checkout"]["outside_diff"]
    assert callers["post_checkout"]["depth"] == 2 and callers["post_checkout"]["entry_point"]
    assert items["legacy_refund"]["removed"]
    assert "reaches 2 callers" in comparison["summary"]



def test_system_behavior_summarizes_outcomes_with_conditions_and_reach():
    symbols, relationships, claims, evidences = _facts()
    comparison = build_behavior_comparison(
        symbols=symbols, relationships=relationships, claims=claims, evidences=evidences
    )
    summary = build_system_behavior(comparison, relationships=relationships, evidences=evidences, claims=claims)
    charge = next(c for c in summary["changes"] if c["subject"] == "charge")
    rows = [(r["before"], r["now"]) for r in charge["rows"]]
    assert ("Callers invoke `charge(order, retries)`.", "Callers must invoke `charge(order, retries, currency)`.") in rows
    # The raise is stated with the condition that encloses it on each side of the diff.
    assert ("`charge` raises ValueError when `total <= 0`.", "It raises InvalidOrder when `total < 0`.") in rows
    assert ("`charge` has no return of `None` when `total == 0`.", "It returns `None` when `total == 0`.") in rows
    assert ("`charge` does not call `audit.record`.", "It calls `audit.record`.") in rows
    assert ("`charge` calls `logger.info`.", "It no longer calls `logger.info`.") in rows
    assert (
        "`charge` calls `gateway.charge` with `total + fee`.",
        "It calls `gateway.charge` with `total + fee, currency=currency`.",
    ) in rows
    # Guard conditions are not repeated as separate rows.
    assert not any("checks" in before or "checks" in now for before, now in rows)
    assert charge["entry_points"] == ["post_checkout"]
    assert "1 entry point: `post_checkout`" in summary["headline"]
    removed = next(c for c in summary["changes"] if c["subject"] == "legacy_refund")
    assert removed["rows"] == [{"before": "`legacy_refund` is defined in app/billing.py.", "now": "`legacy_refund` is no longer defined."}]

    markdown = render_system_behavior_markdown(summary)
    assert "### System Behavior Change" in markdown
    assert "| Before this PR | After this PR |" in markdown
    assert "**Reached from:** `post_checkout` (entry point)." in markdown
    # No raw diff lines: the summary never prints a whole statement such as the def header.
    assert "def charge" not in markdown and 'raise ValueError("empty order")' not in markdown


def test_call_sites_come_from_stored_call_evidence():
    symbols, relationships, claims, evidences = _facts()
    checkout = next(s for s in symbols if s.name == "checkout")
    charge = next(s for s in symbols if s.name == "charge")
    evidences.append(
        Evidence(
            id="ev_call_checkout",
            type="source_span",
            repo="acme/shop",
            commit_sha="b" * 40,
            file="app/cart.py",
            start_line=7,
            end_line=7,
            symbol="charge",
            description="checkout calls charge in app/cart.py.",
            snippet="    receipt = charge(cart.order, retries=2)",
        )
    )
    relationships[0] = Relationship(
        id="r1",
        type="CALLS",
        source_id=checkout.id,
        target_id=charge.id,
        source_name="checkout",
        target_name="charge",
        source_file="app/cart.py",
        target_file="app/billing.py",
        evidence_id="ev_call_checkout",
    )
    comparison = build_behavior_comparison(symbols=symbols, relationships=relationships, claims=claims, evidences=evidences)
    summary = build_system_behavior(comparison, relationships=relationships, evidences=evidences, claims=claims)
    change = next(c for c in summary["changes"] if c["subject"] == "charge")
    assert change["call_sites"] == [
        {"caller": "checkout", "file": "app/cart.py", "line": 7, "call": "charge(cart.order, retries=2)"}
    ]
    assert "`checkout` → `charge(cart.order, retries=2)`" in render_system_behavior_markdown(summary)


def test_old_and_new_flow_diagrams_mark_side_only_edges():
    symbols, relationships, claims, evidences = _facts()
    comparison = build_behavior_comparison(
        symbols=symbols, relationships=relationships, claims=claims, evidences=evidences
    )
    flows = build_behavior_flows(comparison, symbols=symbols, relationships=relationships)
    before, after = flows["before"], flows["after"]
    assert before.startswith("flowchart LR") and after.startswith("flowchart LR")
    assert "charge(order, retries)" in before
    assert "charge(order, retries, currency)" in after
    assert "raise ValueError" in before and "raise ValueError" not in after
    assert "raise InvalidOrder" in after and "raise InvalidOrder" not in before
    # charge -> record is a stored head edge and record only appears on + lines, so it is new-only.
    assert "k_record" in after and "k_record" not in before
    assert "k_InvalidOrder" not in after
    assert "legacy_refund" in before and "legacy_refund" not in after
    assert "classDef gone" in before and "linkStyle" in after
    markdown = render_behavior_flows_markdown(flows)
    assert markdown.count("```mermaid") == 2


def test_no_behavior_claims_means_no_sections():
    comparison = build_behavior_comparison(symbols=[], relationships=[], claims=[], evidences=[])
    assert comparison["items"] == []
    summary = build_system_behavior(comparison, relationships=[], evidences=[], claims=[])
    assert render_system_behavior_markdown(summary) == ""
    flows = build_behavior_flows(comparison, symbols=[], relationships=[])
    assert render_behavior_flows_markdown(flows) == ""
