from types import SimpleNamespace

from app.explanation.review_report import (
    assess_risk,
    build_review,
    build_review_facts,
    diff_block,
    make_ground,
    merge_review,
    render_review_markdown,
    screen_review,
)
from app.explanation.schema import ReviewReport
from tests.test_behavior_comparison import PATCH


def _stored(snippet="    charge(order)"):
    from tests.test_behavior_comparison import _facts, _fn
    from app.analyzer.types import Evidence, Relationship

    symbols, relationships, claims, evidences = _facts()
    job = _fn("sym_job", "nightly_job", "app/jobs.py", 1, 5)
    symbols.append(job)
    evidences.append(
        Evidence(id="ev_call", type="call", repo="acme/shop", commit_sha="b" * 40, file="app/jobs.py",
                 start_line=3, end_line=3, symbol="nightly_job", description="call", snippet=snippet)
    )
    relationships.append(
        Relationship(id="r9", type="CALLS", source_id=job.id, target_id="sym_charge", source_name="nightly_job",
                     target_name="charge", source_file="app/jobs.py", target_file="app/billing.py", evidence_id="ev_call")
    )
    return SimpleNamespace(symbols=symbols, relationships=relationships, claims=claims, evidences=evidences)


def _ground(stored):
    facts = build_review_facts(
        symbols=stored.symbols, relationships=stored.relationships, claims=stored.claims, evidences=stored.evidences,
        repo="acme/shop", sha="b" * 40,
    )
    diff, lines = diff_block({"app/billing.py": PATCH})
    return facts, make_ground(diff=diff, lines_by_file=lines, behavior_facts=[], impact_facts=[], review_facts=facts)


def test_diff_block_numbers_head_and_base_lines():
    diff, lines = diff_block({"app/billing.py": PATCH, "package-lock.json": "@@ -1 +1 @@\n-a\n+b\n"})
    assert "--- app/billing.py" in diff and "package-lock.json" not in diff
    assert "      10 + def charge(order, retries=5, *, currency=\"USD\"):" in diff
    assert "old   10 - def charge(order, retries=3):" in diff
    assert 13 in lines["app/billing.py"]


def test_rule_facts_name_the_outside_call_and_what_it_relies_on():
    facts, _ = _ground(_stored())
    caller = next(f for f in facts if f.kind == "caller" and f.location == "app/jobs.py:3")
    assert "`charge(order)`" in caller.text
    assert "The default of retries changes from 3 to 5, and this call relies on it." in caller.text
    assert caller.severity == "medium"


def test_screen_keeps_grounded_items_and_drops_the_rest():
    facts, ground = _ground(_stored())
    caller = next(f for f in facts if f.kind == "caller")
    reasons: list[str] = []
    report = screen_review(
        {
            "attention": [
                {"area": "Retry default", "why_it_matters": "nightly_job relies on it.", "what_changed": "retries goes from 3 to 5.",
                 "what_could_go_wrong": "Twice as many gateway attempts.", "priority": "High", "fact_ids": [caller.id]},
                {"area": "Cache", "why_it_matters": "x", "what_changed": "`redisClient` is gone.", "what_could_go_wrong": "y",
                 "priority": "High", "fact_ids": [caller.id]},
                {"area": "Nothing cited", "why_it_matters": "x", "what_changed": "y", "what_could_go_wrong": "z", "priority": "Low"},
                {"area": "Wrong line", "why_it_matters": "x", "what_changed": "y", "what_could_go_wrong": "z",
                 "priority": "Low", "locations": ["app/billing.py:400"]},
            ],
            "bugs": [
                {"finding": "Negative totals now raise InvalidOrder.", "evidence": "app/billing.py:13", "scenario": "total < 0",
                 "impact": "Callers catching ValueError miss it.", "confidence": "High", "status": "confirmed",
                 "locations": ["app/billing.py:13"]},
            ],
            "questions": [
                {"question": "Is this tested?", "fact_ids": [caller.id]},
                {"question": "Does nightly_job expect five retries?", "fact_ids": [caller.id]},
            ],
            "overall_risk": "Medium",
            "risk_reason": "Defaults change for an unchanged caller.",
        },
        ground,
        reasons,
    )
    assert [a.area for a in report.attention] == ["Retry default"]
    assert any("redisClient" in r for r in reasons)
    assert any("cites no known fact" in r for r in reasons)
    # A bug that rests only on a diff location is not "confirmed".
    assert report.bugs[0].status == "possible"
    assert [q.question for q in report.questions] == ["Does nightly_job expect five retries?"]


