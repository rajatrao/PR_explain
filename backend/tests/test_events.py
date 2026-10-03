import json
import logging

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.analyzer.fixture import load_oauth_snapshot
from app.api import app
from app.config import get_settings
from app.db.models import AnalysisRun, PipelineEvent
from app.jobs.events import record_event
from app.worker import process_available_job
from tests.fakes import CountingSource, MemoryComments, ScriptedProvider
from tests.test_pipeline import _revision
from tests.test_webhook import _signed

ANALYSIS_STAGES = [
    ("job_queued", "succeeded"),
    ("snapshot_fetch", "started"),
    ("snapshot_fetch", "succeeded"),
    ("diff_analysis", "succeeded"),
    ("symbol_analysis", "succeeded"),
    ("change_graph", "succeeded"),
    ("evidence", "succeeded"),
    ("impact", "succeeded"),
    ("claims_persisted", "succeeded"),
    ("explanation_packet_persisted", "succeeded"),
    ("job_queued", "succeeded"),
]

SUCCESS_STAGES = [
    *ANALYSIS_STAGES,
    ("explanation", "started"),
    ("explanation", "succeeded"),
    ("comment", "started"),
    ("comment", "posted"),
]

FAILURE_STAGES = [
    *ANALYSIS_STAGES,
    ("explanation", "started"),
    ("explanation", "failed"),
]


def _stages(session, run_id) -> list[tuple[str, str]]:
    rows = session.scalars(
        select(PipelineEvent)
        .where(PipelineEvent.run_id == run_id)
        .order_by(PipelineEvent.ordinal, PipelineEvent.created_at)
    ).all()
    return [(row.stage, row.status) for row in rows]


def test_configure_logging_emits_pipeline_event(db, capsys):
    from app.logsetup import configure_logging

    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = list(root.handlers)
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    try:
        configure_logging()
        record_event(
            db,
            stage="webhook_received",
            status="succeeded",
            message="Received ping webhook",
        )
        captured = capsys.readouterr()
    finally:
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
    assert "pipeline_event stage=webhook_received" in captured.err
    assert "status=succeeded" in captured.err
    assert "message=Received ping webhook" in captured.err


def test_successful_job_records_stage_order(db, caplog):
    caplog.set_level(logging.INFO, logger="app.jobs.events")
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    source = CountingSource(snapshot)
    settings = get_settings()
    settings.app_base_url = "http://explain.example"
    provider = ScriptedProvider()
    comments = MemoryComments()
    process_available_job(db, settings, snapshot_source=source, provider=provider)
    process_available_job(db, settings, snapshot_source=source, provider=provider, comment_client=comments)
    db.commit()

    assert _stages(db, run.id) == SUCCESS_STAGES
    logged = [
        record.message
        for record in caplog.records
        if record.name == "app.jobs.events" and record.message.startswith("pipeline_event ")
    ]
    assert any(
        f"stage=snapshot_fetch run_id={run.id} head_sha={snapshot.head_sha} status=succeeded" in line
        for line in logged
    )
    stored = db.scalars(select(PipelineEvent).where(PipelineEvent.run_id == run.id)).all()
    for row in stored:
        assert "\n" not in row.message
        if not row.detail:
            continue
        for value in row.detail.values():
            assert isinstance(value, (int, str, bool))
            if isinstance(value, str):
                assert "\n" not in value
                assert len(value) <= 80
    blob = json.dumps([{"message": row.message, "detail": row.detail} for row in stored])
    assert "export function" not in blob
    assert "Bearer" not in blob

    client = TestClient(app)
    response = client.get(f"/api/runs/{run.id}")
    assert response.status_code == 200
    assert [(item["stage"], item["status"]) for item in response.json()["events"]] == SUCCESS_STAGES


