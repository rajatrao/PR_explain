from app.explanation.schema import EvidenceRef, ExplanationDocument, Statement
from app.github.patches import patches_from_compare
from app.github.comment import (
    RETIRED_DETAILS_NOTE,
    _clip_preserving_flow,
    details_marker,
    explain_marker,
    legacy_explain_marker,
    publish_combined_comment,
    render_combined_comment,
)
from tests.fakes import MemoryComments

OLD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NEW = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _document(summary: str) -> ExplanationDocument:
    return ExplanationDocument(
        summary=summary,
        change_flow=[
            Statement(
                epistemic="FACT",
                text="login calls createSession.",
                claim_ids=["cl_calls"],
                evidence_ids=["ev_login"],
            )
        ],
        impacts=[
            Statement(
                epistemic="INFERENCE",
                text="src/health.ts behavior is unchanged because no call or import path reaches a changed symbol.",
                claim_ids=["cl_health"],
                evidence_ids=["ev_health"],
            )
        ],
        unknowns=[
            Statement(
                epistemic="UNKNOWN",
                text="No test references refreshToken.",
                claim_ids=["cl_missing"],
                evidence_ids=[],
            )
        ],
        review_questions=[
            Statement(
                epistemic="UNKNOWN",
                text="Which caller has no test?",
                claim_ids=["cl_missing"],
                evidence_ids=[],
            )
        ],
    )


def _evidence() -> dict[str, EvidenceRef]:
    return {
        "ev_login": EvidenceRef(
            id="ev_login",
            type="source_span",
            repo="acme/app",
            commit_sha=NEW,
            file="src/login.ts",
            start_line=4,
            end_line=4,
            symbol="createSession",
            description="login calls createSession.",
        ),
        "ev_health": EvidenceRef(
            id="ev_health",
            type="absent_file",
            repo="acme/app",
            commit_sha=NEW,
            file="src/health.ts",
            description="absent",
        ),
    }


def _combined(**overrides):
    payload = {
        "document": _document("second"),
        "failure": None,
        "repo_full_name": "acme/app",
        "pr_number": 7,
        "head_sha": NEW,
        "app_base_url": "http://explain.example",
        "run_id": "run-new",
        "evidence_by_id": _evidence(),
        "change_flow": "Changed\n  login",
        "bullets": ["second"],
        "mermaid": 'flowchart TD\n  login["login"]',
    }
    payload.update(overrides)
    return render_combined_comment(**payload)


def test_comment_is_the_quick_story_and_replaces_sha():
    first = _combined(document=_document("first"), head_sha=OLD, run_id="run-old", bullets=["first"])
    second = _combined()
    explain, rest = second.split("## Details for", 1)
    details, review = rest.split("## Review for", 1)
    assert explain_marker("acme/app", 7) in second
    assert second.count(explain_marker("acme/app", 7)) == 1
    assert "view=details" not in second
    assert second.index("## Explain for") < second.index("## Details for") < second.index("## Review for")
    assert f"## Explain for `{NEW}`" in second
    assert f"## Details for `{NEW}`" in second
    assert f"## Review for `{NEW}`" in second
    assert "- second" in explain
    assert "```mermaid" in explain
    assert explain.index("```mermaid") < explain.index("### Behavioral Changes")
    assert explain.index("### Behavioral Changes") < explain.index("### System Impact")
    assert explain.index("### System Impact") < explain.index("- second")
    assert "### Change flow" not in explain
    assert "### Behavioral Changes" in explain
    assert "### System Impact" in explain
    assert "### Behavior Changes" not in second
    assert "### Behavioral Changes" not in details
    assert "### System Impact" not in details
    assert "- Changed" not in explain
    assert "  - login" not in explain
    assert "### Change flow" in details
    assert "### Diagram" not in second
    assert "## Diagram" not in second
    assert "```mermaid" not in details
    assert "```mermaid" not in review
    assert "### Change Overview" not in details
    assert "Change Overview" not in details
    assert "### Impact" not in details
    assert "### System Impact" not in details
    assert "### System Impact" in explain
    assert "| Area | Reason | Evidence file |" not in details
    assert "### Risk Areas" not in details
    assert "### Unknowns" not in details
    assert "### Unknowns" not in second
    assert "### Reviewer Attention" not in details
    assert "### Review questions" not in details
    assert "### Reviewer Attention" in review
    assert "#### Suggested review areas" in review
    assert "### Review questions" in review
    assert review.index("### Reviewer Attention") < review.index("#### Suggested review areas") < review.index("### Review questions")
    assert "login calls createSession" not in second
    assert "**FACT**" not in second
    assert "Architecture" not in second
    assert "Open Explain and Details" in second
    assert "Quick" not in second
    assert "Deep" not in second
    assert "http://explain.example/runs/run-new" in second
    assert "score" not in second.lower()
    assert "verdict" not in second.lower()
    assert NEW in second
    assert OLD not in second
    assert OLD in first
    assert NEW not in first


