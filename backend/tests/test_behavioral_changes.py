import re

from app.analyzer.analyze import analyze
from app.analyzer.fixture import load_oauth_snapshot
from app.explanation.behavior_facts import build_behavior_facts
from app.explanation.behavioral_changes import (
    NO_NARRATIVE,
    build_behavioral_section,
    render_behavioral_changes_markdown,
    screen_narrative,
)
from app.explanation.schema import (
    BehaviorChangeFact,
    BehavioralNarrative,
    BehaviorFunctionFact,
    ExplanationDocument,
)
from app.github.comment import render_combined_comment

_PATH = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")


def _facts():
    return [
        BehaviorFunctionFact(
            function="charge",
            file="app/billing.py",
            public=True,
            reached_from=["post_checkout"],
            callers_at_head=["checkout → charge(cart.order)"],
            changes=[
                BehaviorChangeFact(
                    id="b1",
                    claim_id="c1",
                    kind="error",
                    before='raise ValueError("empty order")',
                    after='raise InvalidOrder("negative total")',
                    before_when="total <= 0",
                    after_when="total < 0",
                ),
                BehaviorChangeFact(
                    id="b2", claim_id="c2", kind="return", before=None, after="return None", after_when="total == 0"
                ),
                BehaviorChangeFact(id="b3", claim_id="c3", kind="call", before=None, after="audit.record(order.id)"),
            ],
        ),
        BehaviorFunctionFact(
            function="_fee_for",
            file="app/billing.py",
            public=False,
            reached_from=["post_checkout"],
            changes=[
                BehaviorChangeFact(id="b4", claim_id="c4", kind="value", before="rate = 0.02", after="rate = 0.03"),
            ],
        ),
    ]


def _good_narrative():
    return BehavioralNarrative(
        overview="Checkout now rejects only negative totals and treats an empty order as a no-op.",
        changes=[
            {
                "title": "Empty and negative orders at checkout",
                "before": "Previously, an order with total <= 0 was rejected with ValueError.",
                "after": "With this change, only total < 0 fails, with InvalidOrder; a zero total returns None without charging.",
                "impact": "Anything entering through `post_checkout`.",
                "fact_ids": ["b1", "b2"],
            },
            {
                "title": "Audit trail for charges",
                "before": "Previously, charging an order left no audit record.",
                "after": "With this change, every charge also leaves an audit record.",
                "impact": "",
                "fact_ids": ["b3"],
            },
        ],
        watch=[{"text": "Do callers of `post_checkout` expect None for empty orders?", "fact_ids": ["b2"]}],
    )


def test_grounded_model_narrative_is_kept():
    screened = screen_narrative(_good_narrative(), _facts())
    assert screened is not None
    assert [change.title for change in screened.changes] == [
        "Empty and negative orders at checkout",
        "Audit trail for charges",
    ]
    assert screened.watch[0].text.startswith("Do callers")
    section = build_behavioral_section(narrative=_good_narrative(), facts=_facts())
    assert section["source"] == "model"
    markdown = render_behavioral_changes_markdown(section)
    assert "**Before:** Previously, an order with total <= 0 was rejected with ValueError." in markdown
    assert "Written by the configured model from 4 before-and-after facts" in markdown


def test_narrative_items_that_go_beyond_their_facts_are_dropped():
    narrative = _good_narrative()
    narrative.changes.extend(
        [
            # Names a function that is not in the facts.
            {
                "title": "Refunds",
                "before": "Previously, refunds went through `issue_refund`.",
                "after": "With this change, they go through `refund_v2`.",
                "fact_ids": ["b3"],
            },
            # Cites no known fact.
            {"title": "Caching", "before": "Previously, no cache.", "after": "Now cached.", "fact_ids": ["b99"]},
            # Names a private helper.
            {
                "title": "Fees",
                "before": "Previously, `_fee_for` used 0.02.",
                "after": "With this change, it uses 0.03.",
                "fact_ids": ["b4"],
            },
            # Lists code edits instead of behavior.
            {
                "title": "Charge",
                "before": "Previously, the old code ran.",
                "after": "Added a new function to charge orders.",
                "fact_ids": ["b1"],
            },
            # Asserts a defect.
            {"title": "Audit", "before": "Previously, fine.", "after": "This is broken.", "fact_ids": ["b3"]},
        ]
    )
    narrative.changes = [
        change if isinstance(change, type(narrative.changes[0])) else type(narrative.changes[0]).model_validate(change)
        for change in narrative.changes
    ]
    screened = screen_narrative(narrative, _facts())
    assert [change.title for change in screened.changes] == [
        "Empty and negative orders at checkout",
        "Audit trail for charges",
    ]


