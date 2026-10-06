from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AnalysisJob, AnalysisRun
from app.jobs.events import record_event


class RetryNotAvailable(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def enqueue_job(
    session: Session,
    run_id: uuid.UUID,
    phase: str,
) -> AnalysisJob:
    job = AnalysisJob(
        run_id=run_id,
        phase=phase,
        status="queued",
        available_at=datetime.now(timezone.utc),
    )
    session.add(job)
    detail = {"phase": phase}
    record_event(
        session,
        stage="job_queued",
        status="succeeded",
        message=f"Queued {phase} job",
        run_id=run_id,
        head_sha=_head_sha(session, run_id),
        detail=detail,
    )
    return job


def _head_sha(session: Session, run_id: uuid.UUID) -> str | None:
    run = session.get(AnalysisRun, run_id)
    if run is None or run.revision is None:
        return None
    return run.revision.head_sha


def requeue_failed_job(session: Session, run: AnalysisRun) -> AnalysisJob:
    """Reset the failed phase on this run so the worker can claim it again."""
    phase = failed_phase(session, run)
    if phase is None:
        raise RetryNotAvailable("no failed phase to retry")
    if phase == "explain" and run.analysis_status != "succeeded":
        raise RetryNotAvailable("analysis has not succeeded")
    if phase == "comment" and run.explanation_status != "succeeded":
        raise RetryNotAvailable("explanation has not succeeded")

    job = _latest_job(session, run.id, phase)
    if job is not None and job.status in {"queued", "pending", "running"}:
        raise RetryNotAvailable("job is already queued")
    created = job is None
    if created:
        job = enqueue_job(session, run.id, phase)
    job.status = "pending"
    job.last_error = None
    job.locked_at = None
    job.locked_by = None
    job.available_at = datetime.now(timezone.utc)
    if phase == "analyze":
        run.analysis_status = "queued"
        run.analysis_error = None
    elif phase == "explain":
        run.explanation_status = "queued"
        run.explanation_error = None
    else:
        run.comment_status = "queued"
        run.comment_error = None
    if not created:
        record_event(
            session,
            stage="job_queued",
            status="succeeded",
            message=f"Queued {phase} job",
            run_id=run.id,
            head_sha=_head_sha(session, run.id),
            detail={"phase": phase, "reason": "retry"},
        )
    return job


def failed_phase(session: Session, run: AnalysisRun) -> str | None:
    """The phase a retry should rerun.

    A failed job counts even when the run status column was left at queued.
    """
    column = _column_failed_phase(run)
    if column is not None:
        return column
    for phase, status in (
        ("analyze", run.analysis_status),
        ("explain", run.explanation_status),
        ("comment", run.comment_status),
    ):
        if status in {"succeeded", "posted"}:
            continue
        job = _latest_job(session, run.id, phase)
        if job is not None and job.status == "failed":
            return phase
    return None


def phase_error(session: Session, run: AnalysisRun, phase: str) -> str | None:
    if phase == "analyze":
        error = run.analysis_error
    elif phase == "explain":
        error = run.explanation_error
    else:
        error = run.comment_error
    if error:
        return error
    job = _latest_job(session, run.id, phase)
    if job is not None and job.status == "failed":
        return job.last_error
    return None


def _column_failed_phase(run: AnalysisRun) -> str | None:
    if run.analysis_status == "failed":
        return "analyze"
    if run.explanation_status == "failed":
        return "explain"
    if run.comment_status == "failed":
        return "comment"
    return None


def _latest_job(session: Session, run_id: uuid.UUID, phase: str) -> AnalysisJob | None:
    return session.scalars(
        select(AnalysisJob)
        .where(AnalysisJob.run_id == run_id, AnalysisJob.phase == phase)
        .order_by(AnalysisJob.created_at.desc())
    ).first()


def _ready_jobs():
    return (
        select(AnalysisJob)
        .where(AnalysisJob.status.in_(("queued", "pending")))
        .where(AnalysisJob.available_at <= datetime.now(timezone.utc))
        .order_by(AnalysisJob.created_at)
    )


def lock_statement():
    return _ready_jobs().limit(1).with_for_update(skip_locked=True)


def claim_next_job(session: Session, worker_id: str) -> AnalysisJob | None:
    stmt = lock_statement()
    if session.get_bind().dialect.name != "postgresql":
        stmt = _ready_jobs().limit(1)
    job = session.scalars(stmt).first()
    if job is None:
        return None
    job.status = "running"
    job.locked_at = datetime.now(timezone.utc)
    job.locked_by = worker_id
    job.attempts = (job.attempts or 0) + 1
    session.commit()
    return job