def test_failure_comment_does_not_keep_previous_prose():
    body = _combined(document=None, failure="connection refused", bullets=None, mermaid=None, change_flow=None)
    assert NEW in body
    assert OLD not in body
    assert "login calls createSession" not in body
    assert "connection refused" in body
    assert f"## Explain for `{NEW}`" in body
    assert f"## Details for `{NEW}`" in body
    assert f"## Review for `{NEW}`" in body
    assert body.index("## Explain for") < body.index("## Details for") < body.index("## Review for")
    assert "Quick" not in body
    assert "score" not in body.lower()
    assert "```mermaid" not in body
    assert "What changed" not in body
    assert "- second" not in body


class _Claim:
    def __init__(self, kind: str, subject: str, text: str) -> None:
        self.kind = kind
        self.subject = subject
        self.text = text
        self.evidence_public_ids: list[str] = []
        self.epistemic = "FACT"


_DETAILS_ORDER = [
    "### High-level areas affected",
    "### Key Changes",
    "### What changed",
    "### Change flow",
    "### Shared code",
    "### Why a file outside the diff matters",
    "<summary>Tests</summary>",
    "<summary>Unchanged boundary</summary>",
]


def test_combined_comment_reuses_explain_and_retires_details():
    comments = MemoryComments()
    legacy = legacy_explain_marker("acme/app", 7)
    explain_id = 5964194098
    details_id = 5964194099
    comments.comments[explain_id] = f"{legacy}\n\n## Explain for `{OLD}`\n\nold prose"
    comments.comments[details_id] = (
        f"{details_marker('acme/app', 7)}\n\n## Details for `{OLD}`\n\n### What changed\n\nold details"
    )
    comments._next = 5964194100
    sections = [
        {"heading": "Changed", "items": [{"text": "login", "detail": None}]},
        {"heading": "login", "items": [{"text": "calls createSession", "detail": "src/login.ts:4"}]},
    ]
    claims = [
        _Claim("symbol_changed", "login", "login changed in src/login.ts."),
        _Claim("unknown_boundary", "Unknown", "No dependency facts are in this packet."),
    ]
    first = _combined(
        document=_document("first"),
        head_sha=OLD,
        run_id="run-old",
        bullets=["first bullet"],
        evidence_by_id={},
        claims=claims,
        sections=sections,
        document_unknowns=[
            "No database or schema facts are in this packet.",
            "No external system facts are in this packet.",
        ],
    )
    explain, rest = first.split("## Details for", 1)
    details, review = rest.split("## Review for", 1)
    places = [details.index(title) for title in _DETAILS_ORDER]
    assert places == sorted(places)
    assert "### Reviewer Attention" not in details
    assert "What changed" in details
    assert "### Impact" not in details
    assert "### System Impact" not in details
    assert "### System Impact" in explain
    assert "| Area | Reason | Evidence file |" not in details
    assert "### Risk Areas" not in details
    assert "### Change Overview" not in details
    assert "Change Overview" not in details
    assert "```mermaid" in explain
    assert "### Diagram" not in explain
    assert review.index("### Reviewer Attention") < review.index("### Review questions")
    assert "### Unknowns" not in details
    assert "### Unknowns" not in first
    assert "No dependency facts are in this packet" not in details
    assert "No database or schema facts are in this packet" not in details
    assert "No external system facts are in this packet" not in details
    assert "No dependency facts are in this packet" not in review
    assert "No database or schema facts are in this packet" not in review
    assert "No external system facts are in this packet" not in review
    assert "**Unknown**" not in review
    assert "Claims stay. Narration is optional." not in first
    assert "view=details" not in first

    assert publish_combined_comment(comments, "acme/app", 7, first, fallback_id=explain_id) == explain_id
    assert len(comments.comments) == 2
    assert comments._next == 5964194100
    assert comments.comments[details_id] == RETIRED_DETAILS_NOTE
    assert "What changed" not in comments.comments[details_id]
    assert "old details" not in comments.comments[details_id]
    created_after_first = comments._next
    writes_after_retire = len(comments.bodies)

    second = _combined(
        bullets=["second bullet"],
        evidence_by_id={},
        claims=claims,
        sections=sections,
    )
    assert publish_combined_comment(comments, "acme/app", 7, second, fallback_id=explain_id) == explain_id
    assert len(comments.comments) == 2
    assert comments._next == created_after_first
    assert len(comments.bodies) == writes_after_retire + 1
    stored = comments.comments[explain_id]
    assert NEW in stored
    assert OLD not in stored
    assert "old prose" not in stored
    assert "second bullet" in stored
    assert "What changed" in stored
    assert comments.comments[details_id] == RETIRED_DETAILS_NOTE

    failed_sha = "c" * 40
    failed = _combined(
        document=None,
        failure="connection refused",
        head_sha=failed_sha,
        run_id="run-failed",
        bullets=None,
        mermaid=None,
        change_flow=None,
        evidence_by_id={},
    )
    assert publish_combined_comment(comments, "acme/app", 7, failed, fallback_id=explain_id) == explain_id
    assert len(comments.comments) == 2
    assert comments._next == created_after_first
    failed_body = comments.comments[explain_id]
    assert "connection refused" in failed_body
    assert failed_sha in failed_body
    assert "second bullet" not in failed_body
    assert NEW not in failed_body
    assert "What changed" not in failed_body
    assert comments.comments[details_id] == RETIRED_DETAILS_NOTE