def test_explanation_failure_keeps_analysis_stages(db):
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)
    source = CountingSource(snapshot)
    settings = get_settings()
    failed = ScriptedProvider(error="connection refused")
    process_available_job(db, settings, snapshot_source=source, provider=failed)
    process_available_job(
        db,
        settings,
        snapshot_source=source,
        provider=failed,
        comment_client=MemoryComments(),
    )
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "succeeded"
    assert stored.explanation_status == "failed"
    assert stored.comment_status == "skipped"
    stages = _stages(db, run.id)
    assert stages[: len(FAILURE_STAGES)] == FAILURE_STAGES
    assert ("explanation", "succeeded") not in stages
    assert ("comment", "posted") not in stages
    assert ("comment", "skipped") in stages


def test_comment_failure_records_comment_once(db):
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
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "succeeded"
    assert stored.explanation_status == "succeeded"
    assert stored.comment_status == "failed"
    stages = _stages(db, run.id)
    assert stages.count(("comment", "started")) == 1
    assert stages.count(("comment", "failed")) == 1
    assert ("comment", "posted") not in stages
    assert ("explanation", "succeeded") in stages


def test_event_write_failure_does_not_stop_the_pipeline(db, monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="app.jobs.events")
    snapshot = load_oauth_snapshot()
    run = _revision(db, snapshot)

    def explode():
        raise RuntimeError("event store unavailable")

    monkeypatch.setattr(db, "begin_nested", explode)
    source = CountingSource(snapshot)
    settings = get_settings()
    settings.app_base_url = "http://explain.example"
    analyze_job = process_available_job(
        db,
        settings,
        snapshot_source=source,
        provider=ScriptedProvider(),
        comment_client=MemoryComments(),
    )
    explain_job = process_available_job(
        db,
        settings,
        snapshot_source=source,
        provider=ScriptedProvider(),
        comment_client=MemoryComments(),
    )
    assert analyze_job.status == "succeeded"
    assert explain_job.status == "succeeded"
    db.expire_all()
    stored = db.get(AnalysisRun, run.id)
    assert stored.analysis_status == "succeeded"
    assert stored.explanation_status == "succeeded"
    assert stored.comment_status == "posted"
    assert "pipeline event write failed" in caplog.text


