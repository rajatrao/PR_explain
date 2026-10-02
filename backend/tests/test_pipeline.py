import uuid

from sqlalchemy import func, select

from app.analyzer.fixture import load_oauth_snapshot
from app.analyzer.types import FileChange, Snapshot
from app.config import get_settings
from app.db.models import (
    AnalysisJob,
    AnalysisRun,
    ClaimRow,
    GithubInstallation,
    PullRequest,
    Repository,
    Revision,
)
from app.jobs.queue import enqueue_job
from app.worker import process_available_job
from tests.fakes import CountingSource, MemoryComments, ScriptedProvider


def _revision(session, snapshot: Snapshot, installation_id: int = 1, repo_id: int = 2) -> AnalysisRun:
    if session.get(GithubInstallation, installation_id) is None:
        session.add(GithubInstallation(id=installation_id, account_login="fixture"))
        session.add(
            Repository(
                id=repo_id,
                installation_id=installation_id,
                full_name=snapshot.repository,
            )
        )
        session.flush()
    pr = session.scalars(select(PullRequest).where(PullRequest.repository_id == repo_id)).first()
    if pr is None:
        pr = PullRequest(repository_id=repo_id, number=snapshot.pr_number or 7)
        session.add(pr)
        session.flush()
    revision = Revision(
        pull_request_id=pr.id,
        head_sha=snapshot.head_sha,
        base_sha=snapshot.base_sha,
        title=snapshot.pr_title,
        body=snapshot.pr_body,
    )
    session.add(revision)
    session.flush()
    run = AnalysisRun(revision_id=revision.id, analysis_status="queued")
    session.add(run)
    session.flush()
    enqueue_job(session, run.id, "analyze")
    session.commit()
    return run


def _claim_count(session, run_id) -> int:
    return session.scalar(select(func.count()).select_from(ClaimRow).where(ClaimRow.run_id == run_id))


def test_explanation_failure_keeps_claims_and_retry_does_not_refetch(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    source = CountingSource(snapshot)
    settings = get_settings()
    failed = ScriptedProvider(error="connection refused")
    analyze_job = process_available_job(db, settings, snapshot_source=source, provider=failed)
    assert analyze_job.status == "succeeded"
    assert source.calls == 1
    explain_job = process_available_job(
        db,
        settings,
        snapshot_source=source,
        provider=failed,
        comment_client=MemoryComments(),
    )
    assert explain_job.phase == "explain"
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "succeeded"
    assert stored.explanation_status == "failed"
    assert "connection refused" in stored.explanation_error
    assert _claim_count(db, run.id) > 0
    assert any(row.subject == "createSession" and row.kind == "calls" for row in stored.claims) or any(
        "calls createSession" in row.text for row in stored.claims
    )
    enqueue_job(db, run.id, "explain", "developer")
    db.commit()
    process_available_job(db, settings, snapshot_source=source, provider=failed, comment_client=MemoryComments())
    assert source.calls == 1
    assert _claim_count(db, run.id) > 0


def test_comment_failure_keeps_analysis_and_explanation(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    source = CountingSource(snapshot)
    settings = get_settings()
    settings.app_base_url = "http://explain.example"
    process_available_job(db, settings, snapshot_source=source, provider=ScriptedProvider())
    process_available_job(
        db,
        settings,
        snapshot_source=source,
        provider=ScriptedProvider(),
        comment_client=MemoryComments(fail=True),
    )
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "succeeded"
    assert stored.explanation_status == "succeeded"
    assert stored.comment_status == "failed"
    assert _claim_count(db, run.id) > 0
    assert stored.explanations[0].document["summary"]


def test_new_sha_replaces_comment_body(db):
    first = load_oauth_snapshot()
    second = Snapshot(
        repository=first.repository,
        base_sha=first.base_sha,
        head_sha="c" * 40,
        files=dict(first.files),
        changes=[
            *first.changes,
            FileChange(
                path="src/health.ts",
                status="modified",
                patch="@@ -1,3 +1,3 @@\n export function health(): string {\n-  return \"ok\";\n+  return \"ready\";\n }\n",
            ),
        ],
        pr_number=first.pr_number,
        pr_title=first.pr_title,
        pr_body=first.pr_body,
    )
    second.files["src/health.ts"] = 'export function health(): string {\n  return "ready";\n}\n'
    comments = MemoryComments()
    provider = ScriptedProvider()
    settings = get_settings()
    settings.app_base_url = "http://explain.example"
    source = CountingSource(first)
    run_one = _revision(db, first)
    process_available_job(db, settings, snapshot_source=source, provider=provider)
    process_available_job(db, settings, snapshot_source=source, provider=provider, comment_client=comments)
    assert first.head_sha in comments.comments[1]

    source.snapshot = second
    run_two = _revision(db, second)
    process_available_job(db, settings, snapshot_source=source, provider=provider)
    process_available_job(db, settings, snapshot_source=source, provider=provider, comment_client=comments)
    latest = comments.comments[1]
    assert second.head_sha in latest
    assert first.head_sha not in latest
    assert run_one.id != run_two.id
    delta = run_two.revision.delta
    assert delta is not None
    assert delta.previous_head_sha == first.head_sha
    assert delta.added or delta.removed


def test_unconfigured_provider_fails_explanation_only(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    source = CountingSource(snapshot)
    settings = get_settings()
    process_available_job(db, settings, snapshot_source=source, provider=ScriptedProvider())
    process_available_job(db, settings, snapshot_source=source, provider=None, comment_client=MemoryComments())
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "succeeded"
    assert stored.explanation_status == "failed"
    assert stored.explanation_error == "Ollama is not configured"
    assert _claim_count(db, run.id) > 0
    assert source.calls == 1
    jobs = db.scalars(select(AnalysisJob).where(AnalysisJob.run_id == run.id)).all()
    assert any(job.phase == "explain" and job.status == "succeeded" for job in jobs)
