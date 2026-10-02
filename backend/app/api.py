from __future__ import annotations

import json
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

from app.config import get_settings
from app.db.models import AnalysisRun, PullRequest, Revision, RevisionDelta
from app.db.session import get_db
from app.github.events import handle_github_event
from app.github.webhook import verify_signature
from app.jobs.queue import enqueue_job

DEPTHS = ("quick", "developer", "deep", "architecture")
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class DepthBody(BaseModel):
    depth: str = Field(default="developer")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    if settings.run_migrations:
        from app.db.migrate import upgrade

        upgrade()
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
    return [_summary(run) for run in runs]


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
    enqueue_job(session, run.id, "explain", body.depth)
    if body.depth == "developer" and run.explanation_status != "succeeded":
        run.explanation_status = "queued"
        run.explanation_error = None
    session.commit()
    return {"status": "queued", "depth": body.depth, "run_id": str(run.id)}


@app.post("/api/runs/{run_id}/comment")
def retry_comment(run_id: UUID, session: Session = Depends(get_db)) -> dict:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.analysis_status != "succeeded":
        raise HTTPException(status_code=409, detail="analysis has not succeeded")
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


def _summary(run: AnalysisRun) -> dict:
    revision = run.revision
    pull = revision.pull_request
    return {
        "id": str(run.id),
        "analysis_status": run.analysis_status,
        "explanation_status": run.explanation_status,
        "comment_status": run.comment_status,
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
    return {
        "id": str(run.id),
        "analysis_status": run.analysis_status,
        "explanation_status": run.explanation_status,
        "comment_status": run.comment_status,
        "analysis_error": run.analysis_error,
        "explanation_error": run.explanation_error,
        "comment_error": run.comment_error,
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
        "explanations": explanations,
        "delta": None
        if delta is None
        else {
            "previous_head_sha": delta.previous_head_sha,
            "added": delta.added,
            "removed": delta.removed,
            "unchanged_count": delta.unchanged_count,
        },
        "depths": list(DEPTHS),
    }


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