def test_private_helper_change_is_described_by_value_not_name():
    narrative = BehavioralNarrative(
        changes=[
            {
                "title": "Checkout fee rate",
                "before": "Previously, the fee rate applied at checkout was `0.02`.",
                "after": "With this change, it is `0.03`.",
                "impact": "Anything entering through `post_checkout`.",
                "fact_ids": ["b4"],
            }
        ]
    )
    screened = screen_narrative(narrative, _facts())
    assert screened is not None and screened.changes[0].title == "Checkout fee rate"


def test_function_and_method_names_are_rejected_even_when_grounded():
    for text in (
        "With this change, `charge` stops early for a zero total.",
        "With this change, it also runs `audit.record`.",
        "With this change, it also runs audit.record(order.id).",
        "With this change, checkout_total is used.",
    ):
        narrative = BehavioralNarrative(
            changes=[{"title": "Checkout", "before": "Previously, a zero total was charged.", "after": text, "fact_ids": ["b2", "b3"]}]
        )
        reasons: list[str] = []
        assert screen_narrative(narrative, _facts(), reasons) is None, text
        assert reasons and reasons[0].startswith("dropped 'Checkout'")


def test_entry_points_may_be_named_only_in_impact():
    ok = BehavioralNarrative(
        changes=[
            {
                "title": "Zero-total orders",
                "before": "Previously, a zero-total order was charged.",
                "after": "With this change, it returns without charging.",
                "impact": "Clients of `post_checkout`.",
                "fact_ids": ["b2"],
            }
        ]
    )
    assert screen_narrative(ok, _facts()) is not None
    bad = ok.model_copy(deep=True)
    bad.changes[0].after = "With this change, `post_checkout` returns without charging."
    assert screen_narrative(bad, _facts()) is None


def test_no_usable_narrative_falls_back_to_flow_and_system_level_summaries():
    narrative = BehavioralNarrative(
        changes=[{"title": "X", "before": "Previously, `made_up`.", "after": "Now `other`.", "fact_ids": ["b1"]}]
    )
    facts = _facts()
    facts[0].changes[0].location = "app/billing.py:13"
    surfaces = [
        {"level": "api", "change": "added", "name": "POST /api/refunds", "detail": "", "evidence": {"label": "app/api.py:40", "href": None}},
    ]
    section = build_behavioral_section(narrative=narrative, facts=facts, surfaces=surfaces)
    assert section["source"] == "rules"
    # System interfaces first, then one item per flow (entry points), never one per function.
    assert [c["title"] for c in section["changes"]] == ["HTTP interface", "Post checkout flow"]
    assert section["changes"][0]["after"] == "The service adds route POST /api/refunds."
    flow = section["changes"][1]
    assert flow["before"].startswith("Previously, requests through this flow failed with one kind of error at the charge step")
    assert flow["after"] == "Now they fail with a different error there; can end early at the charge step in that case; also trigger audit record there."
    assert flow["evidence"][0]["label"] == "app/billing.py:13"
    markdown = render_behavioral_changes_markdown(section).split("**Evidence:**")[0]
    for code in ("made_up", "ValueError", "total", "`", "audit.record", "_fee_for"):
        assert code not in markdown, code


