from app.explanation.schema import EvidenceRef, ExplanationDocument, Statement
from app.github.comment import (
    RETIRED_DETAILS_NOTE,
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
    assert explain.index("```mermaid") < explain.index("### Change flow")
    assert "- Changed" in explain
    assert "  - login" in explain
    assert "### Diagram" not in second
    assert "## Diagram" not in second
    assert "```mermaid" not in details
    assert "```mermaid" not in review
    assert "### Change Overview" in details
    assert "| Area | Reason | Evidence file |" in details
    assert "### Unknowns" in details
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
    "### Change Overview",
    "### High-level areas affected",
    "### Key Changes",
    "### Behavior Changes",
    "### Risk Areas",
    "### What changed",
    "### Change flow",
    "### Impact",
    "### Shared code",
    "### Tests",
    "### Unchanged boundary",
    "### Why a file outside the diff matters",
    "### Unknowns",
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
    claims = [_Claim("symbol_changed", "login", "login changed in src/login.ts.")]
    first = _combined(
        document=_document("first"),
        head_sha=OLD,
        run_id="run-old",
        bullets=["first bullet"],
        evidence_by_id={},
        claims=claims,
        sections=sections,
    )
    explain, rest = first.split("## Details for", 1)
    details, review = rest.split("## Review for", 1)
    places = [details.index(title) for title in _DETAILS_ORDER]
    assert places == sorted(places)
    assert "### Reviewer Attention" not in details
    assert "What changed" in details
    assert "| Area | Reason | Evidence file |" in details
    assert "```mermaid" in explain
    assert "### Diagram" not in explain
    assert review.index("### Reviewer Attention") < review.index("### Review questions")
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


def test_combined_comment_includes_short_trace_and_updates_in_place():
    comments = MemoryComments()
    trace = [
        {
            "stage": "webhook_received",
            "status": "succeeded",
            "created_at": "2026-10-02T23:01:00+00:00",
            "message": "Received pull_request webhook",
            "detail": {"token": "ghp_secret", "body": "do not store this body"},
        },
        {
            "stage": "AGENT_REPORTED",
            "status": "succeeded",
            "created_at": "2026-10-02T23:01:01+00:00",
            "message": "Reported agent dependabot[bot]",
        },
    ]
    first = _combined(head_sha=OLD, run_id="run-old", bullets=["first"], mermaid=None, trace=trace)
    assert "### Trace" in first
    assert first.index("## Review for") < first.index("### Trace")
    assert "webhook received" in first
    assert "succeeded" in first
    assert "2026-10-02T23:01:00+00:00" in first
    assert "Received pull_request webhook" in first
    assert "AGENT REPORTED" in first
    assert "dependabot[bot]" in first
    assert "ghp_secret" not in first
    assert "do not store this body" not in first
    assert "```" not in first
    assert "view=explain" in first
    assert "view=details" not in first

    first_id = publish_combined_comment(comments, "acme/app", 7, first)
    later = _combined(
        bullets=["second"],
        mermaid=None,
        trace=[
            *trace,
            {
                "stage": "comment",
                "status": "started",
                "created_at": "2026-10-02T23:02:00+00:00",
                "message": "Comment started",
            },
        ],
    )
    second_id = publish_combined_comment(comments, "acme/app", 7, later, fallback_id=first_id)
    assert second_id == first_id
    assert len(comments.comments) == 1
    assert comments._next == first_id + 1
    assert "Comment started" in comments.comments[first_id]
    assert OLD not in comments.comments[first_id]
    assert "## Explain for" in comments.comments[first_id]
    assert "## Details for" in comments.comments[first_id]
    assert "## Review for" in comments.comments[first_id]
