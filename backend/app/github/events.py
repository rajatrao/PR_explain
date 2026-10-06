from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AnalysisRun,
    GithubInstallation,
    PipelineEvent,
    PullRequest,
    Repository,
    Revision,
    WebhookDelivery,
)
from app.jobs.events import record_event
from app.jobs.queue import enqueue_job

logger = logging.getLogger(__name__)

_ANALYZE_ACTIONS = {"opened", "reopened", "synchronize"}


def handle_github_event(session: Session, event: str, payload: dict, delivery_id: str) -> dict:
    if delivery_id:
        existing = session.scalars(
            select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
        ).first()
        if existing is not None:
            run_id, head_sha = _delivery_run(session, delivery_id)
            record_event(
                session,
                stage="webhook_rejected",
                status="skipped",
                message="Ignored duplicate webhook delivery",
                run_id=run_id,
                delivery_id=delivery_id,
                head_sha=head_sha,
                detail={"reason": "duplicate"},
            )
            return {"status": "duplicate"}
        session.add(WebhookDelivery(delivery_id=delivery_id, event=event or "unknown"))

    action = payload.get("action")
    logger.info("enqueue_pull_request event=%s", (event or "-")[:64])
    if event == "pull_request" and action in _ANALYZE_ACTIONS:
        result = _enqueue_pull_request(session, payload, delivery_id)
        print("enqueue_pull_request done")
        logger.info("enqueue_pull_request done")
        return result

    _record_received(session, delivery_id, event, None, None)
    return _dispatch(session, event, payload, action)


def _dispatch(session: Session, event: str, payload: dict, action: str | None) -> dict:
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

    return {"status": "ignored"}


def _upsert_installation(
    session: Session,
    installation: dict,
    *,
    fill_missing: bool = False,
) -> GithubInstallation | None:
    installation_id = installation.get("id")
    if not installation_id:
        return None
    account = (installation.get("account") or {}).get("login") or installation.get("account_login") or "unknown"
    account_type = _account_type(installation)
    row = session.get(GithubInstallation, installation_id)
    if row is None:
        row = GithubInstallation(id=installation_id, account_login=account, account_type=account_type)
        session.add(row)
    elif fill_missing:
        if account and (not row.account_login or row.account_login == "unknown"):
            row.account_login = account
        if account_type and not row.account_type:
            row.account_type = account_type
    else:
        row.account_login = account
        if account_type:
            row.account_type = account_type
    return row


def _account_type(installation: dict) -> str | None:
    account = installation.get("account")
    raw = account.get("type") if isinstance(account, dict) else None
    if raw is None:
        raw = installation.get("account_type")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text[:32] or None


def _payload_installation_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _link_pull_request_installation(session: Session, payload: dict) -> Repository | None:
    """Save installation.id from a pull_request payload onto the repository.

    Runs before the analyze job is queued so the worker can mint a token.
    Creates the installation and repository from the payload when either row
    is missing. An existing row keeps its stored fields; only a null
    installation_id is filled in.
    """
    installation = payload.get("installation") or {}
    if not isinstance(installation, dict):
        installation = {}
    repository = payload.get("repository") or {}
    if not isinstance(repository, dict):
        repository = {}
    installation_id = _payload_installation_id(installation.get("id"))

    full_name = _owner_repo_name(repository)
    repo_id = _payload_installation_id(repository.get("id"))
    if full_name:
        repo_row = _repository_row(session, repo_id, full_name)
    elif repo_id:
        repo_row = session.get(Repository, repo_id)
    else:
        repo_row = None

    if installation_id is not None:
        _upsert_installation(
            session,
            _installation_payload(installation, installation_id, full_name),
            fill_missing=True,
        )
        session.flush()

    if repo_row is None and installation_id is not None and repo_id and full_name:
        repo_row = Repository(
            id=repo_id,
            installation_id=installation_id,
            full_name=full_name,
            default_branch=_branch_name(repository),
        )
        session.add(repo_row)
    elif repo_row is not None:
        if installation_id is not None and repo_row.installation_id is None:
            repo_row.installation_id = installation_id
        if full_name and "/" in full_name and "/" not in (repo_row.full_name or ""):
            repo_row.full_name = full_name
        if not repo_row.default_branch:
            branch = _branch_name(repository)
            if branch:
                repo_row.default_branch = branch
    return repo_row


