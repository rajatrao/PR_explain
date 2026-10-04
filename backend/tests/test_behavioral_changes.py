import re

from app.analyzer.analyze import analyze
from app.analyzer.fixture import load_oauth_snapshot
from app.explanation.behavioral_changes import build_behavioral_changes, render_behavioral_changes_markdown
from app.github.comment import render_combined_comment
from app.explanation.schema import ExplanationDocument


_TEST_PATH = re.compile(r"(?:\.test\.|\.spec\.|(?:^|/)test_|(?:^|/)[^/]+_test\.py|Test\.java|Tests\.java)")
_BOILERPLATE = (
    "The previous behavior is not established from this pull request.",
    "Application logic in this pull request was updated.",
    "An exported API surface changed.",
    "Logic outside the diff can still execute paths that reach the changed application code.",
    "API consumers may see different responses",
)


class _Symbol:
    def __init__(self, name, file_path, *, changed=True, exported=False):
        self.kind = "function"
        self.name = name
        self.file_path = file_path
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


def _values(rows):
    return {row["label"]: row["value"] for row in rows}


def _oauth():
    snapshot = load_oauth_snapshot()
    patches = {change.path: change.patch for change in snapshot.changes if change.patch}
    result = analyze(snapshot)
    return result, patches


def test_oauth_behavioral_changes_name_the_session_edit():
    result, patches = _oauth()
    rows = build_behavioral_changes(
        symbols=result.symbols,
        relationships=result.relationships,
        claims=result.claims,
        patches=patches,
    )
    labels = [row["label"] for row in rows]
    assert labels == ["Before", "Now", "Conditions", "What observers notice"]
    blob = render_behavioral_changes_markdown(rows)
    assert "### Behavioral Changes" in blob
    assert _TEST_PATH.search(blob) is None
    values = _values(rows)
    assert "createSession" in values["Before"]
    assert "src/session.ts" in values["Before"]
    assert "userId" in values["Before"]
    assert "sess_${userId}" in values["Before"]
    assert "ttlMs" in values["Now"]
    assert "sess_${userId}_${ttlMs}" in values["Now"]
    assert "login" in values["Conditions"] and "src/login.ts" in values["Conditions"]
    assert "googleCallback" in values["Conditions"]
    assert "refreshToken" in values["Conditions"]
    assert "createSession" in values["What observers notice"]
    assert "src/login.test.ts" not in blob
    assert "src/oauth.test.ts" not in blob
    for phrase in _BOILERPLATE:
        assert phrase not in blob


def test_two_packets_read_as_different_pull_requests():
    result, patches = _oauth()
    oauth_rows = build_behavioral_changes(
        symbols=result.symbols,
        relationships=result.relationships,
        claims=result.claims,
        patches=patches,
    )
    billing_patch = {
        "src/billing.ts": (
            "@@ -1,3 +1,3 @@\n"
            "-export function chargeCard(id: string): number {\n"
            "-  return 0;\n"
            "+export function chargeCard(id: string, cents: number): number {\n"
            "+  return cents;\n"
            " }\n"
        )
    }
    billing_rows = build_behavioral_changes(
        symbols=[
            _Symbol("chargeCard", "src/billing.ts", exported=True),
            _Symbol("checkout", "src/checkout.ts", changed=False),
        ],
        relationships=[_Rel("CALLS", "checkout", "chargeCard", "src/checkout.ts", "src/billing.ts")],
        claims=[
            _Claim("symbol_changed", "chargeCard", "chargeCard changed in src/billing.ts."),
            _Claim("defines_api", "chargeCard", "chargeCard is an exported API in src/billing.ts."),
            _Claim("file_changed", "src/billing.ts", "src/billing.ts is changed in this pull request."),
            _Claim("missing_test", "chargeCard", "No test references chargeCard."),
        ],
        patches=billing_patch,
    )
    oauth_blob = " ".join(row["value"] for row in oauth_rows)
    billing_blob = " ".join(row["value"] for row in billing_rows)
    assert oauth_blob != billing_blob
    assert "createSession" in oauth_blob and "chargeCard" not in oauth_blob
    assert "chargeCard" in billing_blob and "cents" in billing_blob
    assert "createSession" not in billing_blob
    assert "src/checkout.ts" in billing_blob


def test_behavioral_changes_hide_private_python_and_keep_public_names():
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
        _Claim("missing_test", "_configured", "No test references _configured."),
    ]
    patches = {
        "backend/app/llm/provider.py": (
            "@@ -1,2 +1,2 @@\n"
            "-def create_llm_provider():\n"
            "-    return _configured()\n"
            "+def create_llm_provider(model):\n"
            "+    return model\n"
        )
    }
    rows = build_behavioral_changes(symbols=symbols, relationships=[], claims=claims, patches=patches)
    blob = " ".join(row["value"] for row in rows)
    assert "_configured" not in blob
    assert "create_llm_provider" in blob
    assert "backend/app/worker.py" in blob
    assert "model" in blob
    assert "test_provider.py" not in blob


def test_combined_comment_puts_behavioral_on_explain_not_details():
    result, patches = _oauth()
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
    assert "### Behavioral Changes" in explain
    assert "### Behavior Changes" not in body
    assert "### Behavioral Changes" not in details
    assert "### Change flow" not in explain
    assert explain.index("```mermaid") < explain.index("### Behavioral Changes")
    section = explain.split("### Behavioral Changes", 1)[1].split("##", 1)[0]
    assert "createSession" in section
    assert "ttlMs" in section
    assert "src/login.test.ts" not in section
    assert "The previous behavior is not established from this pull request." not in section
