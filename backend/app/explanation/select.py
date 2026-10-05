from __future__ import annotations

from collections import defaultdict

from app.analyzer.types import AnalysisResult, Snapshot
from app.explanation.behavior_facts import build_behavior_facts
from app.explanation.impact_facts import build_impact_facts
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
    "behavior_changed": 2,
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

# Same analysis, two slices. Kinds outside the set are omitted even when the budget can hold them.
# Older developer and architecture packets use the deep slice.
_INCLUDED = {
    "quick": {
        "symbol_changed",
        "file_changed",
        "missing_test",
        "tests",
        "reaches_changed",
        "changed_symbol_cap",
        "diff_only",
    },
    "deep": {
        "symbol_changed",
        "behavior_changed",
        "file_changed",
        "calls",
        "tests",
        "missing_test",
        "dependency_changed",
        "file_reason",
        "file_absent",
        "behavior_unchanged",
        "reaches_changed",
        "ambiguous_call",
        "fanout_truncated",
        "diff_only",
        "changed_symbol_cap",
        "defines_api",
    },
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
    packet = _materialize(result, snapshot, depth, selected, notes)
    if _slice(depth) == "quick":
        # Behavior facts carry their own size cap and are added after the claim budget is fitted.
        packet.behavior_facts = build_behavior_facts(
            symbols=result.symbols,
            relationships=result.relationships,
            claims=result.claims,
            evidences=result.evidences,
        )
        packet.impact_facts = build_impact_facts(
            claims=result.claims, evidences=result.evidences, behavior_facts=packet.behavior_facts
        )
    return packet


def _slice(depth: str) -> str:
    if depth in {"developer", "architecture"}:
        return "deep"
    return depth


def _rank(kind: str, depth: str) -> int:
    included = _INCLUDED.get(_slice(depth))
    if included is not None and kind not in included:
        return 100
    return _PRIORITY.get(kind, 7)


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
        if not claim.subject:
            continue
        for symbol in result.symbols:
            if symbol.name != claim.subject:
                continue
            if depth == "quick" and not symbol.changed:
                continue
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
    boundary_claims = _boundary_claims(selected) if _slice(depth) == "deep" else []
    if boundary_claims:
        claims.extend(boundary_claims)
        unknowns.extend(
            UnknownRef(id=f"unk_{claim.id}", text=claim.text, claim_id=claim.id)
            for claim in boundary_claims
        )
        packet_notes.append(
            ContextNote(
                code="no_boundary",
                text=" ".join(claim.text for claim in boundary_claims),
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


def _boundary_claims(selected) -> list[ClaimRef]:
    """UNKNOWN claims for architecture facts the analysis did not produce."""
    gaps = []
    if not any(claim.kind == "defines_api" for claim in selected):
        gaps.append(("api", "No exported API facts are in this packet."))
    if not any(claim.kind == "dependency_changed" for claim in selected):
        gaps.append(("dependency", "No dependency facts are in this packet."))
    gaps.append(("database", "No database or schema facts are in this packet."))
    gaps.append(("external", "No external system facts are in this packet."))
    return [
        ClaimRef(
            id=f"cl_unknown_boundary_{code}",
            epistemic="UNKNOWN",
            kind="unknown_boundary",
            text=text,
            subject=None,
            evidence_ids=[],
        )
        for code, text in gaps
    ]


def _keep_relationship(rel, selected, depth: str) -> bool:
    subjects = {claim.subject for claim in selected if claim.subject}
    changed_names = {
        claim.subject
        for claim in selected
        if claim.kind == "symbol_changed" and claim.subject
    }
    changed_files: set[str] = set()
    for claim in selected:
        if claim.kind == "file_changed" and claim.subject:
            changed_files.add(claim.subject)
        if claim.kind == "symbol_changed" and " changed in " in claim.text:
            changed_files.add(claim.text.split(" changed in ", 1)[1].rstrip("."))
    if _slice(depth) == "quick":
        return False
    if _slice(depth) != "deep":
        return False
    if rel.type in {"DEFINES_API", "DEPENDS_ON"}:
        return True
    if rel.type == "TESTS":
        return rel.target_name in subjects or rel.target_name in changed_names
    if rel.type not in {"CALLS", "IMPORTS"}:
        return False
    return (
        rel.source_name in changed_names
        or rel.target_name in changed_names
        or (rel.source_file or "") in changed_files
        or (rel.target_file or "") in changed_files
    )
