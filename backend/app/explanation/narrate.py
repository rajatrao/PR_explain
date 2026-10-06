"""Wording for the two views from one packet.

Selection already happened in the packet. This module only arranges those
claims, relationships, and evidence into the explanation document.
It does not add files, callers, or edges that the packet does not contain.
"""

from __future__ import annotations

from app.analyzer.parse import is_test_path
from app.explanation.explain_view import (
    explain_skip_symbol,
    function_file_by_name,
    hidden_explain_names,
    text_mentions_test_path,
    without_hidden_symbols,
)
from app.explanation.schema import ExplanationDocument, ExplanationPacket, Statement

def compose_document(packet: ExplanationPacket) -> ExplanationDocument:
    return _summary_document(packet)


def explain_bullets(claims, symbols=None) -> list[str]:
    """Short Explain lines from stored claims.

    One line for what changed, one for major areas, and one for each file
    outside the diff that still reaches a changed symbol. Test paths and
    private Python helpers are omitted from this view.
    """
    paths = function_file_by_name(symbols)
    hidden = hidden_explain_names(symbols)

    def symbol_path(name: str | None) -> str | None:
        if not name:
            return None
        return paths.get(name)

    names: list[str] = []
    for claim in claims:
        if _kind(claim) != "symbol_changed":
            continue
        subject = _subject(claim)
        if subject in hidden or explain_skip_symbol(subject, symbol_path(subject)):
            continue
        if text_mentions_test_path(_text(claim)):
            continue
        if subject and subject not in names:
            names.append(subject)
    shown = names[:4]
    if shown:
        changed_sentence = _join(shown) + " changed."
        if len(names) > len(shown):
            changed_sentence += f" {len(names) - len(shown)} more changed symbols are in the packet."
    else:
        changed_sentence = "No changed symbols are in this packet."
    areas = sorted(
        {
            _area(path)
            for claim in claims
            if _kind(claim) == "file_changed"
            for path in [_subject(claim)]
            if path and not is_test_path(path) and not text_mentions_test_path(path)
        }
    )
    area_sentence = f"Major areas: {_join(areas)}." if areas else "No changed files are in this packet."
    bullets = [changed_sentence, area_sentence]
    for claim in claims:
        if _kind(claim) != "reaches_changed" or not _text(claim):
            continue
        text = without_hidden_symbols(_text(claim).strip(), hidden)
        if not text or text_mentions_test_path(text):
            continue
        bullets.append(text)
    return [item for item in bullets if item]


def _summary_document(packet: ExplanationPacket) -> ExplanationDocument:
    changed = _kinds(packet, {"symbol_changed"})
    summary = " ".join(explain_bullets(packet.claims, packet.symbols))
    anchor = _statement_from_claims(changed[:4] or _kinds(packet, {"file_changed"})[:1] or packet.claims[:1], packet)
    return ExplanationDocument(
        summary=summary,
        change_flow=[anchor] if anchor else [],
    )




_FLOW_KINDS = {"symbol_changed", "calls"}
_TEST_KINDS = {"tests", "missing_test"}
_REASON_KINDS = {"file_reason", "reaches_changed", "behavior_unchanged"}
_DEPENDENCY_KINDS = {"dependency_changed", "dependency"}


def compose_full_document(packet: ExplanationPacket) -> ExplanationDocument:
    """A document that states every claim in the packet, with callers and locations.

    Used by the scripted provider (tests, local seed) and as the benchmark's reference answer;
    the app's own explanation uses ``compose_document``.
    """
    flow = [_located_statement(claim, packet) for claim in _kinds(packet, _FLOW_KINDS)]
    flow.extend(_edge_statements(packet, types={"CALLS", "IMPORTS", "TESTS"}, limit_to_changed=True))
    reasons = _kinds(packet, _REASON_KINDS)
    covered = {claim.subject for claim in reasons if claim.subject}
    bare_absent = [
        claim
        for claim in _kinds(packet, {"file_absent"})
        if claim.subject and claim.subject not in covered
    ]
    return ExplanationDocument(
        summary=(
            "Changed functions, callers, file and line evidence, tests, why files changed, "
            "and API, database, dependency, or external facts."
        ),
        change_flow=_unique(flow),
        important_changes=[
            _located_statement(claim, packet) for claim in _kinds(packet, {"defines_api"})
        ],
        impacts=[_located_statement(claim, packet) for claim in _kinds(packet, _DEPENDENCY_KINDS)],
        tests=[_located_statement(claim, packet) for claim in _kinds(packet, _TEST_KINDS)],
        unchanged=[_located_statement(claim, packet) for claim in [*reasons, *bare_absent]],
        unknowns=[
            _statement_from_claim(claim, packet)
            for claim in _kinds(
                packet,
                {"fanout_truncated", "ambiguous_call", "diff_only", "unknown_boundary"},
            )
        ],
    )


def _kinds(packet: ExplanationPacket, kinds: set[str]):
    return [claim for claim in packet.claims if claim.kind in kinds]


def _kind(claim) -> str:
    return getattr(claim, "kind", "") or ""