def test_leftover_details_comment_is_deleted_when_the_client_supports_it():
    class DeletingComments(MemoryComments):
        def delete_comment(self, full_name: str, comment_id: int) -> None:
            self.comments.pop(comment_id, None)

    comments = DeletingComments()
    comments.comments[1] = f"{explain_marker('acme/app', 7)}\n\n## Explain for `{OLD}`\n\nold prose"
    comments.comments[2] = f"{details_marker('acme/app', 7)}\n\n## Details for `{OLD}`\n\n### What changed\n\nold details"
    comments._next = 3
    body = _combined(head_sha=OLD, run_id="run-old", bullets=["first bullet"])
    assert publish_combined_comment(comments, "acme/app", 7, body, fallback_id=1) == 1
    assert list(comments.comments) == [1]
    assert "view=details" not in comments.comments[1]
    assert comments._next == 3
    later = _combined(bullets=["second bullet"])
    assert publish_combined_comment(comments, "acme/app", 7, later, fallback_id=1) == 1
    assert list(comments.comments) == [1]
    assert comments._next == 3
    assert "second bullet" in comments.comments[1]


def test_combined_comment_omits_trace_and_updates_in_place():
    comments = MemoryComments()
    first = _combined(head_sha=OLD, run_id="run-old", bullets=["first"], mermaid=None)
    assert "### Trace" not in first
    assert "webhook received" not in first.lower()
    assert "AGENT REPORTED" not in first
    assert "Received pull_request webhook" not in first
    assert "dependabot[bot]" not in first
    assert "```" not in first
    assert "view=explain" in first
    assert "view=details" not in first
    explain, rest = first.split("## Details for", 1)
    _details, review = rest.split("## Review for", 1)
    assert "### Reviewer Attention" in review
    assert "### Review questions" in review
    assert "trace" not in review.lower()

    first_id = publish_combined_comment(comments, "acme/app", 7, first)
    later = _combined(bullets=["second"], mermaid=None)
    second_id = publish_combined_comment(comments, "acme/app", 7, later, fallback_id=first_id)
    assert second_id == first_id
    assert len(comments.comments) == 1
    assert comments._next == first_id + 1
    assert "Comment started" not in comments.comments[first_id]
    assert "### Trace" not in comments.comments[first_id]
    assert OLD not in comments.comments[first_id]
    assert "second" in comments.comments[first_id]
    assert "## Explain for" in comments.comments[first_id]
    assert "## Details for" in comments.comments[first_id]
    assert "## Review for" in comments.comments[first_id]


