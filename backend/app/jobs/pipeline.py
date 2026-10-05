from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analyzer.analyze import analyze
from app.analyzer.types import Snapshot
from app.config import Settings
from app.db.models import AnalysisRun, ExplanationRow, PullRequest
from app.explanation.assemble import PROMPT_VERSION, build_user_message, system_prompt
from app.explanation.behavior_facts import build_behavior_facts
from app.explanation.behavioral_changes import screen_narrative
from app.explanation.impact import build_impact_facts, screen_impact
from app.explanation.review_diagram import build_review_diagram
from app.explanation.narrate import compose_document, explain_bullets
from app.explanation.schema import EvidenceRef, ExplanationDocument, ExplanationPacket
from app.explanation.select import build_packet
from app.explanation.validate import ValidationResult, validate_response
from app.analyzer.diagram import build_change_flow
from app.github.comment import publish_combined_comment, render_combined_comment
from app.github.patches import fetch_compare_patches
from app.jobs.events import record_event, restore_pipeline_events
from app.jobs.queue import enqueue_job
from app.jobs.store import load_result, persist_result, record_delta, save_packet
from app.llm.provider import ExplainRequest, ExplanationCallError, LLMConfigError, LLMProvider


def execute_analyze(session: Session, run_id: uuid.UUID, snapshot: Snapshot, settings: Settings) -> None:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise LookupError(f"run {run_id} was not found")
    revision = run.revision
    snapshot.repository = revision.pull_request.repository.full_name
    snapshot.base_sha = revision.base_sha
    snapshot.head_sha = revision.head_sha
    snapshot.pr_number = revision.pull_request.number
    snapshot.pr_title = revision.title
    snapshot.pr_body = revision.body
    head_sha = revision.head_sha
    run.analysis_status = "running"
    session.commit()
    try:
        result = analyze(
            snapshot,
            fanout_cap=settings.fanout_cap,
            max_changed_symbols=settings.max_changed_symbols,
            on_stage=lambda stage, status, message, detail=None: record_event(
                session,
                stage=stage,
                status=status,
                message=message,
                run_id=run.id,
                head_sha=head_sha,
                detail=detail,
            ),
        )
        persist_result(session, run, result)
        record_event(
            session,
            stage="claims_persisted",
            status="succeeded",
            message="Persisted claims",
            run_id=run.id,
            head_sha=head_sha,
            detail={"claim_count": len(result.claims)},
        )
        packet = build_packet(
            result,
            snapshot,
            "quick",
            settings.explanation_packet_char_budget,
        )
        save_packet(session, run, packet)
        record_event(
            session,
            stage="explanation_packet_persisted",
            status="succeeded",
            message="Persisted the explanation packet",
            run_id=run.id,
            head_sha=head_sha,
            detail={"depth": packet.depth, "claim_count": len(packet.claims)},
        )
        record_delta(session, revision, result.claims)
        run.analysis_status = "succeeded"
        run.analysis_error = None
        run.explanation_status = "queued"
        run.comment_status = "pending"
        enqueue_job(session, run.id, "explain", "quick")
        session.commit()
    except Exception as exc:
        session.rollback()
        restore_pipeline_events(session)
        failed = session.get(AnalysisRun, run_id)
        if failed is not None:
            failed.analysis_status = "failed"
            failed.analysis_error = str(exc)[:2000]
            session.commit()
        raise


