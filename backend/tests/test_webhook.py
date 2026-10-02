import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import app
from app.db.models import AnalysisJob, AnalysisRun, GithubInstallation, Repository


def _signed(payload: dict, event: str, delivery: str):
    body = json.dumps(payload).encode()
    digest = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "Content-Type": "application/json",
    }
    return body, headers


def test_webhook_queues_analyze_job_and_ignores_closed(db):
    client = TestClient(app)
    opened = {
        "action": "opened",
        "installation": {"id": 9, "account": {"login": "acme"}},
        "repository": {"id": 11, "full_name": "acme/app", "default_branch": "main"},
        "pull_request": {
            "number": 4,
            "title": "Share sessions",
            "body": "narrative",
            "head": {"sha": "b" * 40},
            "base": {"sha": "a" * 40},
        },
    }
    body, headers = _signed(opened, "pull_request", "delivery-1")
    response = client.post("/api/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 1
    job = db.scalars(select(AnalysisJob)).one()
    assert job.phase == "analyze"
    assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 1

    again, headers = _signed(opened, "pull_request", "delivery-1")
    duplicate = client.post("/api/webhooks/github", content=again, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 1

    closed = dict(opened)
    closed["action"] = "closed"
    body, headers = _signed(closed, "pull_request", "delivery-closed")
    ignored = client.post("/api/webhooks/github", content=body, headers=headers)
    assert ignored.status_code == 200
    assert ignored.json()["status"] == "ignored"
    assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 1

    bad = client.post(
        "/api/webhooks/github",
        content=b"{}",
        headers={"X-Hub-Signature-256": "sha256=nope", "X-GitHub-Event": "ping", "X-GitHub-Delivery": "x"},
    )
    assert bad.status_code == 401


def test_installation_delete_removes_runs(db):
    client = TestClient(app)
    opened = {
        "action": "opened",
        "installation": {"id": 3, "account": {"login": "acme"}},
        "repository": {"id": 8, "full_name": "acme/web"},
        "pull_request": {
            "number": 1,
            "head": {"sha": "d" * 40},
            "base": {"sha": "e" * 40},
        },
    }
    body, headers = _signed(opened, "pull_request", "delivery-install")
    assert client.post("/api/webhooks/github", content=body, headers=headers).status_code == 200
    deleted = {"action": "deleted", "installation": {"id": 3, "account": {"login": "acme"}}}
    body, headers = _signed(deleted, "installation", "delivery-delete")
    response = client.post("/api/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200
    db.expire_all()
    assert db.get(GithubInstallation, 3) is None
    assert db.scalar(select(func.count()).select_from(Repository)) == 0
    assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 0
