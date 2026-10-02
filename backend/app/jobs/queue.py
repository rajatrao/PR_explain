from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AnalysisJob, AnalysisRun
from app.jobs.events import record_event


def enqueue_job(
    session: Session,
    run_id: uuid.UUID,
    phase: str,
    depth: str | None = None,
) -> AnalysisJob:
    job = AnalysisJob(
        run_id=run_id,
        phase=phase,
        depth=depth,
        status="queued",
        available_at=datetime.now(timezone.utc),
    )
    session.add(job)
    detail = {"phase": phase}
    if depth:
        detail["depth"] = depth
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


def lock_statement():
    return (
        select(AnalysisJob)
        .where(AnalysisJob.status == "queued")
        .where(AnalysisJob.available_at <= datetime.now(timezone.utc))
        .order_by(AnalysisJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def claim_next_job(session: Session, worker_id: str) -> AnalysisJob | None:
    stmt = lock_statement()
    if session.get_bind().dialect.name != "postgresql":
        stmt = (
            select(AnalysisJob)
            .where(AnalysisJob.status == "queued")
            .where(AnalysisJob.available_at <= datetime.now(timezone.utc))
            .order_by(AnalysisJob.created_at)
            .limit(1)
        )
    job = session.scalars(stmt).first()
    if job is None:
        return None
    job.status = "running"
    job.locked_at = datetime.now(timezone.utc)
    job.locked_by = worker_id
    job.attempts = (job.attempts or 0) + 1
    session.commit()
    return job
