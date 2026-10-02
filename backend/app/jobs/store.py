from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analyzer.types import AnalysisResult, Claim, Evidence, Relationship, Symbol
from app.db.models import (
    AnalysisRun,
    ClaimEvidence,
    ClaimRow,
    ClaimSupport,
    EvidenceRow,
    ExplanationPacketRow,
    RelationshipRow,
    Revision,
    RevisionDelta,
    SymbolRow,
)
from app.explanation.schema import ExplanationPacket
from app.revision.delta import compare_claim_sets


def persist_result(session: Session, run: AnalysisRun, result: AnalysisResult) -> None:
    for row in list(run.claims):
        session.delete(row)
    for row in list(run.evidences):
        session.delete(row)
    for row in list(run.relationships_):
        session.delete(row)
    for row in list(run.symbols):
        session.delete(row)
    session.flush()

    evidence_ids: dict[str, EvidenceRow] = {}
    for item in result.evidences:
        row = EvidenceRow(
            run_id=run.id,
            public_id=item.id,
            type=item.type,
            repo=item.repo,
            commit_sha=item.commit_sha,
            file=item.file,
            start_line=item.start_line,
            end_line=item.end_line,
            symbol=item.symbol,
            description=item.description,
            snippet=item.snippet,
        )
        session.add(row)
        evidence_ids[item.id] = row
    claim_rows: dict[str, ClaimRow] = {}
    for item in result.claims:
        row = ClaimRow(
            run_id=run.id,
            public_id=item.id,
            epistemic=item.epistemic,
            kind=item.kind,
            text=item.text,
            subject=item.subject,
            evidence_public_ids=list(item.evidence_ids),
            support_public_ids=list(item.support_ids),
        )
        session.add(row)
        claim_rows[item.id] = row
    for item in result.symbols:
        session.add(
            SymbolRow(
                run_id=run.id,
                public_id=item.id,
                name=item.name,
                kind=item.kind,
                file_path=item.file_path,
                start_line=item.start_line,
                end_line=item.end_line,
                exported=item.exported,
                changed=item.changed,
            )
        )
    for item in result.relationships:
        session.add(
            RelationshipRow(
                run_id=run.id,
                public_id=item.id,
                type=item.type,
                source_public_id=item.source_id,
                target_public_id=item.target_id,
                source_name=item.source_name,
                target_name=item.target_name,
                source_file=item.source_file,
                target_file=item.target_file,
                evidence_public_id=item.evidence_id,
            )
        )
    session.flush()
    for item in result.claims:
        claim_row = claim_rows[item.id]
        for evidence_id in item.evidence_ids:
            evidence_row = evidence_ids.get(evidence_id)
            if evidence_row is None:
                continue
            session.add(ClaimEvidence(claim_id=claim_row.id, evidence_id=evidence_row.id))
        for support_id in item.support_ids:
            support = claim_rows.get(support_id)
            if support is None:
                continue
            session.add(ClaimSupport(claim_id=claim_row.id, support_claim_id=support.id))
    run.language_coverage = result.language_coverage
    run.context_notes = list(result.context_notes)


def load_result(session: Session, run: AnalysisRun) -> AnalysisResult:
    symbols = [
        Symbol(
            id=row.public_id,
            name=row.name,
            kind=row.kind,
            file_path=row.file_path,
            start_line=row.start_line,
            end_line=row.end_line,
            exported=row.exported,
            changed=row.changed,
        )
        for row in session.scalars(select(SymbolRow).where(SymbolRow.run_id == run.id))
    ]
    relationships = [
        Relationship(
            id=row.public_id,
            type=row.type,
            source_id=row.source_public_id,
            target_id=row.target_public_id,
            source_name=row.source_name,
            target_name=row.target_name,
            source_file=row.source_file,
            target_file=row.target_file,
            evidence_id=row.evidence_public_id,
        )
        for row in session.scalars(select(RelationshipRow).where(RelationshipRow.run_id == run.id))
    ]
    evidences = [
        Evidence(
            id=row.public_id,
            type=row.type,
            repo=row.repo,
            commit_sha=row.commit_sha,
            file=row.file,
            start_line=row.start_line,
            end_line=row.end_line,
            symbol=row.symbol,
            description=row.description,
            snippet=row.snippet,
        )
        for row in session.scalars(select(EvidenceRow).where(EvidenceRow.run_id == run.id))
    ]
    claims = claims_from_run(session, run)
    return AnalysisResult(
        language_coverage=run.language_coverage,
        symbols=symbols,
        relationships=relationships,
        evidences=evidences,
        claims=claims,
        context_notes=list(run.context_notes or []),
    )


def claims_from_run(session: Session, run: AnalysisRun) -> list[Claim]:
    rows = session.scalars(select(ClaimRow).where(ClaimRow.run_id == run.id)).all()
    return [
        Claim(
            id=row.public_id,
            epistemic=row.epistemic,
            kind=row.kind,
            text=row.text,
            subject=row.subject,
            evidence_ids=list(row.evidence_public_ids or []),
            support_ids=list(row.support_public_ids or []),
        )
        for row in rows
    ]


def save_packet(session: Session, run: AnalysisRun, packet: ExplanationPacket) -> ExplanationPacketRow:
    row = session.scalars(
        select(ExplanationPacketRow).where(
            ExplanationPacketRow.run_id == run.id,
            ExplanationPacketRow.depth == packet.depth,
        )
    ).first()
    payload = packet.model_dump(mode="json")
    notes = [note.model_dump() for note in packet.context_notes]
    if row is None:
        row = ExplanationPacketRow(
            run_id=run.id,
            depth=packet.depth,
            payload=payload,
            selection_notes=notes,
        )
        session.add(row)
    else:
        row.payload = payload
        row.selection_notes = notes
    return row


def record_delta(session: Session, revision: Revision, current_claims: list[Claim]) -> None:
    previous = session.scalars(
        select(Revision)
        .where(Revision.pull_request_id == revision.pull_request_id)
        .where(Revision.id != revision.id)
        .order_by(Revision.created_at.desc())
    ).first()
    if previous is None or previous.run is None:
        return
    older = claims_from_run(session, previous.run)
    compared = compare_claim_sets(older, current_claims)
    existing = session.scalars(
        select(RevisionDelta).where(RevisionDelta.revision_id == revision.id)
    ).first()
    if existing is None:
        existing = RevisionDelta(revision_id=revision.id)
        session.add(existing)
    existing.previous_revision_id = previous.id
    existing.previous_head_sha = previous.head_sha
    existing.added = compared["added"]
    existing.removed = compared["removed"]
    existing.unchanged_count = compared["unchanged_count"]
