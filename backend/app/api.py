from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.analyzer.diagram import build_change_flow
from app.analyzer.types import Snapshot
from app.config import get_settings
from app.db.models import AnalysisRun, PipelineEvent, PullRequest, Revision, RevisionDelta
from app.db.session import get_db
from app.explanation.details import build_details
from app.explanation.narrate import compose_document, explain_bullets
from app.explanation.select import build_packet
from app.explanation.validate import validate_response
from app.jobs.store import load_result
from app.github.events import ensure_stored_repository, handle_github_event
from app.github.webhook import verify_signature
from app.jobs.events import record_event, save_events
from app.jobs.queue import RetryNotAvailable, enqueue_job, failed_phase, phase_error, requeue_failed_job
from app.logsetup import configure_logging

logger = logging.getLogger(__name__)

DEPTHS = ("quick", "deep")
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class DepthBody(BaseModel):
    depth: str = Field(default="quick")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    if settings.run_migrations:
        from app.db.migrate import upgrade

        upgrade()
    configure_logging()
    logger.info("application logging configured")
    from app.bootstrap import bootstrap_on_startup

    bootstrap_on_startup(settings)
    yield


app = FastAPI(title="PR Explain", lifespan=lifespan)
_settings = get_settings()
_origins = [item.strip() for item in _settings.cors_origins.split(",") if item.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/webhooks/github")
async def github_webhook(request: Request, session: Session = Depends(get_db)) -> dict:
    body = await request.body()
    settings = get_settings()
    signature = request.headers.get("x-hub-signature-256")
    if not verify_signature(settings.github_webhook_secret, body, signature):
        record_event(
            session,
            stage="webhook_rejected",
            status="failed",
            message="Rejected webhook with an invalid signature",
            delivery_id=request.headers.get("x-github-delivery") or None,
            detail={"reason": "invalid_signature"},
        )
        save_events(session)
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    result = handle_github_event(
        session,
        request.headers.get("x-github-event", ""),
        payload,
        request.headers.get("x-github-delivery", ""),
    )
    session.commit()
    return result


@app.get("/api/runs")
def list_runs(session: Session = Depends(get_db)) -> list[dict]:
    runs = session.scalars(
        select(AnalysisRun)
        .options(
            selectinload(AnalysisRun.revision)
            .selectinload(Revision.pull_request)
            .selectinload(PullRequest.repository)
        )
        .order_by(AnalysisRun.created_at.desc())
    ).all()
    return [_summary(session, run) for run in runs]


@app.get("/api/runs/{run_id}")
def get_run(run_id: UUID, session: Session = Depends(get_db)) -> dict:
    run = _load_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _detail(session, run)


@app.post("/api/runs/{run_id}/explanations")
def retry_explanation(
    run_id: UUID,
    body: DepthBody,
    session: Session = Depends(get_db),
) -> dict:
    if body.depth not in DEPTHS:
        raise HTTPException(status_code=422, detail="unknown depth")
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.analysis_status != "succeeded":
        raise HTTPException(status_code=409, detail="analysis has not succeeded")
    _ensure_stored_repository(session, run)
    enqueue_job(session, run.id, "explain", body.depth)
    if body.depth == "quick" and run.explanation_status != "succeeded":
        run.explanation_status = "queued"
        run.explanation_error = None
    session.commit()
    return {"status": "queued", "depth": body.depth, "run_id": str(run.id)}


@app.post("/api/runs/{run_id}/retry")
def retry_failed_run(run_id: UUID, session: Session = Depends(get_db)) -> dict:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    _ensure_stored_repository(session, run)
    try:
        requeue_failed_job(session, run)
    except RetryNotAvailable as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    session.commit()
    loaded = _load_run(session, run_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _detail(session, loaded)


@app.post("/api/runs/{run_id}/comment")
def retry_comment(run_id: UUID, session: Session = Depends(get_db)) -> dict:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.analysis_status != "succeeded":
        raise HTTPException(status_code=409, detail="analysis has not succeeded")
    _ensure_stored_repository(session, run)
    enqueue_job(session, run.id, "comment", None)
    session.commit()
    return {"status": "queued", "run_id": str(run.id)}


@app.get("/api/revisions/{revision_id}/delta")
def get_delta(revision_id: UUID, session: Session = Depends(get_db)) -> dict:
    delta = session.scalars(
        select(RevisionDelta).where(RevisionDelta.revision_id == revision_id)
    ).first()
    if delta is None:
        raise HTTPException(status_code=404, detail="delta not found")
    revision = session.get(Revision, revision_id)
    return {
        "revision_id": str(delta.revision_id),
        "head_sha": revision.head_sha if revision else None,
        "previous_revision_id": str(delta.previous_revision_id) if delta.previous_revision_id else None,
        "previous_head_sha": delta.previous_head_sha,
        "added": delta.added,
        "removed": delta.removed,
        "unchanged_count": delta.unchanged_count,
    }


def _ensure_stored_repository(session: Session, run: AnalysisRun) -> None:
    """Retry routes have a run id, not a GitHub pull_request payload.

    A missing installation is created only when this run's repository already
    stores the installation id and owner/name. Nothing is invented from the
    run id alone.
    """
    ensure_stored_repository(session, run)


def _load_run(session: Session, run_id: UUID) -> AnalysisRun | None:
    return session.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.id == run_id)
        .options(
            selectinload(AnalysisRun.revision)
            .selectinload(Revision.pull_request)
            .selectinload(PullRequest.repository),
            selectinload(AnalysisRun.symbols),
            selectinload(AnalysisRun.claims),
            selectinload(AnalysisRun.evidences),
            selectinload(AnalysisRun.relationships_),
            selectinload(AnalysisRun.explanations),
        )
    ).first()