def _subject(claim) -> str:
    return getattr(claim, "subject", None) or ""


def _text(claim) -> str:
    return getattr(claim, "text", None) or ""


def _located_statement(claim, packet: ExplanationPacket) -> Statement:
    statement = _statement_from_claim(claim, packet)
    evidence = {item.id: item for item in packet.evidence}
    for evidence_id in statement.evidence_ids:
        item = evidence.get(evidence_id)
        if item is None or not item.file or not item.start_line:
            continue
        if item.end_line and item.end_line != item.start_line:
            located = f"{item.file}:{item.start_line}-{item.end_line}"
        else:
            located = f"{item.file}:{item.start_line}"
        if located in statement.text:
            break
        text = statement.text.rstrip()
        if text.endswith("."):
            text = text[:-1]
        statement = statement.model_copy(update={"text": f"{text} ({located})."})
        break
    return statement


def _statement_from_claim(claim, packet: ExplanationPacket) -> Statement:
    known = {item.id for item in packet.evidence}
    return Statement(
        epistemic=claim.epistemic,
        text=claim.text,
        claim_ids=[claim.id],
        evidence_ids=[item for item in claim.evidence_ids if item in known],
    )


def _statement_from_claims(claims, packet: ExplanationPacket) -> Statement | None:
    usable = [claim for claim in claims if claim is not None]
    if not usable:
        return None
    if len(usable) == 1:
        return _statement_from_claim(usable[0], packet)
    known = {item.id for item in packet.evidence}
    evidence_ids: list[str] = []
    for claim in usable:
        for item in claim.evidence_ids:
            if item in known and item not in evidence_ids:
                evidence_ids.append(item)
    epistemic = usable[0].epistemic
    if any(claim.epistemic != epistemic for claim in usable):
        epistemic = "FACT" if any(claim.epistemic == "FACT" for claim in usable) else epistemic
    return Statement(
        epistemic=epistemic,
        text=" ".join(claim.text for claim in usable),
        claim_ids=[claim.id for claim in usable],
        evidence_ids=evidence_ids,
    )


def _edge_statements(packet: ExplanationPacket, *, types: set[str], limit_to_changed: bool) -> list[Statement]:
    evidence = {item.id: item for item in packet.evidence}
    changed_names = {claim.subject for claim in packet.claims if claim.kind == "symbol_changed" and claim.subject}
    changed_files = {symbol.file_path for symbol in packet.symbols if symbol.changed}
    for claim in packet.claims:
        if claim.kind == "symbol_changed" and " changed in " in claim.text:
            changed_files.add(claim.text.split(" changed in ", 1)[1].rstrip("."))
    claimed = {
        ("CALLS", _before(claim.text, " calls "), _after(claim.text, " calls "))
        for claim in packet.claims
        if claim.kind == "calls" and " calls " in claim.text
    }
    fact = next((claim for claim in packet.claims if claim.epistemic == "FACT"), None)
    statements: list[Statement] = []
    for rel in packet.relationships:
        if rel.type not in types:
            continue
        if (rel.type, rel.source, rel.target.rstrip(".")) in claimed:
            continue
        if limit_to_changed and not _touches_change(rel, changed_names, changed_files):
            continue
        item = evidence.get(rel.evidence_id or "")
        if item is None or fact is None:
            continue
        text = _with_location(item)
        if not text:
            continue
        statements.append(
            Statement(
                epistemic="FACT",
                text=text,
                claim_ids=[fact.id],
                evidence_ids=[item.id],
            )
        )
    return statements


def _with_location(item) -> str:
    text = (item.description or "").strip()
    if not text:
        return ""
    if item.file and item.start_line:
        if item.end_line and item.end_line != item.start_line:
            located = f"{item.file}:{item.start_line}-{item.end_line}"
        else:
            located = f"{item.file}:{item.start_line}"
        if located not in text and item.file in text:
            text = text.replace(item.file, located, 1)
        elif located not in text:
            text = text.rstrip(".")
            text = f"{text} {located}"
    if not text.endswith("."):
        text += "."
    return text


def _touches_change(rel, changed_names: set[str], changed_files: set[str]) -> bool:
    if rel.type == "IMPORTS":
        return rel.target in changed_names
    return (
        rel.source in changed_names
        or rel.target in changed_names
        or (rel.source_file or "") in changed_files
        or (rel.target_file or "") in changed_files
    )


def _unique(statements: list[Statement]) -> list[Statement]:
    seen: set[str] = set()
    kept: list[Statement] = []
    for statement in statements:
        if statement.text in seen:
            continue
        seen.add(statement.text)
        kept.append(statement)
    return kept


def _area(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return path or "change"


def _join(names: list[str]) -> str:
    unique: list[str] = []
    for name in names:
        if name and name not in unique:
            unique.append(name)
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    if len(unique) == 2:
        return f"{unique[0]} and {unique[1]}"
    return ", ".join(unique[:-1]) + f", and {unique[-1]}"


def _before(text: str, marker: str) -> str:
    return text.split(marker, 1)[0].strip()


def _after(text: str, marker: str) -> str:
    return text.split(marker, 1)[1].strip().rstrip(".")
