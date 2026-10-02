from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AnalysisRun,
    GithubInstallation,
    PullRequest,
    Repository,
    Revision,
    WebhookDelivery,
)
from app.jobs.queue import enqueue_job

_ANALYZE_ACTIONS = {"opened", "reopened", "synchronize"}


def handle_github_event(session: Session, event: str, payload: dict, delivery_id: str) -> dict:
    if delivery_id:
        existing = session.scalars(
            select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
        ).first()
        if existing is not None:
            return {"status": "duplicate"}
        session.add(WebhookDelivery(delivery_id=delivery_id, event=event or "unknown"))

    action = payload.get("action")
    if event == "installation" and action == "deleted":
        installation_id = (payload.get("installation") or {}).get("id")
        installation = session.get(GithubInstallation, installation_id) if installation_id else None
        if installation is not None:
            session.delete(installation)
        return {"status": "deleted"}

    if event == "installation":
        _upsert_installation(session, payload.get("installation") or {})
        for repo in payload.get("repositories") or []:
            _upsert_repository(session, (payload.get("installation") or {}).get("id"), repo)
        return {"status": "ok"}

    if event == "installation_repositories":
        installation_id = (payload.get("installation") or {}).get("id")
        _upsert_installation(session, payload.get("installation") or {})
        for repo in payload.get("repositories_added") or []:
            _upsert_repository(session, installation_id, repo)
        for repo in payload.get("repositories_removed") or []:
            row = session.get(Repository, repo.get("id"))
            if row is not None:
                session.delete(row)
        return {"status": "ok"}

    if event == "pull_request":
        if action not in _ANALYZE_ACTIONS:
            return {"status": "ignored"}
        return _enqueue_pull_request(session, payload)

    return {"status": "ignored"}


def _upsert_installation(session: Session, installation: dict) -> GithubInstallation | None:
    installation_id = installation.get("id")
    if not installation_id:
        return None
    account = (installation.get("account") or {}).get("login") or installation.get("account_login") or "unknown"
    row = session.get(GithubInstallation, installation_id)
    if row is None:
        row = GithubInstallation(id=installation_id, account_login=account)
        session.add(row)
    else:
        row.account_login = account
    return row


def _upsert_repository(session: Session, installation_id: int | None, repo: dict) -> Repository | None:
    repo_id = repo.get("id")
    full_name = repo.get("full_name")
    if not repo_id or not full_name or not installation_id:
        return None
    _upsert_installation(session, {"id": installation_id, "account": {"login": full_name.split("/")[0]}})
    row = session.get(Repository, repo_id)
    if row is None:
        row = Repository(
            id=repo_id,
            installation_id=installation_id,
            full_name=full_name,
            default_branch=repo.get("default_branch"),
        )
        session.add(row)
    else:
        row.full_name = full_name
        row.installation_id = installation_id
        row.default_branch = repo.get("default_branch") or row.default_branch
    return row


def _enqueue_pull_request(session: Session, payload: dict) -> dict:
    installation = payload.get("installation") or {}
    repository = payload.get("repository") or {}
    pull = payload.get("pull_request") or {}
    repo_row = _upsert_repository(session, installation.get("id"), repository)
    if repo_row is None:
        return {"status": "ignored"}
    number = pull.get("number")
    head_sha = (pull.get("head") or {}).get("sha")
    base_sha = (pull.get("base") or {}).get("sha")
    if not number or not head_sha or not base_sha:
        return {"status": "ignored"}
    session.flush()
    pr = session.scalars(
        select(PullRequest).where(PullRequest.repository_id == repo_row.id, PullRequest.number == number)
    ).first()
    if pr is None:
        pr = PullRequest(repository_id=repo_row.id, number=number)
        session.add(pr)
        session.flush()
    revision = session.scalars(
        select(Revision).where(Revision.pull_request_id == pr.id, Revision.head_sha == head_sha)
    ).first()
    if revision is not None and revision.run is not None:
        return {"status": "exists", "run_id": str(revision.run.id)}
    if revision is None:
        revision = Revision(
            pull_request_id=pr.id,
            head_sha=head_sha,
            base_sha=base_sha,
            title=pull.get("title"),
            body=pull.get("body"),
        )
        session.add(revision)
        session.flush()
    run = AnalysisRun(
        revision_id=revision.id,
        analysis_status="queued",
        explanation_status="not_started",
        comment_status="pending",
    )
    session.add(run)
    session.flush()
    enqueue_job(session, run.id, "analyze", None)
    return {"status": "queued", "run_id": str(run.id)}
