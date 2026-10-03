from __future__ import annotations

import itertools
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models import PipelineEvent

logger = logging.getLogger(__name__)

_BUFFER = "pipeline_event_buffer"
_ORDINALS = itertools.count(1)
_DETAIL_KEYS = {
    "change_count",
    "changed_file_count",
    "claim_count",
    "depth",
    "error_type",
    "evidence_count",
    "file_count",
    "github_event",
    "impact_count",
    "phase",
    "reason",
    "relationship_count",
    "symbol_count",
}
_MESSAGE_LIMIT = 240


def record_event(
    session: Session,
    *,
    stage: str,
    status: str,
    message: str,
    run_id: uuid.UUID | None = None,
    delivery_id: str | None = None,
    head_sha: str | None = None,
    detail: dict | None = None,
) -> None:
    """Append one pipeline event. A write failure is logged and swallowed."""
    try:
        payload = _payload(
            stage=stage,
            status=status,
            message=message,
            run_id=run_id,
            delivery_id=delivery_id,
            head_sha=head_sha,
            detail=detail,
        )
        with session.begin_nested():
            session.add(PipelineEvent(**payload))
        session.info.setdefault(_BUFFER, []).append(payload)
    except Exception:
        _log_write_failure(stage, run_id, head_sha, status)
        return
    _log_event(stage, run_id, head_sha, status, payload["message"])


def restore_pipeline_events(session: Session) -> None:
    """Re-insert events that a rollback removed, without failing the caller."""
    payloads = list(session.info.pop(_BUFFER, []))
    for payload in payloads:
        if _event_present(session, payload.get("id")):
            continue
        try:
            with session.begin_nested():
                session.add(PipelineEvent(**payload))
        except Exception:
            _log_write_failure(
                payload.get("stage", "-"),
                payload.get("run_id"),
                payload.get("head_sha"),
                payload.get("status", "-"),
            )


def _event_present(session: Session, event_id) -> bool:
    if event_id is None:
        return False
    if session.get(PipelineEvent, event_id) is not None:
        return True
    return any(isinstance(obj, PipelineEvent) and obj.id == event_id for obj in session.new)


def save_events(session: Session) -> None:
    """Commit the current transaction, and keep going if that commit fails."""
    try:
        session.commit()
    except Exception:
        _log_write_failure("-", None, None, "-")
        try:
            session.rollback()
        except Exception:
            return


@event.listens_for(Session, "after_commit")
def _clear_event_buffer(session: Session) -> None:
    session.info.pop(_BUFFER, None)


def _payload(
    *,
    stage: str,
    status: str,
    message: str,
    run_id: uuid.UUID | None,
    delivery_id: str | None,
    head_sha: str | None,
    detail: dict | None,
) -> dict:
    return {
        "id": uuid.uuid4(),
        "run_id": run_id,
        "delivery_id": _short(delivery_id, 255),
        "head_sha": _short(head_sha, 64),
        "stage": _short(stage, 64) or "unknown",
        "status": _short(status, 32) or "unknown",
        "message": _message(message),
        "detail": _detail(detail),
        "ordinal": next(_ORDINALS),
        "created_at": datetime.now(timezone.utc),
    }


def _detail(detail: dict | None) -> dict | None:
    if not detail:
        return None
    clean: dict = {}
    for key, value in detail.items():
        if key not in _DETAIL_KEYS:
            continue
        if isinstance(value, bool):
            clean[key] = value
            continue
        if isinstance(value, int):
            if abs(value) <= 1_000_000_000:
                clean[key] = value
            continue
        if isinstance(value, str) and "\n" not in value and len(value) <= 80:
            clean[key] = value
    return clean or None


def _message(message: str) -> str:
    text = " ".join(str(message).split())
    if not text:
        return "Pipeline stage"
    return text[:_MESSAGE_LIMIT]


def _short(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _log_event(stage: str, run_id, head_sha, status: str, message: str) -> None:
    try:
        logger.info(
            "pipeline_event stage=%s run_id=%s head_sha=%s status=%s message=%s",
            stage,
            run_id or "-",
            head_sha or "-",
            status,
            message,
        )
    except Exception:
        return


def _log_write_failure(stage, run_id, head_sha, status) -> None:
    try:
        logger.exception(
            "pipeline event write failed stage=%s run_id=%s head_sha=%s status=%s",
            stage or "-",
            run_id or "-",
            head_sha or "-",
            status or "-",
        )
    except Exception:
        return
