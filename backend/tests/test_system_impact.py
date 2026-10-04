import re

from app.analyzer.analyze import analyze
from app.analyzer.fixture import load_oauth_snapshot
from app.explanation.system_impact import build_system_impact_rows, render_system_impact_markdown
from app.github.comment import render_combined_comment
from app.explanation.schema import ExplanationDocument

_PATH = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")


def test_system_impact_rows_are_three_labeled_parts():
    result = analyze(load_oauth_snapshot())
    rows = build_system_impact_rows(
        symbols=result.symbols,
        relationships=result.relationships,
        claims=result.claims,
    )
    assert [row["label"] for row in rows] == ["System Impact", "Reviewer Considerations", "Risk & Scope"]
    blob = render_system_impact_markdown(rows)
    assert _PATH.search(blob) is None
    assert "createSession" not in blob


def test_combined_comment_puts_system_impact_on_explain_after_behavioral():
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
    assert "### System Impact" in explain
    assert "### System Impact" not in details
    assert explain.index("### Behavioral Changes") < explain.index("### System Impact")
    assert explain.index("### System Impact") < explain.index("- createSession changed.")