def _installation_payload(installation: dict, installation_id: int, full_name: str | None) -> dict:
    account = installation.get("account") if isinstance(installation.get("account"), dict) else {}
    login = account.get("login") if isinstance(account.get("login"), str) else None
    if not (login and login.strip()) and full_name and "/" in full_name:
        login = full_name.split("/", 1)[0]
    payload = {**installation, "id": installation_id}
    if login and login.strip():
        payload["account"] = {**account, "login": login.strip()}
    return payload


def ensure_stored_repository(session: Session, run: AnalysisRun) -> Repository | None:
    """Run-id API routes do not receive a GitHub pull_request payload.

    Do not invent an installation id or a repository id. The webhook path
    creates both rows from the payload. Here, use only the repository already
    stored on the run. Create a missing github_installations row only when
    that repository still has an installation id and an owner/name full name.
    A null installation_id stays null.
    """
    revision = run.revision if run.revision is not None else session.get(Revision, run.revision_id)
    if revision is None:
        return None
    pull = revision.pull_request
    if pull is None and revision.pull_request_id is not None:
        pull = session.get(PullRequest, revision.pull_request_id)
    if pull is None:
        return None
    repo = pull.repository
    if repo is None and pull.repository_id is not None:
        repo = session.get(Repository, pull.repository_id)
    if repo is None or not repo.id or not repo.full_name:
        return repo
    installation_id = _payload_installation_id(repo.installation_id)
    if installation_id is None or "/" not in repo.full_name:
        return repo
    if session.get(GithubInstallation, installation_id) is None:
        owner = repo.full_name.split("/", 1)[0].strip()
        if not owner:
            return repo
        session.add(GithubInstallation(id=installation_id, account_login=owner))
        session.flush()
    return repo


def _owner_repo_name(repo: dict) -> str | None:
    """owner/name used by the GitHub tarball path."""
    owner = repo.get("owner") if isinstance(repo.get("owner"), dict) else {}
    login = owner.get("login")
    name = repo.get("name")
    if isinstance(login, str) and login.strip() and isinstance(name, str) and name.strip():
        return f"{login.strip()}/{name.strip()}"
    full_name = repo.get("full_name")
    if isinstance(full_name, str) and "/" in full_name:
        owner_name, repo_name = full_name.split("/", 1)
        owner_name, repo_name = owner_name.strip(), repo_name.strip()
        if owner_name and repo_name:
            return f"{owner_name}/{repo_name}"
    return None


def _branch_name(repo: dict) -> str | None:
    branch = repo.get("default_branch")
    if isinstance(branch, str) and branch.strip():
        return branch.strip()
    return None


def _repository_row(session: Session, repo_id: int | None, full_name: str) -> Repository | None:
    """Find a repository by GitHub repo id or by owner/name.

    The payload id wins. A full_name hit is used only when it is that same row,
    so an older row with a different id is not reused.
    """
    if repo_id:
        by_id = session.get(Repository, repo_id)
        if by_id is not None:
            return by_id
    by_name = session.scalars(select(Repository).where(Repository.full_name == full_name)).first()
    if by_name is None:
        return None
    if repo_id and by_name.id != repo_id:
        return None
    return by_name


def _upsert_repository(session: Session, installation_id: int | None, repo: dict) -> Repository | None:
    repo_id = repo.get("id")
    full_name = _owner_repo_name(repo)
    if not full_name or not installation_id:
        return None
    _upsert_installation(session, {"id": installation_id, "account": {"login": full_name.split("/", 1)[0]}})
    branch = _branch_name(repo)
    row = _repository_row(session, repo_id, full_name)
    if row is None:
        if not repo_id:
            return None
        row = Repository(
            id=repo_id,
            installation_id=installation_id,
            full_name=full_name,
            default_branch=branch,
        )
        session.add(row)
    else:
        row.full_name = full_name
        row.installation_id = installation_id
        row.default_branch = branch or row.default_branch
    return row


def _record_received(session: Session, delivery_id: str, event: str, run_id, head_sha) -> None:
    label = (event or "github").strip()[:64] or "github"
    record_event(
        session,
        stage="webhook_received",
        status="succeeded",
        message=f"Received {label} webhook",
        run_id=run_id,
        delivery_id=delivery_id or None,
        head_sha=head_sha,
        detail={"github_event": label},
    )


