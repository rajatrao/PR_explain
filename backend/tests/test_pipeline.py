import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.analyzer.fixture import load_oauth_snapshot
from app.analyzer.types import FileChange, Snapshot
from app.api import app
from app.config import get_settings
from app.db.models import (
    AnalysisJob,
    AnalysisRun,
    ClaimRow,
    ExplanationPacketRow,
    GithubInstallation,
    PullRequest,
    Repository,
    Revision,
)
from app.jobs.queue import enqueue_job
from app.llm.provider import LLMResult
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
    enqueue_job(db, run.id, "explain", "quick")
    db.commit()
    process_available_job(db, settings, snapshot_source=source, provider=failed, comment_client=MemoryComments())
    assert source.calls == 1
    assert _claim_count(db, run.id) > 0


def test_explanation_failure_does_not_publish_comment(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    comments = MemoryComments()
    previous = "## Explain for `old`\n\nprevious prose"
    comments.comments[41] = previous
    comments._next = 42
    run.revision.pull_request.explanation_comment_id = 41
    db.commit()
    source = CountingSource(snapshot)
    settings = get_settings()
    failed = ScriptedProvider(error="connection refused")
    process_available_job(db, settings, snapshot_source=source, provider=failed)
    process_available_job(
        db,
        settings,
        snapshot_source=source,
        provider=failed,
        comment_client=comments,
    )
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "succeeded"
    assert stored.explanation_status == "failed"
    assert stored.comment_status == "skipped"
    assert comments.comments[41] == previous
    assert comments.bodies == []
    assert list(comments.comments) == [41]


class _EmptyStatements:
    id = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def explain(self, request):  # noqa: ANN001
        self.calls += 1
        body = {
            "summary": "The API and UI changed.",
            "change_flow": [],
            "impacts": [],
            "important_changes": [],
            "tests": [],
            "unchanged": [],
            "unknowns": [],
            "review_questions": [],
        }
        return LLMResult(content=json.dumps(body), latency_ms=1, model="fake-model")


def test_empty_statements_keep_packet_explanation(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    source = CountingSource(snapshot)
    settings = get_settings()
    settings.app_base_url = "http://explain.example"
    provider = _EmptyStatements()
    comments = MemoryComments()
    process_available_job(db, settings, snapshot_source=source, provider=provider)
    process_available_job(
        db,
        settings,
        snapshot_source=source,
        provider=provider,
        comment_client=comments,
    )
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert provider.calls == 1
    assert stored.analysis_status == "succeeded"
    assert stored.explanation_status == "succeeded"
    assert stored.explanation_error is None
    assert stored.comment_status == "posted"
    document = next(row.document for row in stored.explanations if row.depth == "quick")
    rendered = json.dumps(document)
    assert document["change_flow"]
    assert "createSession" in rendered
    assert "inventedFourthCaller" not in rendered
    assert comments.comments
    assert "connection refused" not in comments.comments[1]


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
    assert len(comments.comments) == 1
    assert "view=explain" in latest
    assert "view=details" not in latest
    assert latest.index("## Explain for") < latest.index("## Details for") < latest.index("## Review for")
    explain_body, rest = latest.split("## Details for", 1)
    details_body, review_body = rest.split("## Review for", 1)
    assert "```mermaid" in explain_body
    # The only diagrams in Details are the old and new flow diagrams, above Key Changes.
    assert "```mermaid" not in details_body.split("### Old flow vs New flow", 1)[0]
    assert "```mermaid" not in review_body
    assert "### Impact" not in details_body
    assert "| Area | Reason | Evidence file |" not in details_body
    assert "### Impact" in explain_body
    assert explain_body.index("### Behavioral Changes") < explain_body.index("### Impact")
    assert "### Reviewer Attention" not in details_body
    assert "### Reviewer Attention" in review_body
    assert "### Review questions" in review_body
    assert "one-hop" not in latest.lower()
    assert "insecure" not in latest.lower()
    assert "score" not in latest.lower()
    assert run_one.id != run_two.id
    delta = run_two.revision.delta
    assert delta is not None
    assert delta.previous_head_sha == first.head_sha
    assert delta.added or delta.removed


class _FailOnceSource:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def fetch(self, full_name: str, base_sha: str, head_sha: str):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("snapshot fetch failed")
        self.snapshot.head_sha = head_sha
        self.snapshot.base_sha = base_sha
        self.snapshot.repository = full_name
        return self.snapshot


def test_failed_analyze_job_becomes_pending_and_is_picked_up(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    head_sha = snapshot.head_sha
    revision_id = run.revision_id
    source = _FailOnceSource(snapshot)
    settings = get_settings()
    failed = process_available_job(db, settings, snapshot_source=source, provider=ScriptedProvider())
    assert failed is not None
    assert failed.status == "failed"
    assert failed.phase == "analyze"
    assert source.calls == 1
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "failed"
    assert stored.revision_id == revision_id
    assert stored.revision.head_sha == head_sha

    client = TestClient(app)
    response = client.post(f"/api/runs/{run.id}/retry")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(run.id)
    assert body["analysis_status"] == "queued"
    assert body["analysis_error"] is None
    assert body["revision"]["head_sha"] == head_sha
    assert any(event["stage"] == "snapshot_fetch" and event["status"] == "failed" for event in body["events"])

    db.expire_all()
    job = db.get(AnalysisJob, failed.id)
    assert job.status == "pending"
    assert job.last_error is None
    assert job.run_id == run.id
    assert db.scalar(select(func.count()).select_from(Revision)) == 1

    picked = process_available_job(db, settings, snapshot_source=source, provider=ScriptedProvider())
    assert picked is not None
    assert picked.id == failed.id
    assert picked.status == "succeeded"
    assert source.calls == 2
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "succeeded"
    assert stored.revision_id == revision_id
    assert stored.revision.head_sha == head_sha
    assert db.scalar(select(func.count()).select_from(Revision)) == 1
    assert _claim_count(db, run.id) > 0


def test_explanation_retry_does_not_refetch_snapshot(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    source = CountingSource(snapshot)
    settings = get_settings()
    failed = ScriptedProvider(error="connection refused")
    process_available_job(db, settings, snapshot_source=source, provider=failed)
    explain_job = process_available_job(
        db,
        settings,
        snapshot_source=source,
        provider=failed,
        comment_client=MemoryComments(),
    )
    assert explain_job.phase == "explain"
    assert source.calls == 1
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "succeeded"
    assert stored.explanation_status == "failed"
    claims_before = _claim_count(db, run.id)
    assert claims_before > 0
    packet = db.scalars(
        select(ExplanationPacketRow).where(ExplanationPacketRow.run_id == run.id)
    ).one()
    packet_id = packet.id
    payload = dict(packet.payload)

    client = TestClient(app)
    response = client.post(f"/api/runs/{run.id}/retry")
    assert response.status_code == 200
    body = response.json()
    assert body["analysis_status"] == "succeeded"
    assert body["explanation_status"] == "queued"
    assert body["explanation_error"] is None
    assert body["revision"]["head_sha"] == snapshot.head_sha
    assert len(body["claims"]) == claims_before
    assert any(event["stage"] == "claims_persisted" for event in body["events"])

    db.expire_all()
    job = db.get(AnalysisJob, explain_job.id)
    assert job.status == "pending"
    assert job.last_error is None
    assert job.phase == "explain"
    assert (
        db.scalar(
            select(func.count())
            .select_from(AnalysisJob)
            .where(AnalysisJob.run_id == run.id, AnalysisJob.phase == "explain")
        )
        == 1
    )

    provider = ScriptedProvider()
    picked = process_available_job(
        db,
        settings,
        snapshot_source=source,
        provider=provider,
        comment_client=MemoryComments(),
    )
    assert picked.id == explain_job.id
    assert picked.status == "succeeded"
    assert source.calls == 1
    assert provider.calls >= 1
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "succeeded"
    assert stored.explanation_status == "succeeded"
    assert _claim_count(db, run.id) == claims_before
    same = db.get(ExplanationPacketRow, packet_id)
    assert same.payload == payload
    assert db.scalar(select(func.count()).select_from(Revision)) == 1


def test_comment_retry_does_not_call_the_provider(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    source = CountingSource(snapshot)
    settings = get_settings()
    settings.app_base_url = "http://explain.example"
    provider = ScriptedProvider()
    process_available_job(db, settings, snapshot_source=source, provider=provider)
    process_available_job(
        db,
        settings,
        snapshot_source=source,
        provider=provider,
        comment_client=MemoryComments(fail=True),
    )
    calls = provider.calls
    assert calls >= 1
    assert source.calls == 1
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.explanation_status == "succeeded"
    assert stored.comment_status == "failed"

    client = TestClient(app)
    response = client.post(f"/api/runs/{run.id}/retry")
    assert response.status_code == 200
    body = response.json()
    assert body["explanation_status"] == "succeeded"
    assert body["comment_status"] == "queued"
    assert body["comment_error"] is None
    assert body["depths"] == ["quick", "deep"]
    assert "developer" not in body["explanations"]
    assert "architecture" not in body["explanations"]
    assert body["explanations"]["quick"]["document"]["summary"]
    assert body["explanations"]["deep"]["document"]["summary"]

    db.expire_all()
    comment_job = db.scalars(
        select(AnalysisJob).where(AnalysisJob.run_id == run.id, AnalysisJob.phase == "comment")
    ).one()
    assert comment_job.status == "pending"
    assert comment_job.last_error is None

    comments = MemoryComments()
    picked = process_available_job(
        db,
        settings,
        snapshot_source=source,
        provider=provider,
        comment_client=comments,
    )
    assert picked.id == comment_job.id
    assert picked.status == "succeeded"
    assert provider.calls == calls
    assert source.calls == 1
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "succeeded"
    assert stored.explanation_status == "succeeded"
    assert stored.comment_status == "posted"
    assert comments.comments


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


def test_queued_run_with_failed_analyze_job_exposes_retry(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    job = db.scalars(select(AnalysisJob).where(AnalysisJob.run_id == run.id)).one()
    job.status = "failed"
    job.last_error = "Could not parse the provided public key."
    db.commit()

    client = TestClient(app)
    detail = client.get(f"/api/runs/{run.id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["analysis_status"] == "failed"
    assert "public key" in body["analysis_error"]

    response = client.post(f"/api/runs/{run.id}/retry")
    assert response.status_code == 200
    retried = response.json()
    assert retried["analysis_status"] == "queued"
    assert retried["analysis_error"] is None
    db.expire_all()
    stored = db.get(AnalysisJob, job.id)
    assert stored.status == "pending"
    assert stored.last_error is None


class _NarratingProvider:
    """Returns the packet document plus a behavioral narrative: one grounded change, one invented."""

    id = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.packets = []

    def explain(self, request):  # noqa: ANN001
        self.calls += 1
        self.packets.append(request.packet)
        # Like the real quick prompt: a summary and empty statement arrays. The pipeline then keeps its
        # packet-built document, and the narrative must still be read from this raw reply.
        document = {
            "summary": "Session creation now takes a TTL.",
            "change_flow": [],
            "impacts": [],
            "important_changes": [],
            "tests": [],
            "unchanged": [],
            "unknowns": [],
            "review_questions": [],
        }
        facts = request.packet.behavior_facts
        session = next(fact for fact in facts if fact.function == "createSession")
        ids = [change.id for change in session.changes]
        document["behavioral_changes"] = {
            "overview": "Session tokens now carry the caller's TTL.",
            "changes": [
                {
                    "title": "Session token format",
                    "before": "Previously, a session token was built from the user id alone.",
                    "after": "With this change, each caller must supply `ttlMs`, and the TTL becomes part of the token.",
                    "impact": "Anything entering through `login`, `googleCallback` or `refreshToken`.",
                    "fact_ids": ids,
                },
                {
                    "title": "Session cache",
                    "before": "Previously, sessions were cached in `redisClient`.",
                    "after": "With this change, they are not cached.",
                    "fact_ids": ids,
                },
            ],
            "watch": [],
        }
        return LLMResult(content=json.dumps(document), latency_ms=1, model="fake-model")


def test_model_behavioral_narrative_is_screened_and_shown(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    source = CountingSource(snapshot)
    settings = get_settings()
    provider = _NarratingProvider()
    comments = MemoryComments()
    process_available_job(db, settings, snapshot_source=source, provider=provider)
    process_available_job(db, settings, snapshot_source=source, provider=provider, comment_client=comments)
    assert provider.calls == 1
    assert provider.packets[0].behavior_facts, "the quick packet carries behavior facts"

    client = TestClient(app)
    body = client.get(f"/api/runs/{run.id}").json()
    section = body["behavioral_changes"]
    assert section["source"] == "model"
    assert [change["title"] for change in section["changes"]] == ["Session token format"]
    posted = comments.bodies[-1]
    assert "**Session token format**" in posted
    assert "redisClient" not in posted
    impact = body["impact"]
    assert impact["scope"].startswith("Several workflows.")
    assert [item["title"] for item in impact["attention"]] == ["Shared function createSession changes its contract"]
    assert [item["entry"] for item in impact["dependents"]] == ["googleCallback", "login", "refreshToken"]
    assert "**Needs attention**" in posted and "**Who depends on this**" in posted
    details = posted.split("## Details for", 1)[1].split("## Review for", 1)[0]
    assert "### Old flow vs New flow" in details
    assert "### Old flow vs New flow" not in posted.split("## Details for", 1)[0]


def test_missing_narrative_reason_is_shown(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    source = CountingSource(snapshot)
    settings = get_settings()
    provider = _EmptyStatements()
    process_available_job(db, settings, snapshot_source=source, provider=provider)
    process_available_job(db, settings, snapshot_source=source, provider=provider, comment_client=MemoryComments())
    body = TestClient(app).get(f"/api/runs/{run.id}").json()
    section = body["behavioral_changes"]
    assert section["source"] == "none"
    assert section["overview"].endswith("Reason: the model returned no behavioral_changes.")