def test_webhook_rejection_is_persisted_without_the_payload(db):
    client = TestClient(app)
    opened = {
        "action": "opened",
        "installation": {"id": 9, "account": {"login": "acme"}},
        "repository": {"id": 11, "full_name": "acme/app", "default_branch": "main"},
        "pull_request": {
            "number": 4,
            "title": "Share sessions",
            "body": "do not store this body",
            "head": {"sha": "b" * 40},
            "base": {"sha": "a" * 40},
        },
    }
    body, headers = _signed(opened, "pull_request", "delivery-events")
    queued = client.post("/api/webhooks/github", content=body, headers=headers)
    assert queued.status_code == 200
    run_id = queued.json()["run_id"]

    duplicate = client.post("/api/webhooks/github", content=body, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"

    bad = client.post(
        "/api/webhooks/github",
        content=b'{"token":"secret-token","body":"payload"}',
        headers={"X-Hub-Signature-256": "sha256=nope", "X-GitHub-Event": "ping", "X-GitHub-Delivery": "delivery-bad"},
    )
    assert bad.status_code == 401

    db.commit()
    rows = db.scalars(select(PipelineEvent).order_by(PipelineEvent.ordinal)).all()
    by_stage = [(row.stage, row.status, row.delivery_id, str(row.run_id) if row.run_id else None) for row in rows]
    assert ("webhook_received", "succeeded", "delivery-events", run_id) in by_stage
    assert ("webhook_rejected", "skipped", "delivery-events", run_id) in by_stage
    assert ("webhook_rejected", "failed", "delivery-bad", None) in by_stage
    blob = json.dumps(
        [{"message": row.message, "detail": row.detail, "head_sha": row.head_sha} for row in rows]
    )
    assert "do not store this body" not in blob
    assert "secret-token" not in blob
    assert "payload" not in blob


def _opened_pull(number: int, repo_id: int, full_name: str, head: str, **extra) -> dict:
    payload = {
        "action": "opened",
        "installation": {"id": 9, "account": {"login": "acme"}},
        "repository": {"id": repo_id, "full_name": full_name, "default_branch": "main"},
        "pull_request": {
            "number": number,
            "title": extra.pop("title", "Share sessions"),
            "body": "do not store this body",
            "head": {"sha": head},
            "base": {"sha": "a" * 40},
        },
        "performed_via_github_app": None,
    }
    payload.update(extra)
    return payload


def _run_events(db, run_id: str):
    from uuid import UUID

    return db.scalars(
        select(PipelineEvent)
        .where(PipelineEvent.run_id == UUID(run_id))
        .order_by(PipelineEvent.ordinal, PipelineEvent.created_at)
    ).all()


def test_opened_pull_request_stores_webhook_trace(db):
    client = TestClient(app)
    opened = _opened_pull(4, 21, "acme/trace", "b" * 40)
    body, headers = _signed(opened, "pull_request", "delivery-opened-trace")
    queued = client.post("/api/webhooks/github", content=body, headers=headers)
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"
    run_id = queued.json()["run_id"]

    db.commit()
    rows = _run_events(db, run_id)
    assert rows
    first = rows[0]
    assert first.stage == "webhook_received"
    assert first.status == "succeeded"
    assert first.message == "Received pull_request webhook"
    assert first.created_at is not None
    assert all(row.stage != "AGENT_REPORTED" for row in rows)
    blob = json.dumps([{"message": row.message, "detail": row.detail} for row in rows])
    assert "do not store this body" not in blob


def test_bot_sender_stores_agent_reported_and_user_sender_does_not(db):
    client = TestClient(app)
    bot = _opened_pull(
        5,
        31,
        "acme/bots",
        "c" * 40,
        sender={"login": "dependabot[bot]", "type": "Bot"},
    )
    user = _opened_pull(
        6,
        32,
        "acme/humans",
        "d" * 40,
        title="Written by an AI agent",
        sender={"login": "octocat", "type": "User"},
    )
    app_user = _opened_pull(
        7,
        33,
        "acme/apps",
        "e" * 40,
        sender={"login": "octocat", "type": "User"},
        performed_via_github_app={"id": 99, "slug": "pr-agent", "name": "PR Agent"},
    )

    bot_response = client.post(
        "/api/webhooks/github",
        content=_signed(bot, "pull_request", "delivery-bot")[0],
        headers=_signed(bot, "pull_request", "delivery-bot")[1],
    )
    user_response = client.post(
        "/api/webhooks/github",
        content=_signed(user, "pull_request", "delivery-user")[0],
        headers=_signed(user, "pull_request", "delivery-user")[1],
    )
    app_response = client.post(
        "/api/webhooks/github",
        content=_signed(app_user, "pull_request", "delivery-app")[0],
        headers=_signed(app_user, "pull_request", "delivery-app")[1],
    )
    assert bot_response.status_code == 200
    assert user_response.status_code == 200
    assert app_response.status_code == 200

    db.commit()
    bot_rows = _run_events(db, bot_response.json()["run_id"])
    user_rows = _run_events(db, user_response.json()["run_id"])
    app_rows = _run_events(db, app_response.json()["run_id"])

    bot_agents = [row for row in bot_rows if row.stage == "AGENT_REPORTED"]
    assert len(bot_agents) == 1
    assert bot_agents[0].status == "succeeded"
    assert bot_agents[0].message == "Reported agent dependabot[bot]"
    assert [row.stage for row in bot_rows[:3]] == ["webhook_received", "AGENT_REPORTED", "job_queued"]

    assert all(row.stage != "AGENT_REPORTED" for row in user_rows)
    assert user_rows[0].stage == "webhook_received"
    assert "octocat" not in json.dumps([row.message for row in user_rows])

    app_agents = [row for row in app_rows if row.stage == "AGENT_REPORTED"]
    assert len(app_agents) == 1
    assert app_agents[0].message == "Reported agent pr-agent"
    assert "octocat" not in app_agents[0].message

    blob = json.dumps(
        [
            {"message": row.message, "detail": row.detail}
            for row in (*bot_rows, *user_rows, *app_rows)
        ]
    )
    assert "do not store this body" not in blob
    assert "Written by an AI agent" not in blob
    assert "sender" not in blob
