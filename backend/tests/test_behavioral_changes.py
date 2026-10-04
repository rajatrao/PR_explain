import re

from app.analyzer.analyze import analyze
from app.analyzer.fixture import load_oauth_snapshot
from app.explanation.behavioral_changes import build_behavioral_changes, render_behavioral_changes_markdown
from app.github.comment import render_combined_comment
from app.explanation.schema import ExplanationDocument


_PATH = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
_CALLS = re.compile(r"\bcalls\b", re.I)


def _values(rows):
    return {row["label"]: row["value"] for row in rows}


def test_oauth_behavioral_changes_are_outcome_lines_without_calls():
    result = analyze(load_oauth_snapshot())
    rows = build_behavioral_changes(
        symbols=result.symbols,
        relationships=result.relationships,
        claims=result.claims,
    )
    labels = [row["label"] for row in rows]
    assert labels == ["Before", "Now", "Conditions", "What observers notice"]
    blob = render_behavioral_changes_markdown(rows)
    assert "### Behavioral Changes" in blob
    assert "| Call |" not in blob
    assert _CALLS.search(blob) is None
    assert _PATH.search(blob) is None
    values = _values(rows)
    assert "not established" in values["Before"].lower()
    assert "application logic" in values["Now"].lower() or "api" in values["Now"].lower()
    assert values["Conditions"].strip()
    assert values["What observers notice"].strip()


def test_behavioral_changes_hide_private_python():
    class _Symbol:
        def __init__(self, name, file_path, *, changed=True):
            self.kind = "function"
            self.name = name
            self.file_path = file_path
            self.changed = changed

    class _Claim:
        def __init__(self, kind, subject, text):
            self.kind = kind
            self.subject = subject
            self.text = text

    symbols = [
        _Symbol("create_llm_provider", "backend/app/llm/provider.py"),
        _Symbol("_configured", "backend/app/llm/provider.py"),
    ]
    claims = [
        _Claim("symbol_changed", "create_llm_provider", "create_llm_provider changed in backend/app/llm/provider.py."),
        _Claim("defines_api", "create_llm_provider", "create_llm_provider is an exported API."),
        _Claim(
            "reaches_changed",
            "backend/app/worker.py",
            "backend/app/worker.py reaches changed symbol _configured, create_llm_provider.",
        ),
        _Claim("missing_test", "create_llm_provider", "No test references create_llm_provider."),
    ]
    rows = build_behavioral_changes(symbols=symbols, relationships=[], claims=claims)
    blob = " ".join(row["value"] for row in rows)
    assert "_configured" not in blob
    assert "create_llm_provider" not in blob
    assert "worker.py" not in blob
    assert _CALLS.search(blob) is None


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
    assert "### Behavior Changes" not in body
    assert "### Behavioral Changes" not in details
    assert "### Change flow" not in explain
    assert explain.index("```mermaid") < explain.index("### Behavioral Changes")
    assert _CALLS.search(explain.split("### Behavioral Changes", 1)[1].split("##", 1)[0]) is None
