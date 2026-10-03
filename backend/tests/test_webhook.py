import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.api import app
from app.db.models import (
    AnalysisJob,
    AnalysisRun,
    GithubInstallation,
    PullRequest,
    Repository,
    Revision,
)


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
    db.expire_all()
    stored = db.get(Repository, 11)
    assert stored is not None
    assert stored.full_name == "acme/app"

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


def test_webhook_stores_owner_and_repo_when_full_name_lacks_owner(db):
    client = TestClient(app)
    opened = {
        "action": "synchronize",
        "installation": {"id": 9, "account": {"login": "acme"}},
        "repository": {
            "id": 11,
            "name": "app",
            "full_name": "app",
            "owner": {"login": "acme"},
            "default_branch": "main",
        },
        "pull_request": {
            "number": 4,
            "title": "Share sessions",
            "head": {"sha": "c" * 40},
            "base": {"sha": "a" * 40},
        },
    }
    db.add(GithubInstallation(id=9, account_login="acme"))
    db.add(Repository(id=11, installation_id=9, full_name="app", default_branch="main"))
    db.commit()
    body, headers = _signed(opened, "pull_request", "delivery-owner")
    response = client.post("/api/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    db.expire_all()
    stored = db.get(Repository, 11)
    assert stored is not None
    assert stored.full_name == "acme/app"


def test_pull_request_fills_missing_installation_and_reuses_repository(db):
    client = TestClient(app)
    db.add(Repository(id=555, installation_id=None, full_name="rajatrao/PR_explain", default_branch="main"))
    db.commit()

    opened = {
        "action": "opened",
        "installation": {
            "id": 167345129,
            "account": {"login": "rajatrao", "type": "User"},
        },
        "repository": {
            "id": 555,
            "name": "PR_explain",
            "full_name": "rajatrao/PR_explain",
            "owner": {"login": "rajatrao"},
            "default_branch": "main",
        },
        "pull_request": {
            "number": 8,
            "title": "Link installation",
            "head": {"sha": "b" * 40},
            "base": {"sha": "a" * 40},
        },
    }
    body, headers = _signed(opened, "pull_request", "delivery-link-1")
    first = client.post("/api/webhooks/github", content=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "queued"

    db.expire_all()
    stored = db.get(Repository, 555)
    assert stored is not None
    assert stored.installation_id == 167345129
    installation = db.get(GithubInstallation, 167345129)
    assert installation is not None
    assert installation.account_login == "rajatrao"
    assert installation.account_type == "User"
    assert db.scalar(select(func.count()).select_from(Repository)) == 1
    assert db.scalar(select(func.count()).select_from(GithubInstallation)) == 1

    again = {
        "action": "synchronize",
        "installation": {
            "id": 167345129,
            "account": {"login": "rajatrao", "type": "User"},
        },
        "repository": {
            "id": 555,
            "name": "PR_explain",
            "full_name": "rajatrao/PR_explain",
            "owner": {"login": "rajatrao"},
            "default_branch": "main",
        },
        "pull_request": {
            "number": 8,
            "title": "Link installation",
            "head": {"sha": "c" * 40},
            "base": {"sha": "a" * 40},
        },
    }
    body, headers = _signed(again, "pull_request", "delivery-link-2")
    second = client.post("/api/webhooks/github", content=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["status"] == "queued"

    db.expire_all()
    rows = db.scalars(select(Repository)).all()
    assert len(rows) == 1
    assert rows[0].id == stored.id
    assert rows[0].installation_id == 167345129
    installations = db.scalars(select(GithubInstallation)).all()
    assert len(installations) == 1
    assert installations[0].id == 167345129
    assert installations[0].account_login == "rajatrao"
    assert installations[0].account_type == "User"


def _opened(installation: dict, repository: dict, number: int = 4, head: str = "b" * 40) -> dict:
    return {
        "action": "opened",
        "installation": installation,
        "repository": repository,
        "pull_request": {
            "number": number,
            "title": "From payload",
            "head": {"sha": head},
            "base": {"sha": "a" * 40},
        },
    }


def test_pull_request_creates_installation_and_repository_when_neither_exists(db):
    client = TestClient(app)
    opened = _opened(
        {"id": 42, "account": {"login": "octocat", "type": "User"}},
        {
            "id": 424242,
            "name": "widget",
            "full_name": "octocat/widget",
            "owner": {"login": "octocat"},
            "default_branch": "main",
        },
    )
    body, headers = _signed(opened, "pull_request", "delivery-create-both")
    response = client.post("/api/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "queued"

    db.expire_all()
    installation = db.get(GithubInstallation, 42)
    assert installation is not None
    assert installation.account_login == "octocat"
    assert installation.account_type == "User"
    repo = db.get(Repository, 424242)
    assert repo is not None
    assert repo.full_name == "octocat/widget"
    assert repo.installation_id == 42
    assert repo.default_branch == "main"
    job = db.scalars(select(AnalysisJob)).one()
    assert job.phase == "analyze"
    assert db.scalar(select(func.count()).select_from(GithubInstallation)) == 1
    assert db.scalar(select(func.count()).select_from(Repository)) == 1


def test_pull_request_creates_repository_without_clobbering_installation(db):
    client = TestClient(app)
    db.add(GithubInstallation(id=51, account_login="kept-login", account_type="Organization"))
    db.commit()

    opened = _opened(
        {"id": 51, "account": {"login": "someone-else", "type": "User"}},
        {
            "id": 5151,
            "name": "web",
            "full_name": "someone-else/web",
            "owner": {"login": "someone-else"},
            "default_branch": "main",
        },
    )
    body, headers = _signed(opened, "pull_request", "delivery-repo-only")
    response = client.post("/api/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "queued"

    db.expire_all()
    installation = db.get(GithubInstallation, 51)
    assert installation is not None
    assert installation.account_login == "kept-login"
    assert installation.account_type == "Organization"
    repo = db.get(Repository, 5151)
    assert repo is not None
    assert repo.full_name == "someone-else/web"
    assert repo.installation_id == 51
    assert db.scalar(select(func.count()).select_from(GithubInstallation)) == 1
    assert db.scalar(select(func.count()).select_from(Repository)) == 1


def test_pull_request_fills_null_installation_id_without_clobbering_repository(db):
    client = TestClient(app)
    db.add(
        Repository(
            id=6161,
            installation_id=None,
            full_name="rajatrao/PR_explain",
            default_branch="develop",
        )
    )
    db.commit()

    opened = _opened(
        {"id": 616161, "account": {"login": "rajatrao", "type": "User"}},
        {
            "id": 6161,
            "name": "renamed",
            "full_name": "other/renamed",
            "owner": {"login": "other"},
            "default_branch": "main",
        },
    )
    body, headers = _signed(opened, "pull_request", "delivery-fill-link")
    response = client.post("/api/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "queued"

    db.expire_all()
    repo = db.get(Repository, 6161)
    assert repo is not None
    assert repo.installation_id == 616161
    assert repo.full_name == "rajatrao/PR_explain"
    assert repo.default_branch == "develop"
    installation = db.get(GithubInstallation, 616161)
    assert installation is not None
    assert installation.account_login == "rajatrao"
    assert installation.account_type == "User"
    assert db.scalar(select(func.count()).select_from(Repository)) == 1


def _stored_run(
    db,
    *,
    repo_id: int,
    full_name: str,
    analysis_status: str,
    installation_id: int | None,
    default_branch: str = "main",
    account_login: str = "kept-login",
    account_type: str = "Organization",
):
    if installation_id is not None and db.get(GithubInstallation, installation_id) is None:
        db.add(
            GithubInstallation(
                id=installation_id,
                account_login=account_login,
                account_type=account_type,
            )
        )
    db.add(
        Repository(
            id=repo_id,
            installation_id=installation_id,
            full_name=full_name,
            default_branch=default_branch,
        )
    )
    db.flush()
    pull = PullRequest(repository_id=repo_id, number=repo_id)
    db.add(pull)
    db.flush()
    revision = Revision(
        pull_request_id=pull.id,
        head_sha="b" * 40,
        base_sha="a" * 40,
        title="Stored",
    )
    db.add(revision)
    db.flush()
    run = AnalysisRun(
        revision_id=revision.id,
        analysis_status=analysis_status,
        analysis_error="snapshot failed" if analysis_status == "failed" else None,
        explanation_status="not_started",
        comment_status="pending",
    )
    db.add(run)
    db.flush()
    if analysis_status == "failed":
        db.add(AnalysisJob(run_id=run.id, phase="analyze", status="failed", last_error="snapshot failed"))
    db.commit()
    return run


def _drop_installation(db, installation_id: int) -> None:
    db.execute(text("PRAGMA foreign_keys=OFF"))
    db.execute(text("DELETE FROM github_installations WHERE id = :id"), {"id": installation_id})
    db.commit()
    db.execute(text("PRAGMA foreign_keys=ON"))
    db.expire_all()


def test_api_retry_recreates_missing_installation_from_stored_repository(db):
    client = TestClient(app)
    run = _stored_run(
        db,
        repo_id=77,
        full_name="octocat/hello",
        analysis_status="failed",
        installation_id=770,
        default_branch="trunk",
    )
    _drop_installation(db, 770)
    assert db.get(GithubInstallation, 770) is None
    assert db.get(Repository, 77).installation_id == 770

    response = client.post(f"/api/runs/{run.id}/retry")
    assert response.status_code == 200
    assert response.json()["analysis_status"] == "queued"

    db.expire_all()
    installation = db.get(GithubInstallation, 770)
    assert installation is not None
    assert installation.account_login == "octocat"
    assert installation.account_type is None
    repo = db.get(Repository, 77)
    assert repo.installation_id == 770
    assert repo.full_name == "octocat/hello"
    assert repo.default_branch == "trunk"
    assert db.scalar(select(func.count()).select_from(Repository)) == 1
    assert db.scalar(select(func.count()).select_from(GithubInstallation)) == 1


def test_api_retry_does_not_invent_installation_without_stored_id(db):
    client = TestClient(app)
    run = _stored_run(
        db,
        repo_id=78,
        full_name="octocat/hello",
        analysis_status="failed",
        installation_id=None,
        default_branch="trunk",
    )

    response = client.post(f"/api/runs/{run.id}/retry")
    assert response.status_code == 200
    assert response.json()["analysis_status"] == "queued"

    db.expire_all()
    assert db.scalar(select(func.count()).select_from(GithubInstallation)) == 0
    repo = db.get(Repository, 78)
    assert repo is not None
    assert repo.installation_id is None
    assert repo.full_name == "octocat/hello"
    assert repo.default_branch == "trunk"


def test_api_explanation_and_comment_recreate_missing_installation(db):
    client = TestClient(app)
    explain_run = _stored_run(
        db,
        repo_id=81,
        full_name="acme/explain",
        analysis_status="succeeded",
        installation_id=810,
        default_branch="main",
    )
    comment_run = _stored_run(
        db,
        repo_id=82,
        full_name="acme/comment",
        analysis_status="succeeded",
        installation_id=820,
        default_branch="main",
    )
    _drop_installation(db, 810)
    _drop_installation(db, 820)

    explained = client.post(f"/api/runs/{explain_run.id}/explanations", json={"depth": "quick"})
    commented = client.post(f"/api/runs/{comment_run.id}/comment")
    assert explained.status_code == 200
    assert explained.json()["status"] == "queued"
    assert commented.status_code == 200
    assert commented.json()["status"] == "queued"

    db.expire_all()
    explain_installation = db.get(GithubInstallation, 810)
    comment_installation = db.get(GithubInstallation, 820)
    assert explain_installation is not None
    assert explain_installation.account_login == "acme"
    assert comment_installation is not None
    assert comment_installation.account_login == "acme"
    assert db.get(Repository, 81).full_name == "acme/explain"
    assert db.get(Repository, 82).full_name == "acme/comment"
    assert db.get(Repository, 81).installation_id == 810
    assert db.get(Repository, 82).installation_id == 820


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