def execute_explain(
    session: Session,
    run_id: uuid.UUID,
    depth: str,
    provider: LLMProvider,
    settings: Settings,
    comment_client=None,
) -> None:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise LookupError(f"run {run_id} was not found")
    head_sha = run.revision.head_sha
    if run.analysis_status != "succeeded":
        record_event(
            session,
            stage="explanation",
            status="failed",
            message="Explanation failed",
            run_id=run.id,
            head_sha=head_sha,
            detail={"depth": depth, "reason": "analysis_not_ready"},
        )
        raise RuntimeError("explanation requires a succeeded analysis")
    if depth == "quick":
        run.explanation_status = "running"
        run.explanation_error = None
    record_event(
        session,
        stage="explanation",
        status="started",
        message="Explanation started",
        run_id=run.id,
        head_sha=head_sha,
        detail={"depth": depth},
    )
    if depth == "quick":
        session.commit()
    packet = _packet_for_depth(session, run, depth, settings)
    try:
        validation, result = _generate(provider, packet, depth)
    except (ExplanationCallError, LLMConfigError, OSError, ConnectionError) as exc:
        _fail_explanation(session, run, depth, provider, str(exc), None, head_sha, type(exc).__name__)
        return
    except Exception as exc:
        _fail_explanation(session, run, depth, provider, str(exc), None, head_sha, type(exc).__name__)
        return
    if not validation.ok or validation.document is None:
        message = "; ".join(validation.errors) or "explanation failed validation"
        _fail_explanation(session, run, depth, provider, message, validation.raw_text, head_sha, "validation")
        return
    document = _grounded_document(session, run, depth, settings) or validation.document
    if depth == "quick":
        # Read the narrative from the model's raw reply. The validated document may be the packet-built
        # fallback (the quick prompt leaves statement arrays empty), which never carries a narrative.
        # Keep it only after it is checked against the packet's behavior facts.
        narrative = _raw_field(result.content, "behavioral_changes")
        screening: list[str] = []
        document.behavioral_changes = screen_narrative(narrative, packet.behavior_facts, screening)
        document.behavior_screening = [] if document.behavioral_changes else screening[:8]
        kept = len(document.behavioral_changes.changes) if document.behavioral_changes else 0
        impact_screening: list[str] = []
        document.impact = screen_impact(
            _raw_field(result.content, "impact"), packet.behavior_facts, packet.impact_facts, impact_screening
        )
        document.impact_screening = [] if document.impact else impact_screening[:8]
        impact_kept = len(document.impact.areas) if document.impact else 0
    _store_explanation(
        session,
        run,
        depth,
        status="succeeded",
        provider_id=provider.id,
        model=result.model,
        document=document.model_dump(mode="json"),
        raw_response=result.content,
        error=None,
    )
    if depth == "quick":
        _store_deep_document(session, run, provider, settings)
        run.explanation_status = "succeeded"
        run.explanation_error = None
    record_event(
        session,
        stage="explanation",
        status="succeeded",
        message="Explanation succeeded",
        run_id=run.id,
        head_sha=run.revision.head_sha,
        detail=(
            {
                "depth": depth,
                "behavior_changes_kept": kept,
                "behavior_screening": screening[:8],
                "impact_areas_kept": impact_kept,
                "impact_screening": impact_screening[:8],
            }
            if depth == "quick"
            else {"depth": depth}
        ),
    )
    session.commit()
    if depth == "quick":
        _sync_comment(
            session,
            run,
            settings,
            comment_client,
            document=document,
            failure=None,
        )


def execute_comment(session: Session, run_id: uuid.UUID, settings: Settings, comment_client) -> None:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise LookupError(f"run {run_id} was not found")
    if run.explanation_status != "succeeded":
        _skip_comment(session, run)
        return
    row = _explanation_row(session, run.id, "quick")
    document = None
    if row is not None and row.status == "succeeded" and row.document:
        document = ExplanationDocument.model_validate(row.document)
    else:
        document = _grounded_document(session, run, "quick", settings)
    if document is None:
        _skip_comment(session, run)
        return
    _sync_comment(session, run, settings, comment_client, document=document, failure=None)


def _store_deep_document(session: Session, run: AnalysisRun, provider: LLMProvider, settings: Settings) -> None:
    """Store the detailed view beside Quick. Does not call the model again."""
    document = _grounded_document(session, run, "deep", settings)
    if document is None:
        return
    _store_explanation(
        session,
        run,
        "deep",
        status="succeeded",
        provider_id=provider.id,
        model=None,
        document=document.model_dump(mode="json"),
        raw_response=None,
        error=None,
    )


def _grounded_document(session: Session, run: AnalysisRun, depth: str, settings: Settings):
    """Depth document written from the analysis. Does not replace the stored packet."""
    result = load_result(session, run)
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
    packet = build_packet(result, snapshot, depth, settings.explanation_packet_char_budget)  # type: ignore[arg-type]
    composed = compose_document(packet)
    checked = validate_response(composed.model_dump_json(), packet)
    if checked.ok and checked.document is not None and checked.document.statements():
        return checked.document
    return None


def _packet_for_depth(session: Session, run: AnalysisRun, depth: str, settings: Settings) -> ExplanationPacket:
    from app.db.models import ExplanationPacketRow

    row = session.scalars(
        select(ExplanationPacketRow).where(
            ExplanationPacketRow.run_id == run.id,
            ExplanationPacketRow.depth == depth,
        )
    ).first()
    if row is not None:
        packet = ExplanationPacket.model_validate(row.payload)
        if depth == "quick" and not packet.behavior_facts:
            # Packets stored before behavior facts existed: add them from the stored analysis.
            stored = load_result(session, run)
            packet.behavior_facts = build_behavior_facts(
                symbols=stored.symbols,
                relationships=stored.relationships,
                claims=stored.claims,
                evidences=stored.evidences,
            )
        if depth == "quick" and not packet.impact_facts:
            stored = load_result(session, run)
            packet.impact_facts = build_impact_facts(
                claims=stored.claims, evidences=stored.evidences, behavior_facts=packet.behavior_facts
            )
        return packet
    result = load_result(session, run)
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
    packet = build_packet(result, snapshot, depth, settings.explanation_packet_char_budget)  # type: ignore[arg-type]
    save_packet(session, run, packet)
    session.commit()
    return packet