def test_model_cannot_lower_the_risk_below_the_rule_floor():
    stored = _stored()
    facts, ground = _ground(stored)
    rules = build_review(stored=stored, repo="acme/shop", sha="b" * 40, patches={"app/billing.py": PATCH})
    model = ReviewReport(overall_risk="Low", risk_reason="Looks fine.")
    merged = assess_risk(merge_review(model, rules), facts, model=model, ground=ground)
    assert merged.overall_risk == rules.overall_risk == "Medium"
    assert merged.attention == rules.attention
    # The model argued for Low, which the evidence does not support, so its sentence is not shown.
    assert "Looks fine" not in merged.risk_reason


def test_risk_rests_on_rule_findings_with_locations():
    stored = _stored()
    report = build_review(stored=stored, repo="acme/shop", sha="b" * 40, patches={"app/billing.py": PATCH})
    assert report.overall_risk == "Medium"
    assert report.risk_drivers, "every risk level above Low names its evidence"
    top = report.risk_drivers[0]
    assert top.level == "Medium" and top.fact_ids[0].startswith("r") and top.location
    assert report.risk_reason.startswith("Medium: ")
    markdown = render_review_markdown(report, repo="acme/shop", sha="b" * 40)
    risk = markdown.split("### Overall review risk: Medium", 1)[1]
    assert "**Evidence:**" in risk and "](https://github.com/acme/shop/blob/" in risk


def test_model_risk_without_grounded_support_is_ignored():
    stored = _stored()
    facts, ground = _ground(stored)
    rules = build_review(stored=stored, repo="acme/shop", sha="b" * 40, patches={"app/billing.py": PATCH})
    model = ReviewReport(overall_risk="Critical", risk_reason="This is a Critical change that breaks payments everywhere.")
    reasons: list[str] = []
    merged = assess_risk(merge_review(model, rules), facts, model=model, ground=ground, reasons=reasons)
    assert merged.overall_risk == "Medium"
    assert "breaks payments" not in merged.risk_reason
    assert any("no grounded finding above Medium" in r for r in reasons)


def test_grounded_model_bug_raises_the_risk_by_one_level_only():
    from app.explanation.schema import PotentialBug

    stored = _stored()
    facts, ground = _ground(stored)
    rules = build_review(stored=stored, repo="acme/shop", sha="b" * 40, patches={"app/billing.py": PATCH})
    bug = PotentialBug(
        finding="A zero total now skips the charge", evidence="e", scenario="s", impact="i",
        confidence="Medium", status="possible", locations=["app/billing.py:15"], source="model",
    )
    model = ReviewReport(overall_risk="Critical", bugs=[bug])
    merged = assess_risk(merge_review(model, rules), facts, model=model, ground=ground)
    assert merged.overall_risk == "High"  # one step above the Medium floor, never Critical
    assert merged.risk_drivers[0].source == "model" and merged.risk_drivers[0].location == "app/billing.py:15"
    assert merged.risk_reason.startswith("High: raised one level from Medium")


def test_model_attention_priority_is_capped_by_its_evidence():
    stored = _stored()
    facts, ground = _ground(stored)
    raw = {
        "attention": [
            {"area": "charge (app/billing.py)", "why_it_matters": "w", "what_changed": "c", "what_could_go_wrong": "g",
             "priority": "Critical", "locations": ["app/billing.py:13"]},
        ]
    }
    report = screen_review(raw, ground)
    assert report.attention[0].priority == "Medium"  # no rule fact cited


def test_failed_model_call_leaves_the_rule_review():
    stored = _stored()
    reasons: list[str] = []

    def broken(system, user, schema):
        raise TimeoutError

    report = build_review(stored=stored, repo="acme/shop", sha="b" * 40, patches={}, ask_model=broken, reasons=reasons)
    assert reasons == ["review call failed: TimeoutError"]
    assert report.attention and report.attention[0].source == "rules"
    markdown = render_review_markdown(report, repo="acme/shop", sha="b" * 40)
    assert markdown.startswith("### 1. Reviewer attention areas")
    titles = ["### 1. Reviewer attention areas", "### 2. Potential bugs", "### 3. Top review questions", "### Overall review risk: Medium"]
    assert [markdown.index(t) for t in titles] == sorted(markdown.index(t) for t in titles)
    assert "Is the new default of retries (3 → 5) intended for nightly_job in app/jobs.py:3?" in markdown
    for removed in ("Reviewer questions", "Missing test scenarios", "Things that look safe", "Could not determine"):
        assert removed not in markdown
    assert not report.questions and not report.missing_tests and not report.safe and not report.undetermined