def test_oauth_facts_carry_entry_points_and_contract_check():
    result = analyze(load_oauth_snapshot())
    facts = build_behavior_facts(
        symbols=result.symbols,
        relationships=result.relationships,
        claims=result.claims,
        evidences=result.evidences,
    )
    session = next(fact for fact in facts if fact.function == "createSession")
    assert {change.kind for change in session.changes} >= {"signature", "return"}
    assert set(session.reached_from) == {"login", "googleCallback", "refreshToken"}
    assert session.notes == ["All 3 head call sites of createSession pass a matching number of arguments."]


def test_combined_comment_puts_behavioral_on_explain_not_details():
    result = analyze(load_oauth_snapshot())
    body = render_combined_comment(
        document=ExplanationDocument(summary="summary"),
        failure=None,
        repo_full_name="fixture/oauth-ts",
        pr_number=7,
        head_sha="b" * 40,
        app_base_url="http://explain.example",
        run_id="run",
        evidence_by_id={},
        change_flow="Changed\n  login",
        bullets=["createSession changed."],
        mermaid="flowchart LR\n  A-->B",
        claims=result.claims,
        symbols=result.symbols,
        relationships=result.relationships,
        sections=[],
        evidence=result.evidences,
    )
    explain, rest = body.split("## Details for", 1)
    details = rest.split("## Review for", 1)[0]
    assert "### Behavioral Changes" in explain
    assert "### Behavioral Changes" not in details
    assert "### System Flow Change" not in body
    assert explain.index("```mermaid") < explain.index("### Behavioral Changes")


def _one(title, before, after, impact="", fact_ids=("b1",)):
    return BehavioralNarrative(
        changes=[{"title": title, "before": before, "after": after, "impact": impact, "fact_ids": list(fact_ids)}]
    )


def test_hallucinated_claims_are_dropped():
    cases = {
        # a number that no fact contains
        "stated the number 500": _one(
            "Order totals", "Previously, an order with total <= 0 was rejected.", "With this change, it returns status 500."
        ),
        # a quoted value that no fact contains
        "quoted": _one(
            "Order totals",
            "Previously, an empty order was rejected.",
            'With this change, the client sees "payment declined".',
        ),
        # a failure the cited fact (an added audit call) does not show
        "claimed a failure": _one(
            "Audit trail for charges",
            "Previously, charging an order left no audit record.",
            "With this change, charging fails when the audit record cannot be written.",
            fact_ids=("b3",),
        ),
        # a subject the cited facts never mention
        "shares no subject": _one(
            "Password resets",
            "Previously, password reset emails were sent immediately.",
            "With this change, password reset emails are queued.",
            fact_ids=("b3",),
        ),
    }
    for expected, narrative in cases.items():
        reasons: list[str] = []
        assert screen_narrative(narrative, _facts(), reasons) is None, expected
        assert any(expected in reason for reason in reasons), (expected, reasons)


def test_who_notices_may_name_only_entry_points_that_reach_the_cited_change():
    facts = _facts()
    facts.append(
        BehaviorFunctionFact(
            function="refresh",
            file="app/tokens.py",
            public=True,
            reached_from=["refresh_token"],
            changes=[BehaviorChangeFact(id="b9", claim_id="c9", kind="value", before="ttl = 60", after="ttl = 120")],
        )
    )
    wrong = _one(
        "Audit trail for charges",
        "Previously, charging an order left no audit record.",
        "With this change, every charge also leaves an audit record.",
        impact="Anything entering through `refresh_token`.",
        fact_ids=("b3",),
    )
    reasons: list[str] = []
    assert screen_narrative(wrong, facts, reasons) is None
    assert "does not reach the cited change" in reasons[-1]


def test_numbers_and_failures_backed_by_the_facts_are_kept():
    kept = _one(
        "Empty and negative orders at checkout",
        "Previously, an order with total <= 0 was rejected as an empty order.",
        "With this change, only a total below 0 is rejected as an invalid order.",
    )
    assert screen_narrative(kept, _facts()) is not None


