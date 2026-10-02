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
    assert stored.comment_status == "posted"
    stages = _stages(db, run.id)
    assert stages[: len(FAILURE_STAGES)] == FAILURE_STAGES
    assert ("explanation", "succeeded") not in stages


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