def _record_reported_agent(session: Session, payload: dict, delivery_id: str, run_id, head_sha) -> None:
    """One line when the payload itself names a GitHub App, bot, or agent."""
    login = _explicit_agent_login(payload)
    if not login or run_id is None:
        return
    record_event(
        session,
        stage="AGENT_REPORTED",
        status="succeeded",
        message=f"Reported agent {login}",
        run_id=run_id,
        delivery_id=delivery_id or None,
        head_sha=head_sha,
    )


def _explicit_agent_login(payload: dict) -> str | None:
    """Read sender.type, sender.login, or performed_via_github_app. Do not infer."""
    if not isinstance(payload, dict):
        return None
    sender = payload.get("sender")
    if not isinstance(sender, dict):
        sender = {}
    login = _actor_login(sender.get("login"))
    sender_type = sender.get("type")
    type_text = sender_type.strip().lower() if isinstance(sender_type, str) else ""
    if type_text == "bot" and login:
        return login
    if login and login.lower().endswith("[bot]"):
        return login
    return _app_login(payload.get("performed_via_github_app"))


def _app_login(app: object) -> str | None:
    if not isinstance(app, dict) or not app:
        return None
    slug = _actor_login(app.get("slug"))
    if slug:
        return slug
    return _actor_login(app.get("name"))


def _actor_login(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    lowered = text.lower()
    if "bearer " in lowered or "ghp_" in text or "ghs_" in text or "github_pat_" in text:
        return None
    return text[:80]


def _delivery_run(session: Session, delivery_id: str):
    prior = session.scalars(
        select(PipelineEvent)
        .where(PipelineEvent.delivery_id == delivery_id, PipelineEvent.run_id.is_not(None))
        .order_by(PipelineEvent.ordinal.desc())
    ).first()
    if prior is None:
        return None, None
    return prior.run_id, prior.head_sha


def _enqueue_pull_request(session: Session, payload: dict, delivery_id: str) -> dict:
    installation = payload.get("installation") or {}
    repository = payload.get("repository") or {}
    pull = payload.get("pull_request") or {}
    linked = _link_pull_request_installation(session, payload)
    repo_row = linked
    if repo_row is None:
        repo_row = _upsert_repository(session, installation.get("id"), repository)
    if repo_row is None:
        _record_received(session, delivery_id, "pull_request", None, None)
        return {"status": "ignored"}
    number = pull.get("number")
    head_sha = (pull.get("head") or {}).get("sha")
    base_sha = (pull.get("base") or {}).get("sha")
    print("number", number)
    print("head_sha", head_sha)
    print("base_sha", base_sha)
    logger.info(
        "pull_request number=%s head_sha=%s base_sha=%s",
        number or "-",
        head_sha or "-",
        base_sha or "-",
    )
    if not number or not head_sha or not base_sha:
        _record_received(session, delivery_id, "pull_request", None, None)
        return {"status": "ignored"}
    session.flush()
    pr = session.scalars(
        select(PullRequest).where(PullRequest.repository_id == repo_row.id, PullRequest.number == number)
    ).first()
    # pr = None
    # pr = session.scalars(
    #     select(PullRequest).where(PullRequest.repository_id == repo_row.id, PullRequest.number == number)
    # ).first()
    if pr is None:
        #pr = PullRequest(repository_id=repo_row.id, number=number)
        pr = PullRequest(repository_id=repo_row.id, number=number)
        session.add(pr)
        session.flush()
    revision = session.scalars(
        select(Revision).where(Revision.pull_request_id == pr.id, Revision.head_sha == head_sha)
    ).first()
    if revision is not None and revision.run is not None:
        _record_received(session, delivery_id, "pull_request", revision.run.id, head_sha)
        _record_reported_agent(session, payload, delivery_id, revision.run.id, head_sha)
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
    _record_received(session, delivery_id, "pull_request", run.id, head_sha)
    _record_reported_agent(session, payload, delivery_id, run.id, head_sha)
    enqueue_job(session, run.id, "analyze")
    return {"status": "queued", "run_id": str(run.id)}
