import re

from app.analyzer.analyze import analyze
from app.analyzer.fixture import load_oauth_snapshot
from app.explanation.behavioral_changes import build_behavioral_changes
from app.explanation.system_impact import build_system_impact_rows, render_system_impact_markdown
from app.github.comment import render_combined_comment
from app.explanation.schema import ExplanationDocument

_TEST_PATH = re.compile(r"(?:\.test\.|\.spec\.|(?:^|/)test_|(?:^|/)[^/]+_test\.py|Test\.java|Tests\.java)")
_BOILERPLATE = (
    "An exported API change is reached from call paths outside this pull request.",
    "An exported API surface changed in this pull request.",
    "Stored tests reference part of the changed application logic.",
    "No direct impact was identified in the available code.",
    "No additional review interaction was identified.",
    "No stored relationship ties the change",
    "Verify behavior where code outside this diff reaches the updated logic.",
)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.])\s+", text) if part.strip()]


def test_system_impact_names_callers_import_and_signature():
    snapshot = load_oauth_snapshot()
    patches = {change.path: change.patch for change in snapshot.changes if change.patch}
    result = analyze(snapshot)
    rows = build_system_impact_rows(
        symbols=result.symbols,
        relationships=result.relationships,
        claims=result.claims,
        patches=patches,
    )
    assert [row["label"] for row in rows] == ["System Impact", "Reviewer Considerations", "Risk & Scope"]
    blob = render_system_impact_markdown(rows)
    values = {row["label"]: row["value"] for row in rows}
    assert "createSession" in values["System Impact"]
    assert "src/session.ts" in values["System Impact"]
    assert "login" in values["System Impact"] and "src/login.ts" in values["System Impact"]
    assert "src/password.ts" in values["System Impact"]
    assert "imports" in values["System Impact"]
    assert "ttlMs" in values["Reviewer Considerations"]
    assert "src/password.ts" in values["Reviewer Considerations"]
    assert "refreshToken" in values["Reviewer Considerations"]
    assert "src/login.ts" in values["Risk & Scope"]
    assert _TEST_PATH.search(blob) is None
    assert "src/login.test.ts" not in blob
    assert "src/oauth.test.ts" not in blob
    for phrase in _BOILERPLATE:
        assert phrase not in blob
    behavioral = " ".join(
        row["value"]
        for row in build_behavioral_changes(
            symbols=result.symbols,
            relationships=result.relationships,
            claims=result.claims,
            patches=patches,
        )
    )
    for sentence in _sentences(blob):
        assert sentence not in behavioral


def test_two_packets_have_different_system_impact():
    snapshot = load_oauth_snapshot()
    patches = {change.path: change.patch for change in snapshot.changes if change.patch}
    result = analyze(snapshot)
    oauth = build_system_impact_rows(
        symbols=result.symbols,
        relationships=result.relationships,
        claims=result.claims,
        patches=patches,
    )

    class _Symbol:
        def __init__(self, name, path, *, changed=True, exported=False):
            self.kind = "function"
            self.name = name
            self.file_path = path
            self.changed = changed
            self.exported = exported

    class _Claim:
        def __init__(self, kind, subject, text):
            self.kind = kind
            self.subject = subject
            self.text = text

    class _Rel:
        def __init__(self, type_, source, target, source_file, target_file):
            self.type = type_
            self.source_name = source
            self.target_name = target
            self.source_file = source_file
            self.target_file = target_file

    billing = build_system_impact_rows(
        symbols=[_Symbol("chargeCard", "src/billing.ts", exported=True)],
        relationships=[_Rel("CALLS", "checkout", "chargeCard", "src/checkout.ts", "src/billing.ts")],
        claims=[
            _Claim("symbol_changed", "chargeCard", "chargeCard changed in src/billing.ts."),
            _Claim("defines_api", "chargeCard", "chargeCard is an exported API in src/billing.ts."),
            _Claim("file_changed", "src/billing.ts", "src/billing.ts is changed in this pull request."),
            _Claim("missing_test", "chargeCard", "No test references chargeCard."),
        ],
        patches={
            "src/billing.ts": (
                "@@ -1,2 +1,2 @@\n"
                "-export function chargeCard(id: string): number {\n"
                "-  return 0;\n"
                "+export function chargeCard(id: string, cents: number): number {\n"
                "+  return cents;\n"
            )
        },
    )
    oauth_blob = " ".join(row["value"] for row in oauth)
    billing_blob = " ".join(row["value"] for row in billing)
    assert oauth_blob != billing_blob
    assert "createSession" in oauth_blob and "chargeCard" not in oauth_blob
    assert "chargeCard" in billing_blob and "cents" in billing_blob
    assert "src/checkout.ts" in billing_blob
    assert "createSession" not in billing_blob
    assert "passes `cents`" in billing_blob or "passes `cents`" in billing[1]["value"]


def test_combined_comment_puts_system_impact_on_explain_after_behavioral():
    snapshot = load_oauth_snapshot()
    result = analyze(snapshot)
    patches = {change.path: change.patch for change in snapshot.changes if change.patch}
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
        patches=patches,
    )
    explain, rest = body.split("## Details for", 1)
    details = rest.split("## Review for", 1)[0]
    assert "### System Impact" in explain
    assert "### System Impact" not in details
    assert explain.index("### Behavioral Changes") < explain.index("### System Impact")
    assert explain.index("### System Impact") < explain.index("- createSession changed.")
    impact = explain.split("### System Impact", 1)[1].split("- createSession changed.", 1)[0]
    assert "ttlMs" in impact
    assert "src/password.ts" in impact
    assert "Stored tests reference part of the changed application logic." not in impact
