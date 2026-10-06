from app.explanation.architecture import component, system_facts
from app.explanation.behavior_facts import build_behavior_facts
from app.explanation.behavioral_changes import build_behavioral_section, render_behavioral_changes_markdown
from app.explanation.impact import build_impact_facts, build_impact_section, render_impact_markdown
from tests.test_review_report import _stored


def _sections(snippet="    charge(order)"):
    st = _stored(snippet)
    facts = build_behavior_facts(
        symbols=st.symbols, relationships=st.relationships, claims=st.claims, evidences=st.evidences,
        repo="acme/shop", base_sha="a" * 40, head_sha="b" * 40,
    )
    system = system_facts(
        symbols=st.symbols, relationships=st.relationships, claims=st.claims, evidences=st.evidences,
        behavior_facts=facts, repo="acme/shop", sha="b" * 40,
    )
    behavior = build_behavioral_section(narrative=None, facts=facts, system=system)
    impact = build_impact_section(
        narrative=None, behavior_facts=facts,
        impact_facts=build_impact_facts(claims=st.claims, evidences=st.evidences, behavior_facts=facts), system=system,
    )
    return system, behavior, impact


def test_components_are_directories_not_files():
    assert component("backend/app/explanation/impact.py") == "explanation"
    assert component("src/session.ts") == "session"
    assert component("app/billing.py") == "billing"


def test_system_facts_come_from_the_diff_and_call_graph():
    system, _, _ = _sections()
    assert system["changed_components"] == ["billing"]
    # charge now also calls audit.record, which lives in another component.
    assert [(d["from"], d["to"]) for d in system["added_deps"]] == [("billing", "audit")]
    assert system["added_deps"][0]["evidence"][0]["label"] == "app/billing.py:18"
    titles = [r["title"] for r in system["risks"]]
    assert "Changed default affects existing callers" in titles
    default = next(r for r in system["risks"] if r["title"] == "Changed default affects existing callers")
    assert default["evidence"][0]["label"] == "app/jobs.py:3"


def test_summaries_are_architectural_and_name_no_code():
    _, behavior, impact = _sections()
    overview = behavior["overview"]
    assert overview.startswith("Changed components: billing.")
    assert "Between components, billing now depends on audit." in overview
    assert "It removes 1 capability (legacy refund)." in overview
    assert impact["overview"].startswith("Overall impact: medium. 2 entry points in jobs and routes reach the changed code")
    for text in (overview, impact["overview"], render_behavioral_changes_markdown(behavior).split("<details>")[0],
                 render_impact_markdown(impact).split("<details>")[0]):
        for code in ("`", "charge(", "legacy_refund", "ValueError", "total", "retries", ".py"):
            assert code not in text, (code, text)


def test_a_removed_capability_still_called_is_a_high_risk():
    from app.analyzer.types import Claim, Evidence

    st = _stored()
    st.evidences.append(
        Evidence(id="ev_d", type="source_span", repo="acme/shop", commit_sha="b" * 40, file="app/refunds/api.py",
                 start_line=12, end_line=12, symbol="legacy_refund", description="dangling_call", snippet="legacy_refund(o)")
    )
    st.claims.append(
        Claim(id="cl_d", epistemic="FACT", kind="dangling_call", text="app/refunds/api.py:12 still calls legacy_refund.",
              subject="legacy_refund", evidence_ids=["ev_d"])
    )
    facts = build_behavior_facts(symbols=st.symbols, relationships=st.relationships, claims=st.claims, evidences=st.evidences)
    system = system_facts(symbols=st.symbols, relationships=st.relationships, claims=st.claims, evidences=st.evidences,
                          behavior_facts=facts, repo="acme/shop", sha="b" * 40)
    risk = system["risks"][0]
    assert risk["severity"] == "high" and risk["title"] == "Removed capability still in use"
    assert risk["text"] == "A capability this change removes is still called from 1 place (in refunds)."
    assert risk["evidence"][0]["label"] == "app/refunds/api.py:12"
