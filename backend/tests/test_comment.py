from app.explanation.schema import EvidenceRef, ExplanationDocument, Statement
from app.github.comment import render_pull_request_comment

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


def test_comment_labels_links_and_replaces_sha():
    first = render_pull_request_comment(
        document=_document("first"),
        failure=None,
        repo_full_name="acme/app",
        pr_number=7,
        head_sha=OLD,
        app_base_url="http://explain.example",
        run_id="run-old",
        evidence_by_id=_evidence(),
    )
    second = render_pull_request_comment(
        document=_document("second"),
        failure=None,
        repo_full_name="acme/app",
        pr_number=7,
        head_sha=NEW,
        app_base_url="http://explain.example",
        run_id="run-new",
        evidence_by_id=_evidence(),
    )
    assert "<!-- pr-explain repo=acme/app pr=7 -->" in second
    assert "**FACT**" in second
    assert "**INFERENCE**" in second
    assert "**UNKNOWN**" in second
    assert f"https://github.com/acme/app/blob/{NEW}/src/login.ts#L4" in second
    assert "http://explain.example/runs/run-new" in second
    assert "score" not in second.lower()
    assert NEW in second
    assert OLD not in second
    assert OLD in first


def test_failure_comment_does_not_keep_previous_prose():
    body = render_pull_request_comment(
        document=None,
        failure="connection refused",
        repo_full_name="acme/app",
        pr_number=7,
        head_sha=NEW,
        app_base_url="http://explain.example",
        run_id="run-new",
        evidence_by_id={},
    )
    assert NEW in body
    assert OLD not in body
    assert "login calls createSession" not in body
    assert "connection refused" in body
    assert "score" not in body.lower()