def test_missing_required_argument_is_a_confirmed_bug():
    from app.analyzer.analyze import _behavior_claims
    from app.analyzer.types import Evidence, FileChange, Relationship, Snapshot
    from tests.test_behavior_comparison import _fn

    patch = "@@ -1,2 +1,2 @@\n-def charge(order):\n+def charge(order, currency):\n     return order.total\n"
    charge = _fn("sym_charge", "charge", "app/billing.py", 1, 2, changed=True)
    job = _fn("sym_job", "nightly_job", "app/jobs.py", 1, 5)
    claims, evidences = [], []

    def add_claim(**kwargs):
        from app.analyzer.types import Claim

        claims.append(Claim(**kwargs))
        return claims[-1]

    def add_evidence(**kwargs):
        evidences.append(Evidence(**kwargs))
        return evidences[-1]

    snapshot = Snapshot(repository="acme/shop", base_sha="a" * 40, head_sha="b" * 40, files={},
                        changes=[FileChange(path="app/billing.py", status="modified", patch=patch)])
    _behavior_claims(snapshot, [charge, job], add_claim, add_evidence)
    evidences.append(Evidence(id="ev_call", type="call", repo="acme/shop", commit_sha="b" * 40, file="app/jobs.py",
                              start_line=3, end_line=3, symbol="nightly_job", description="call", snippet="    charge(order)"))
    relationships = [Relationship(id="r1", type="CALLS", source_id=job.id, target_id=charge.id, source_name="nightly_job",
                                  target_name="charge", source_file="app/jobs.py", target_file="app/billing.py",
                                  evidence_id="ev_call")]
    stored = SimpleNamespace(symbols=[charge, job], relationships=relationships, claims=claims, evidences=evidences)
    report = build_review(stored=stored, repo="acme/shop", sha="b" * 40, patches={"app/billing.py": patch})
    bug = report.bugs[0]
    assert (bug.status, bug.confidence) == ("confirmed", "High")
    assert bug.finding == "A call outside the diff does not pass currency, which charge now requires."
    assert report.attention[0].priority == "High"
    assert report.overall_risk == "High"
    assert report.top_questions[0].question.startswith("nightly_job calls `charge(order)` without currency")


def test_attention_skips_private_dunder_yaml_and_readme():
    from app.explanation.review_report import finalize_review
    from app.explanation.schema import AttentionArea

    def area(name, involved):
        return AttentionArea(
            area=name, why_it_matters="w", what_changed="c", what_could_go_wrong="g", involved=involved, priority="High", fact_ids=["r1"]
        )

    report = ReviewReport(
        attention=[
            area("_load_settings (app/config.py)", ["app/config.py:3"]),
            area("__repr__ (app/models.py)", ["app/models.py:9"]),
            area("Deploy config in deploy.yaml", ["deploy.yaml:2"]),
            area("Setup steps", ["README.md:10"]),
            area("charge (app/billing.py)", ["app/billing.py:10", "_round_total", "README.md"]),
        ]
    )
    kept = finalize_review(report).attention
    assert [a.area for a in kept] == ["charge (app/billing.py)"]
    assert kept[0].involved == ["app/billing.py:10"]


def test_model_reason_is_shown_only_when_it_agrees_and_is_grounded():
    stored = _stored()
    facts, ground = _ground(stored)
    rules = build_review(stored=stored, repo="acme/shop", sha="b" * 40, patches={"app/billing.py": PATCH})
    agrees = ReviewReport(overall_risk="Medium", risk_reason="Existing callers of charge get the new retries default.")
    kept = assess_risk(merge_review(agrees, rules), facts, model=agrees, ground=ground)
    assert kept.risk_reason.endswith("Existing callers of charge get the new retries default.")
    invented = ReviewReport(overall_risk="Medium", risk_reason="It affects 40 merchants via paymentGateway.")
    dropped = assess_risk(merge_review(invented, rules), facts, model=invented, ground=ground)
    assert "merchants" not in dropped.risk_reason
