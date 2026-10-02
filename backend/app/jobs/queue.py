from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AnalysisJob


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
    return job


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
