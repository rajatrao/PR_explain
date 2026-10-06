from __future__ import annotations

import logging
import time

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import AnalysisJob, AnalysisRun
from app.db.session import session_factory
from app.github.auth import installation_token
from app.github.client import GitHubClient, GithubSnapshotSource
from app.jobs.events import record_event, restore_pipeline_events
from app.jobs.pipeline import execute_analyze, execute_comment, execute_explain
from app.jobs.queue import claim_next_job
from app.logsetup import configure_logging
from app.llm.provider import LLMConfigError, LLMProvider, create_llm_provider


logger = logging.getLogger("app.worker")


class _FailingProvider:
    """Provider used when no LLM is configured; every call fails with the configuration error so the run records it."""

    id = "unconfigured"

    def __init__(self, message: str) -> None:
        self._message = message

    def explain(self, request):  # noqa: ANN001
        """Raise the configuration error."""
        raise LLMConfigError(self._message)


def process_available_job(
    session: Session,
    settings: Settings,
    snapshot_source=None,
    comment_client=None,
    provider: LLMProvider | None = None,
) -> AnalysisJob | None:
    job = claim_next_job(session, settings.worker_id)
    if job is None:
        return None
    try:
        _run_job(session, job, settings, snapshot_source, comment_client, provider)
        current = session.get(AnalysisJob, job.id)
        if current is not None and current.status == "running":
            current.status = "succeeded"
            current.last_error = None
            session.commit()
    except Exception as exc:
        session.rollback()
        restore_pipeline_events(session)
        current = session.get(AnalysisJob, job.id)
        if current is not None and current.status == "running":
            current.status = "failed"
            current.last_error = str(exc)[:2000]
            if current.phase == "analyze":
                failed_run = session.get(AnalysisRun, current.run_id)
                if failed_run is not None and failed_run.analysis_status not in {"succeeded", "failed"}:
                    failed_run.analysis_status = "failed"
                    failed_run.analysis_error = str(exc)[:2000]
            session.commit()
    return session.get(AnalysisJob, job.id)


class _UnavailableCommentClient:
    """Stand-in used when the installation token cannot be minted.

    Explanation still finishes. The comment step records this error.
    """

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def list_comments(self, full_name: str, pr_number: int) -> list[dict]:
        """Raise the reason the comment client is unavailable."""
        raise RuntimeError(self._reason)

    def create_comment(self, full_name: str, pr_number: int, body: str) -> int:
        """Raise the reason the comment client is unavailable."""
        raise RuntimeError(self._reason)

    def update_comment(self, full_name: str, comment_id: int, body: str) -> None:
        """Raise the reason the comment client is unavailable."""
        raise RuntimeError(self._reason)


def _run_job(session, job: AnalysisJob, settings, snapshot_source, comment_client, provider) -> None:
    run = session.get(AnalysisRun, job.run_id)
    if run is None:
        raise LookupError("analysis run missing for job")
    if job.phase == "analyze":
        revision = run.revision
        repository = revision.pull_request.repository
        head_sha = revision.head_sha
        record_event(
            session,
            stage="snapshot_fetch",
            status="started",
            message="Fetching repository snapshot",
            run_id=run.id,
            head_sha=head_sha,
        )
        try:
            source = snapshot_source or _github_source(settings, repository.installation_id)
            snapshot = source.fetch(repository.full_name, revision.base_sha, revision.head_sha)
        except Exception as exc:
            record_event(
                session,
                stage="snapshot_fetch",
                status="failed",
                message="Snapshot fetch failed",
                run_id=run.id,
                head_sha=head_sha,
                detail={"error_type": type(exc).__name__},
            )
            raise
        record_event(
            session,
            stage="snapshot_fetch",
            status="succeeded",
            message="Fetched repository snapshot",
            run_id=run.id,
            head_sha=head_sha,
            detail={"file_count": len(snapshot.files), "change_count": len(snapshot.changes)},
        )
        execute_analyze(session, run.id, snapshot, settings)
        return
    comment_client, owned_client = _comment_client_for(settings, run, job, comment_client)
    try:
        if job.phase == "explain":
            chosen = provider if provider is not None else _provider_or_failure(settings)
            execute_explain(session, run.id, chosen, settings, comment_client)
            return
        if job.phase == "comment":
            execute_comment(session, run.id, settings, comment_client)
            return
        raise RuntimeError(f"unknown job phase {job.phase}")
    finally:
        if owned_client is not None:
            owned_client.close()


def _comment_client_for(settings: Settings, run: AnalysisRun, job: AnalysisJob, comment_client):
    """Use the injected client, or open an installation client for the PR comment.

    Tests pass a client. The worker process does not, which used to skip the
    comment after a successful explanation.
    """
    needs_comment = job.phase in {"comment", "explain"}
    if not needs_comment or comment_client is not None:
        return comment_client, None
    installation_id = run.revision.pull_request.repository.installation_id
    try:
        token = installation_token(settings, installation_id)
    except Exception as exc:
        logger.info("comment client unavailable: %s", type(exc).__name__)
        return _UnavailableCommentClient(_client_error(exc)), None
    client = GitHubClient(token=token, api_url=settings.github_api_url)
    return client, client


def _client_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"GitHub token request failed with HTTP {exc.response.status_code}"
    text = str(exc)
    lowered = text.lower()
    if "bearer" in lowered or "token" in lowered or "private key" in lowered:
        return type(exc).__name__
    return f"{type(exc).__name__}: {text[:240]}"


def _provider_or_failure(settings: Settings):
    try:
        return create_llm_provider(settings)
    except LLMConfigError as exc:
        return _FailingProvider(str(exc))


def _github_source(settings: Settings, installation_id: int) -> GithubSnapshotSource:
    token = installation_token(settings, installation_id)
    return GithubSnapshotSource(GitHubClient(token=token, api_url=settings.github_api_url))


def main() -> None:
    settings = get_settings()
    if settings.run_migrations:
        from app.db.migrate import upgrade

        upgrade()
    configure_logging()
    logger.info("application logging configured")
    from app.bootstrap import bootstrap_on_startup

    bootstrap_on_startup(settings)
    factory = session_factory()
    while True:
        with factory() as session:
            job = process_available_job(session, settings)
        if job is None:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