def _visible_statuses(
    session: Session, run: AnalysisRun
) -> tuple[str, str, str, str | None, str | None, str | None]:
    """Report a failed job as a failed phase when the status column was not updated."""
    analysis_status = run.analysis_status
    explanation_status = run.explanation_status
    comment_status = run.comment_status
    analysis_error = run.analysis_error
    explanation_error = run.explanation_error
    comment_error = run.comment_error
    phase = failed_phase(session, run)
    if phase == "analyze" and analysis_status != "failed":
        analysis_status = "failed"
        analysis_error = analysis_error or phase_error(session, run, "analyze")
    elif phase == "explain" and explanation_status != "failed":
        explanation_status = "failed"
        explanation_error = explanation_error or phase_error(session, run, "explain")
    elif phase == "comment" and comment_status != "failed":
        comment_status = "failed"
        comment_error = comment_error or phase_error(session, run, "comment")
    return (
        analysis_status,
        explanation_status,
        comment_status,
        analysis_error,
        explanation_error,
        comment_error,
    )


def _summary(session: Session, run: AnalysisRun) -> dict:
    revision = run.revision
    pull = revision.pull_request
    analysis_status, explanation_status, comment_status, _, _, _ = _visible_statuses(session, run)
    return {
        "id": str(run.id),
        "analysis_status": analysis_status,
        "explanation_status": explanation_status,
        "comment_status": comment_status,
        "language_coverage": run.language_coverage,
        "repository": pull.repository.full_name,
        "pr_number": pull.number,
        "head_sha": revision.head_sha,
        "title": revision.title,
    }


def _detail(session: Session, run: AnalysisRun) -> dict:
    revision = run.revision
    pull = revision.pull_request
    repository = pull.repository
    files: dict[str, bool] = {}
    for symbol in run.symbols:
        files[symbol.file_path] = files.get(symbol.file_path, False) or bool(symbol.changed)
    for claim in run.claims:
        if claim.kind == "file_changed" and claim.subject:
            files[claim.subject] = True
    explanations = {
        row.depth: {
            "depth": row.depth,
            "status": row.status,
            "provider": row.provider,
            "model": row.model,
            "error": row.error,
            "document": row.document,
        }
        for row in run.explanations
    }
    delta = session.scalars(
        select(RevisionDelta).where(RevisionDelta.revision_id == revision.id)
    ).first()
    (
        analysis_status,
        explanation_status,
        comment_status,
        analysis_error,
        explanation_error,
        comment_error,
    ) = _visible_statuses(session, run)
    return {
        "id": str(run.id),
        "analysis_status": analysis_status,
        "explanation_status": explanation_status,
        "comment_status": comment_status,
        "analysis_error": analysis_error,
        "explanation_error": explanation_error,
        "comment_error": comment_error,
        "language_coverage": run.language_coverage,
        "revision": {
            "id": str(revision.id),
            "repository": repository.full_name,
            "pr_number": pull.number,
            "head_sha": revision.head_sha,
            "base_sha": revision.base_sha,
            "title": revision.title,
            "body": revision.body,
        },
        "files": [{"path": path, "changed": changed} for path, changed in sorted(files.items())],
        "symbols": [
            {
                "id": row.public_id,
                "name": row.name,
                "kind": row.kind,
                "file_path": row.file_path,
                "start_line": row.start_line,
                "end_line": row.end_line,
                "exported": row.exported,
                "changed": row.changed,
            }
            for row in run.symbols
            if row.kind == "function"
        ],
        "relationships": [
            {
                "id": row.public_id,
                "type": row.type,
                "source": row.source_name,
                "target": row.target_name,
                "source_file": row.source_file,
                "target_file": row.target_file,
            }
            for row in run.relationships_
        ],
        "claims": [
            {
                "id": row.public_id,
                "epistemic": row.epistemic,
                "kind": row.kind,
                "text": row.text,
                "subject": row.subject,
                "evidence_ids": row.evidence_public_ids or [],
            }
            for row in run.claims
        ],
        "evidence": [
            {
                "id": row.public_id,
                "type": row.type,
                "repo": row.repo,
                "commit_sha": row.commit_sha,
                "file": row.file,
                "start_line": row.start_line,
                "end_line": row.end_line,
                "symbol": row.symbol,
                "description": row.description,
            }
            for row in run.evidences
        ],
        "explanations": _with_depth_documents(session, run, map_stored_explanations(explanations)),
        "delta": None
        if delta is None
        else {
            "previous_head_sha": delta.previous_head_sha,
            "added": delta.added,
            "removed": delta.removed,
            "unchanged_count": delta.unchanged_count,
        },
        "events": _events(session, run),
        "depths": list(DEPTHS),
        "change_flow_diagram": (story := build_change_flow(run.symbols, run.relationships_, run.evidences)),
        "explain_bullets": explain_bullets(run.claims),
        "details": build_details(
            symbols=run.symbols,
            relationships=run.relationships_,
            evidences=run.evidences,
            claims=run.claims,
            sections=story["sections"],
            repo=repository.full_name,
            sha=revision.head_sha,
            document_unknowns=_document_texts(explanations, "unknowns"),
            review_questions=_document_texts(explanations, "review_questions"),
        ),
    }