_LOGIN_PATCH = "@@ -4,3 +4,3 @@\n context\n-return token;\n+return session;\n"
_SESSION_PATCH = "@@ -1,2 +1,2 @@\n-const old = 1;\n+const next = 1;\n"


def test_combined_comment_collapses_each_file_after_what_changed():
    claims = [
        _Claim("file_changed", "src/login.ts", "src/login.ts is changed in this pull request."),
        _Claim("file_changed", "src/session.ts", "src/session.ts is changed in this pull request."),
    ]
    body = _combined(
        claims=claims,
        patches={"src/login.ts": _LOGIN_PATCH, "src/session.ts": _SESSION_PATCH},
    )
    explain, rest = body.split("## Details for", 1)
    details, review = rest.split("## Review for", 1)
    assert details.index("### What changed") < details.index("### Changes") < details.index("### Change flow")
    assert "<summary>Tests</summary>" in details
    assert "<summary>Unchanged boundary</summary>" in details
    assert details.count("<details>") == 4
    assert "<details open" not in body
    assert "<summary>src/login.ts</summary>" in details
    assert "<summary>src/session.ts</summary>" in details
    assert "```diff\n@@ -4,3 +4,3 @@\n context\n-return token;\n+return session;\n```" in details
    assert "-const old = 1;" in details
    assert "+const next = 1;" in details
    assert "lines 4-6" not in details
    assert "lines 1-2" not in details
    assert "<details>" not in explain
    assert "<details>" not in review
    assert "The rest of this file is on the web run page." not in body


def test_change_flow_is_a_list_and_is_not_sliced_mid_item():
    sections = [
        {
            "heading": "Changed",
            "items": [
                {"text": "RunPage", "detail": None},
                {"text": "DetailsView", "detail": None},
                {"text": "FileChanges", "detail": None},
            ],
        },
        {
            "heading": "RunPage",
            "items": [
                {"text": "calls coverageLabel", "detail": "frontend/src/RunPage.tsx:109"},
                {"text": "calls failedRunPhase", "detail": "frontend/src/RunPage.tsx:70"},
            ],
        },
    ]
    body = _combined(
        sections=sections,
        patches={"src/big.ts": "@@ -1 +1 @@\n-" + ("x" * 70000) + "\n+y\n"},
    )
    assert "### Change flow - **Changed**" not in body
    details = body.split("## Details for", 1)[1].split("## Review for", 1)[0]
    assert "</details>\n\n### Change flow\n" in details
    flow = details.split("### Change flow", 1)[1].split("\n### ", 1)[0]
    assert flow.startswith("\n\n- **Changed**\n")
    assert "\n  - RunPage\n" in flow
    assert "\n  - DetailsView\n" in flow
    assert "\n  - FileChanges\n" in flow
    assert "\n- **RunPage**\n" in flow
    assert "\n  - [calls coverageLabel (frontend/src/RunPage.tsx:109)](" in flow
    assert "\n  - [calls failedRunPhase (frontend/src/RunPage.tsx:70)](" in flow
    items = [line for line in flow.splitlines() if line.lstrip().startswith("- ")]
    assert len(items) >= 6
    assert all(" - **" not in line for line in items)
    url = f"https://github.com/acme/app/blob/{NEW}/frontend/src/RunPage.tsx#L109"
    assert url in flow
    assert url[:-8] + "…" not in body
    assert len(body) <= 60000
    partial = url[:48]
    clipped = _clip_preserving_flow(flow, flow.index(url) + 12)
    assert partial not in clipped or url in clipped
    assert "…" in clipped