def test_section_links_each_change_to_the_diff_lines_it_rests_on():
    facts = _facts()
    facts[0].changes[0].location = "app/billing.py:13"
    facts[0].changes[0].href = "https://github.com/acme/shop/blob/bbb/app/billing.py#L13"
    section = build_behavioral_section(narrative=_good_narrative(), facts=facts, prescreened=True)
    assert section["changes"][0]["evidence"] == [
        {"label": "app/billing.py:13", "href": "https://github.com/acme/shop/blob/bbb/app/billing.py#L13"}
    ]
    markdown = render_behavioral_changes_markdown(section)
    assert "- **Evidence:** [`app/billing.py:13`](https://github.com/acme/shop/blob/bbb/app/billing.py#L13)" in markdown
    # A stored narrative is screened again: one that no longer matches the facts is not shown.
    stale = _one("Password resets", "Previously, reset emails were sent.", "With this change, they are queued.", fact_ids=("b3",))
    shown = build_behavioral_section(narrative=stale, facts=facts, prescreened=True)
    assert shown["source"] == "rules" and all("Password" not in c["title"] for c in shown["changes"])


def test_plain_words_about_the_changed_function_are_on_topic():
    """'Session expiration' is about createSession even though the changed line only says sess_."""
    facts = [
        BehaviorFunctionFact(
            function="createSession",
            file="src/session.ts",
            public=True,
            reached_from=["login"],
            changes=[
                BehaviorChangeFact(
                    id="b1", claim_id="c1", kind="return", before="return `sess_${userId}`;", after="return `sess_${userId}_${ttlMs}`"
                )
            ],
        )
    ]
    narrative = _one(
        "Session Expiration Handling",
        "Previously, a session identifier named only the user.",
        "With this change, each session identifier also records how long it stays valid.",
    )
    assert screen_narrative(narrative, facts) is not None


def test_areas_and_judgments_the_facts_do_not_show_are_dropped():
    facts = [
        BehaviorFunctionFact(
            function="build_details",
            file="backend/app/explanation/details.py",
            public=True,
            changes=[BehaviorChangeFact(id="b1", claim_id="c1", kind="return", before="return rows", after="return rows[:6]")],
        )
    ]
    honest = _one("Details rows", "Previously, every changed row was listed.", "With this change, at most six rows are listed.")
    reasons: list[str] = []
    kept = screen_narrative(
        BehavioralNarrative(
            overview="These changes primarily affect user authentication and session handling.",
            changes=honest.changes,
        ),
        facts,
        reasons,
    )
    assert kept is not None and kept.overview == ""
    assert "dropped the overview: it goes beyond the kept changes" in reasons
    for text, expected in (
        ("With this change, the updated validation ensures stronger security practices.", "judged the change"),
        ("With this change, the session timeout adjustment cuts the rows listed.", "mentioned session"),
    ):
        reasons = []
        assert screen_narrative(_one("Details rows", "Previously, every changed row was listed.", text), facts, reasons) is None
        assert any(expected in reason for reason in reasons), (expected, reasons)


def test_retry_recovers_a_narrative_that_was_dropped():
    import json
    from types import SimpleNamespace

    from app.explanation.schema import ExplanationDocument
    from app.jobs.pipeline import _retry_narratives

    good = _good_narrative().model_dump()

    class Provider:
        def __init__(self):
            self.requests = []

        def explain(self, request):
            self.requests.append(request)
            return SimpleNamespace(content=json.dumps({"behavioral_changes": good}), model="m")

    packet = SimpleNamespace(behavior_facts=_facts(), impact_facts=[])
    document = ExplanationDocument(summary="s")
    provider = Provider()
    screening = ["dropped 'Password resets': shares no subject with the facts it cites"]
    import app.jobs.pipeline as pipeline

    original = pipeline._request
    pipeline._request = lambda packet, depth, errors: SimpleNamespace(errors=errors)
    try:
        _retry_narratives(provider, packet, "quick", document, screening, [])
    finally:
        pipeline._request = original
    assert document.behavioral_changes is not None and document.behavioral_changes.changes
    assert any("Password resets" in error for error in provider.requests[0].errors)