def _document_texts(explanations: dict, field: str) -> list[str]:
    deep = explanations.get("deep") or {}
    document = deep.get("document") if deep.get("status") == "succeeded" else None
    if not isinstance(document, dict):
        return []
    texts: list[str] = []
    for statement in document.get(field) or []:
        text = statement.get("text") if isinstance(statement, dict) else None
        if text and text not in texts:
            texts.append(text)
    return texts


def map_stored_explanations(stored: dict) -> dict:
    """Return Quick and Deep. A new deep row wins; an older developer row fills Deep until then."""
    visible: dict = {}
    quick = stored.get("quick")
    if quick:
        visible["quick"] = {**quick, "depth": "quick"}
    deep = stored.get("deep")
    developer = stored.get("developer")
    chosen = None
    if deep and deep.get("document") and deep.get("status") == "succeeded":
        chosen = deep
    elif developer and developer.get("document"):
        chosen = developer
    elif deep:
        chosen = deep
    if chosen is not None:
        visible["deep"] = {**chosen, "depth": "deep"}
    return visible


def _with_depth_documents(session: Session, run: AnalysisRun, explanations: dict) -> dict:
    """Fill Quick and Deep from the analysis. Leave a stored developer body when Deep cannot be built."""
    if run.analysis_status != "succeeded":
        return explanations
    try:
        result = load_result(session, run)
    except Exception:
        logger.exception("depth documents were left unchanged")
        return explanations
    revision = run.revision
    snapshot = Snapshot(
        repository=revision.pull_request.repository.full_name,
        base_sha=revision.base_sha,
        head_sha=revision.head_sha,
        files={},
        changes=[],
        pr_number=revision.pull_request.number,
        pr_title=revision.title,
        pr_body=revision.body,
    )
    budget = _settings.explanation_packet_char_budget
    for depth in DEPTHS:
        packet = build_packet(result, snapshot, depth, budget)  # type: ignore[arg-type]
        composed = compose_document(packet)
        checked = validate_response(composed.model_dump_json(), packet)
        if not checked.ok or checked.document is None or not checked.document.statements():
            continue
        payload = checked.document.model_dump(mode="json")
        current = explanations.get(depth)
        if current is None:
            explanations[depth] = {
                "depth": depth,
                "status": "succeeded",
                "provider": None,
                "model": None,
                "error": None,
                "document": payload,
            }
            continue
        if current.get("status") == "succeeded":
            current["document"] = payload
    return explanations


def _events(session: Session, run: AnalysisRun) -> list[dict]:
    rows = session.scalars(
        select(PipelineEvent)
        .where(PipelineEvent.run_id == run.id)
        .order_by(PipelineEvent.created_at, PipelineEvent.ordinal)
    ).all()
    return [
        {
            "stage": row.stage,
            "status": row.status,
            "message": row.message,
            "detail": row.detail,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "head_sha": row.head_sha,
        }
        for row in rows
    ]


if _DIST.is_dir():
    assets = _DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