def _generate(provider: LLMProvider, packet: ExplanationPacket, depth: str):
    request = _request(packet, depth, None)
    result = provider.explain(request)
    validation = validate_response(result.content, packet)
    if _model_saved(validation):
        return validation, result
    # An empty statement list cannot be repaired into packet facts. Keep the
    # deterministic explanation instead of waiting on another empty document.
    if _statements_missing(validation):
        kept = _packet_explanation(packet, validation, result.content)
        if kept is not None:
            return kept, result
    repair = _request(packet, depth, validation.errors)
    repaired = provider.explain(repair)
    repaired_validation = validate_response(repaired.content, packet)
    if _model_saved(repaired_validation):
        return repaired_validation, repaired
    kept = _packet_explanation(packet, repaired_validation, repaired.content)
    if kept is not None:
        return kept, repaired
    return repaired_validation, repaired


def _raw_field(content: str | None, field: str) -> dict | None:
    """One object field (behavioral_changes, impact) from the model's raw JSON reply, if it sent one."""
    try:
        payload = json.loads(content or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get(field)
    return value if isinstance(value, dict) else None


def _model_saved(validation) -> bool:
    return bool(validation.ok and validation.document is not None and validation.document.statements())


def _statements_missing(validation) -> bool:
    return validation.document is not None and not validation.document.statements()


def _packet_explanation(packet: ExplanationPacket, validation, raw_text: str):
    """Save packet claims when the model document cannot be kept."""
    composed = compose_document(packet)
    if not composed.statements():
        return None
    checked = validate_response(composed.model_dump_json(), packet)
    document = composed
    if checked.ok and checked.document is not None and checked.document.statements():
        document = checked.document
    if not document.summary.strip():
        document.summary = _summary_from_packet(packet, document)
    return ValidationResult(
        ok=True,
        document=document,
        errors=list(validation.errors),
        dropped=validation.dropped,
        raw_text=raw_text,
    )


def _summary_from_packet(packet: ExplanationPacket, document: ExplanationDocument) -> str:
    bullets = [item.strip() for item in explain_bullets(packet.claims, packet.symbols) if item and item.strip()]
    if bullets:
        return " ".join(bullets)
    texts = [statement.text.strip() for statement in document.statements() if statement.text.strip()]
    return " ".join(texts[:4])


def _request(packet: ExplanationPacket, depth: str, errors: list[str] | None) -> ExplainRequest:
    scoped = packet.model_copy(update={"depth": depth})
    return ExplainRequest(
        packet=scoped,
        depth=depth,  # type: ignore[arg-type]
        system_prompt=system_prompt(),
        user_prompt=build_user_message(scoped, repair_errors=errors),
        json_schema=ExplanationDocument.model_json_schema(),
        repair_errors=errors,
    )


def _fail_explanation(session, run, depth, provider, message, raw, head_sha, error_type, model=None) -> None:
    record_event(
        session,
        stage="explanation",
        status="failed",
        message="Explanation failed",
        run_id=run.id,
        head_sha=head_sha,
        detail={"depth": depth, "error_type": error_type},
    )
    if depth == "quick":
        _mark_comment_skipped(session, run, head_sha)
    _mark_explanation_failed(session, run, depth, provider, message, raw, model)


def _mark_comment_skipped(session, run, head_sha) -> None:
    """Leave the pull-request comment untouched when explanation did not succeed."""
    run.comment_status = "skipped"
    record_event(
        session,
        stage="comment",
        status="skipped",
        message="Skipped the pull request comment",
        run_id=run.id,
        head_sha=head_sha,
        detail={"reason": "explanation_failed"},
    )


def _skip_comment(session, run) -> None:
    _mark_comment_skipped(session, run, run.revision.head_sha)
    session.commit()


def _mark_explanation_failed(session, run, depth, provider, message, raw, model=None) -> None:
    _store_explanation(
        session,
        run,
        depth,
        status="failed",
        provider_id=getattr(provider, "id", None),
        model=model,
        document=None,
        raw_response=raw,
        error=message[:2000],
    )
    if depth == "quick":
        run.explanation_status = "failed"
        run.explanation_error = message[:2000]
    session.commit()


def _store_explanation(session, run, depth, *, status, provider_id, model, document, raw_response, error) -> None:
    row = _explanation_row(session, run.id, depth)
    if row is None:
        row = ExplanationRow(run_id=run.id, depth=depth, status=status)
        session.add(row)
    row.status = status
    row.provider = provider_id
    row.model = model
    row.prompt_version = PROMPT_VERSION
    row.document = document
    row.raw_response = raw_response
    row.error = error


def _explanation_row(session, run_id, depth) -> ExplanationRow | None:
    return session.scalars(
        select(ExplanationRow).where(ExplanationRow.run_id == run_id, ExplanationRow.depth == depth)
    ).first()


def _sync_comment(session, run, settings: Settings, comment_client, *, document, failure) -> None:
    revision = run.revision
    head_sha = revision.head_sha
    if comment_client is None:
        record_event(
            session,
            stage="comment",
            status="skipped",
            message="Skipped the pull request comment",
            run_id=run.id,
            head_sha=head_sha,
        )
        return
    record_event(
        session,
        stage="comment",
        status="started",
        message="Comment started",
        run_id=run.id,
        head_sha=head_sha,
    )
    pull = revision.pull_request
    packet_row = next((item for item in run.packets if item.depth == "quick"), None)
    evidence_by_id: dict[str, EvidenceRef] = {}
    if packet_row is not None:
        packet = ExplanationPacket.model_validate(packet_row.payload)
        evidence_by_id = {item.id: item for item in packet.evidence}
    stored = load_result(session, run)
    story_explain = build_change_flow(stored.symbols, stored.relationships, stored.evidences, for_explain=True)
    story_full = build_change_flow(stored.symbols, stored.relationships, stored.evidences)
    bullets = explain_bullets(stored.claims, stored.symbols)
    body = render_combined_comment(
        document=document,
        failure=failure,
        repo_full_name=pull.repository.full_name,
        pr_number=pull.number,
        head_sha=revision.head_sha,
        base_sha=revision.base_sha,
        app_base_url=settings.app_base_url,
        run_id=str(run.id),
        evidence_by_id=evidence_by_id,
        change_flow=story_explain["text"],
        bullets=bullets,
        mermaid=build_review_diagram(
            symbols=stored.symbols, relationships=stored.relationships, claims=stored.claims, evidences=stored.evidences
        )
        or story_explain["mermaid"],
        claims=stored.claims,
        sections=story_full["sections"],
        evidence=stored.evidences,
        symbols=stored.symbols,
        relationships=stored.relationships,
        document_unknowns=None if failure else _deep_texts(session, run, "unknowns"),
        review_questions=None if failure else _deep_texts(session, run, "review_questions"),
        patches=fetch_compare_patches(
            settings,
            pull.repository.full_name,
            revision.base_sha,
            revision.head_sha,
            pull.repository.installation_id,
        ),
    )
    try:
        _publish(comment_client, pull, body)
        run.comment_status = "posted"
        run.comment_error = None
        record_event(
            session,
            stage="comment",
            status="posted",
            message="Posted the pull request comment",
            run_id=run.id,
            head_sha=head_sha,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        restore_pipeline_events(session)
        fresh = session.get(AnalysisRun, run.id)
        if fresh is None:
            return
        fresh.comment_status = "failed"
        fresh.comment_error = _comment_failure_message(exc)
        record_event(
            session,
            stage="comment",
            status="failed",
            message="Comment failed",
            run_id=fresh.id,
            head_sha=head_sha,
            detail={"error_type": type(exc).__name__},
        )
        session.commit()


def _comment_failure_message(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status:
        detail = getattr(response, "text", "") or ""
        lowered = detail.lower()
        if "bearer" in lowered or "token" in lowered:
            detail = ""
        message = f"GitHub comment failed with HTTP {status}."
        if detail.strip():
            message = f"{message} {detail.strip()[:300]}"
        return message[:2000]
    text = str(exc)
    lowered = text.lower()
    if "bearer" in lowered or "ghp_" in text or "ghs_" in text:
        return type(exc).__name__
    return text[:2000]


def _deep_texts(session, run, field: str) -> list[str]:
    row = _explanation_row(session, run.id, "deep")
    if row is None or row.status != "succeeded" or not row.document:
        return []
    texts: list[str] = []
    for statement in row.document.get(field) or []:
        text = statement.get("text") if isinstance(statement, dict) else None
        if text and text not in texts:
            texts.append(text)
    return texts


def _publish(comment_client, pull: PullRequest, body: str) -> None:
    pull.explanation_comment_id = publish_combined_comment(
        comment_client,
        pull.repository.full_name,
        pull.number,
        body,
        fallback_id=pull.explanation_comment_id,
    )
