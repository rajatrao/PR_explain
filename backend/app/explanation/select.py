from __future__ import annotations

from collections import defaultdict

from app.analyzer.types import AnalysisResult, Snapshot
from app.explanation.schema import (
    ClaimRef,
    ContextNote,
    EvidenceRef,
    ExplanationDepth,
    ExplanationPacket,
    ImpactRef,
    RelationshipRef,
    RevisionRef,
    SymbolRef,
    TestRef,
    UnknownRef,
)

_PRIORITY = {
    "symbol_changed": 1,
    "file_changed": 1,
    "changed_symbol_cap": 1,
    "diff_only": 1,
    "calls": 2,
    "ambiguous_call": 2,
    "tests": 3,
    "missing_test": 3,
    "fanout_truncated": 3,
    "defines_api": 4,
    "dependency_changed": 4,
    "reaches_changed": 5,
    "behavior_unchanged": 5,
    "file_absent": 5,
    "file_reason": 6,
}


def build_packet(
    result: AnalysisResult,
    snapshot: Snapshot,
    depth: ExplanationDepth,
    budget: int,
) -> ExplanationPacket:
    ranked = sorted(
        result.claims,
        key=lambda claim: (_rank(claim.kind, depth), claim.id),
    )
    candidates = [claim for claim in ranked if _rank(claim.kind, depth) < 100]
    omitted_for_depth = [claim for claim in ranked if _rank(claim.kind, depth) >= 100]
    notes = [ContextNote(code="analyzer", text=note) for note in result.context_notes]
    if omitted_for_depth:
        notes.append(
            ContextNote(
                code="depth_slice",
                text=f"{len(omitted_for_depth)} claims omitted for the {depth} depth.",
            )
        )

    selected = _fit(result, snapshot, depth, candidates, notes, budget)
    return _materialize(result, snapshot, depth, selected, notes)


def _rank(kind: str, depth: str) -> int:
    base = _PRIORITY.get(kind, 7)
    if depth == "quick" and base > 3:
        return 100
    if depth == "architecture" and kind in {
        "defines_api",
        "dependency_changed",
        "reaches_changed",
        "behavior_unchanged",
        "file_absent",
    }:
        return 0
    if depth == "deep" and kind in {"calls", "file_reason", "ambiguous_call"}:
        return min(base, 2)
    return base


def _fit(result, snapshot, depth, candidates, notes: list[ContextNote], budget: int):
    selected = []
    for index, claim in enumerate(candidates):
        trial = _materialize(result, snapshot, depth, selected + [claim], notes)
        if len(trial.model_dump_json()) > budget and selected:
            remaining = candidates[index:]
            notes.append(
                ContextNote(
                    code="budget",
                    text=f"{len(remaining)} claims omitted because the packet budget was reached.",
                )
            )
            break
        selected.append(claim)
    if not selected and candidates:
        selected = [candidates[0]]
        notes.append(
            ContextNote(
                code="budget",
                text="Packet budget could not fit the change set; the packet is partial.",
            )
        )
    return selected