def test_combined_comment_truncates_large_diffs_and_keeps_paths():
    claims = [
        _Claim("file_changed", "src/big.ts", "big changed"),
        _Claim("file_changed", "src/small.ts", "small changed"),
    ]
    body = _combined(
        claims=claims,
        patches={
            "src/big.ts": "@@ -1 +1 @@\n-" + ("x" * 70000) + "\n+y\n",
            "src/small.ts": "@@ -1 +1 @@\n-old\n+ok\n",
        },
    )
    assert len(body) <= 60000
    explain, rest = body.split("## Details for", 1)
    details, review = rest.split("## Review for", 1)
    assert f"## Explain for `{NEW}`" in body
    assert f"## Review for `{NEW}`" in body
    assert "### Reviewer Attention" in review
    assert "<summary>src/big.ts</summary>" in details
    assert "<summary>src/small.ts</summary>" in details
    assert "<details open" not in body
    assert "The rest of this file is on the web run page." in details
    assert "+ok" in details
    assert "x" * 70000 not in body
    assert "lines 1-2" not in details


def test_details_comment_collapses_long_sections_and_omits_unknowns():
    claims = [_Claim("tests", f"fn{i}", f"backend/tests/test_fn{i}.py tests fn{i}.") for i in range(22)]
    claims.append(_Claim("unknown_boundary", "Unknown", "No dependency facts are in this packet."))
    claims.append(_Claim("file_changed", "backend/app/llm/provider.py", "backend/app/llm/provider.py is changed in this pull request."))
    claims.append(_Claim("ambiguous_call", "add", "Call to add at backend/app/llm/provider.py:1 is a member call and was not resolved to a function edge."))
    claims.append(_Claim("ambiguous_call", "_subject", "Call to _subject at backend/app/explanation/details.py:2 is ambiguous across 2 definitions."))
    body = _combined(claims=claims, document_unknowns=["No database or schema facts are in this packet."])
    details = body.split("## Details for", 1)[1].split("## Review for", 1)[0]
    assert "### Unknowns" not in details
    assert "No dependency facts are in this packet" not in details
    assert "No database or schema facts are in this packet" not in details
    tests = details.split("<summary>Tests</summary>", 1)[1].split("</details>", 1)[0]
    assert "<details open" not in details
    assert "more</summary>" not in tests
    assert "test_fn0.py" in tests
    assert "test_fn21.py" in tests
    assert "<summary>Unchanged boundary</summary>" in details
    explain_part = body.split("## Details for", 1)[0]
    system = explain_part.split("### System Impact", 1)[1].split("## ", 1)[0]
    assert "test_fn0.py" not in system
    assert "backend/app/llm/provider.py" not in system
    assert "**Risk & Scope**" in system
    assert "### Risk Areas" not in details
    assert "| add |" not in details
    assert "| _subject |" not in details


def test_compare_payload_keeps_added_and_removed_lines():
    patches = patches_from_compare(
        {
            "files": [
                {
                    "filename": "frontend/src/RunPage.tsx",
                    "patch": "@@ -15,6 +15,7 @@\n export function RunPage() {\n-  const [depth, setDepth] = useState(\"developer\");\n+  const [tab, setTab] = useState(\"quick\");\n",
                },
                {"filename": "notes.bin", "status": "modified"},
            ]
        }
    )
    assert patches["frontend/src/RunPage.tsx"].startswith("@@ -15,6 +15,7 @@")
    assert "-  const [depth, setDepth]" in patches["frontend/src/RunPage.tsx"]
    assert "+  const [tab, setTab]" in patches["frontend/src/RunPage.tsx"]
    assert "notes.bin" not in patches
    assert "lines 15-168" not in patches["frontend/src/RunPage.tsx"]