def _materialize(result, snapshot, depth, selected, notes: list[ContextNote]) -> ExplanationPacket:
    selected_ids = {claim.id for claim in selected}
    evidence_by_id = {item.id: item for item in result.evidences}
    used_evidence: set[str] = set()
    claims: list[ClaimRef] = []
    for claim in selected:
        evidence_ids = [item for item in claim.evidence_ids if item in evidence_by_id]
        used_evidence.update(evidence_ids)
        claims.append(
            ClaimRef(
                id=claim.id,
                epistemic=claim.epistemic,  # type: ignore[arg-type]
                kind=claim.kind,
                text=claim.text,
                subject=claim.subject,
                evidence_ids=evidence_ids,
            )
        )

    symbol_ids: set[str] = set()
    relationships: list[RelationshipRef] = []
    for rel in result.relationships:
        if not _keep_relationship(rel, selected, depth):
            continue
        symbol_ids.add(rel.source_id or "")
        symbol_ids.add(rel.target_id or "")
        if rel.evidence_id and rel.evidence_id in evidence_by_id:
            used_evidence.add(rel.evidence_id)
        relationships.append(
            RelationshipRef(
                id=rel.id,
                type=rel.type,
                source=rel.source_name,
                target=rel.target_name,
                source_file=rel.source_file,
                target_file=rel.target_file,
                evidence_id=rel.evidence_id,
            )
        )
    for claim in selected:
        if claim.subject:
            for symbol in result.symbols:
                if symbol.name == claim.subject or symbol.file_path == claim.subject:
                    symbol_ids.add(symbol.id)

    symbols = [
        SymbolRef(
            id=symbol.id,
            name=symbol.name,
            kind=symbol.kind,
            file_path=symbol.file_path,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            exported=symbol.exported,
            changed=symbol.changed,
        )
        for symbol in result.symbols
        if symbol.id in symbol_ids
    ]
    evidence = []
    for evidence_id in sorted(used_evidence):
        item = evidence_by_id[evidence_id]
        snippet = item.snippet if any(
            evidence_id in claim.evidence_ids and len(claim.text) < 40 for claim in selected
        ) else None
        evidence.append(
            EvidenceRef(
                id=item.id,
                type=item.type,
                repo=item.repo,
                commit_sha=item.commit_sha,
                file=item.file,
                start_line=item.start_line,
                end_line=item.end_line,
                symbol=item.symbol,
                description=item.description,
                snippet=snippet,
            )
        )
    areas: dict[str, list[str]] = defaultdict(list)
    for claim in selected:
        subject = claim.subject or ""
        area = subject.split("/")[0] if "/" in subject else "change"
        if claim.kind in {"symbol_changed", "calls", "file_changed", "defines_api", "dependency_changed"}:
            areas[area].append(claim.id)
    impact = [
        ImpactRef(
            id=f"impact_{area}",
            area=area,
            summary=f"{len(claim_ids)} selected claims touch {area}.",
            claim_ids=claim_ids,
        )
        for area, claim_ids in sorted(areas.items())
    ]
    tests: list[TestRef] = []
    for rel in result.relationships:
        if rel.type != "TESTS":
            continue
        claim = next(
            (
                item
                for item in selected
                if item.kind == "tests" and item.subject == rel.target_name
            ),
            None,
        )
        if claim is None and depth == "quick":
            continue
        if claim is None and not any(item.subject == rel.target_name for item in selected):
            continue
        tests.append(
            TestRef(
                id=rel.id,
                file_path=rel.source_file or "",
                symbol=rel.target_name,
                present=True,
                claim_id=claim.id if claim else None,
            )
        )
    for claim in selected:
        if claim.kind == "missing_test" and claim.subject:
            tests.append(
                TestRef(
                    id=f"test_missing_{claim.id}",
                    file_path="",
                    symbol=claim.subject,
                    present=False,
                    claim_id=claim.id,
                )
            )
    unknowns = [
        UnknownRef(id=f"unk_{claim.id}", text=claim.text, claim_id=claim.id)
        for claim in selected
        if claim.epistemic == "UNKNOWN"
    ]
    packet_notes = list(notes)
    if depth == "architecture" and not any(
        claim.kind in {"defines_api", "dependency_changed"} for claim in selected
    ):
        packet_notes.append(
            ContextNote(
                code="no_boundary",
                text="No database, API, or dependency facts are in this packet.",
            )
        )
    if depth == "deep":
        hop_count = sum(1 for rel in relationships if rel.type == "CALLS")
        packet_notes.append(
            ContextNote(
                code="one_hop",
                text=f"Deep depth includes {hop_count} stored one-hop call edges and no second hop.",
            )
        )
    return ExplanationPacket(
        revision=RevisionRef(
            repository=snapshot.repository,
            pr_number=snapshot.pr_number,
            head_sha=snapshot.head_sha,
            base_sha=snapshot.base_sha,
            title=snapshot.pr_title,
            body=snapshot.pr_body,
        ),
        depth=depth,
        claims=claims,
        symbols=symbols,
        relationships=relationships,
        impact=impact,
        tests=tests,
        evidence=evidence,
        unknowns=unknowns,
        context_notes=packet_notes,
    )


def _keep_relationship(rel, selected, depth: str) -> bool:
    subjects = {claim.subject for claim in selected if claim.subject}
    texts = " ".join(claim.text for claim in selected)
    named = rel.source_name in subjects or rel.target_name in subjects
    mentioned = rel.source_name in texts or rel.target_name in texts
    if depth == "deep" and rel.type in {"CALLS", "IMPORTS", "TESTS"} and (named or mentioned):
        return True
    if depth == "architecture" and rel.type in {"DEFINES_API", "DEPENDS_ON", "CHANGED_IN_PR"}:
        return named or mentioned or rel.type == "DEPENDS_ON"
    if rel.type == "CALLS" and (named or mentioned):
        return True
    if rel.type in {"TESTS", "CHANGED_IN_PR", "DEFINES_API", "DEPENDS_ON", "IMPORTS"} and (
        named or mentioned
    ):
        return True
    return False
