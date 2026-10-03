from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.explanation.schema import ExplanationDocument, ExplanationPacket, Statement

_DEFECT = re.compile(r"\b(is insecure|will break|this pr is bad|is broken)\b", re.IGNORECASE)
_CAMEL = re.compile(r"\b[A-Za-z][a-z0-9]*[A-Z][A-Za-z0-9]*\b")
_NOT_SYMBOLS = {"TypeScript", "JavaScript", "GitHub", "OAuth", "PullRequest"}
_STATEMENT_FIELDS = (
    "change_flow",
    "impacts",
    "important_changes",
    "tests",
    "unchanged",
    "unknowns",
    "review_questions",
)


@dataclass
class ValidationResult:
    ok: bool
    document: ExplanationDocument | None
    errors: list[str] = field(default_factory=list)
    dropped: int = 0
    raw_text: str = ""


def validate_response(raw: str, packet: ExplanationPacket) -> ValidationResult:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ValidationResult(ok=False, document=None, errors=["response is not JSON"], raw_text=raw)
    if not isinstance(payload, dict):
        return ValidationResult(ok=False, document=None, errors=["response JSON must be an object"], raw_text=raw)
    try:
        document = ExplanationDocument.model_validate(payload)
    except ValidationError as exc:
        return ValidationResult(
            ok=False,
            document=None,
            errors=[f"schema validation failed: {exc.error_count()} errors"],
            raw_text=raw,
        )
    errors: list[str] = []
    dropped = 0
    if _DEFECT.search(document.summary):
        document.summary = ""
        errors.append("summary asserted a defect and was cleared")
    invented = _invented_names(document.summary, packet)
    if invented:
        document.summary = ""
        errors.append("summary named symbols outside the packet and was cleared: " + ", ".join(invented))

    claims = {claim.id: claim for claim in packet.claims}
    evidence = {item.id: item for item in packet.evidence}
    allowed_cache: dict[tuple[str, ...], str] = {}

    for field_name in _STATEMENT_FIELDS:
        kept: list[Statement] = []
        for statement in getattr(document, field_name):
            outcome, reason = _screen_statement(
                statement,
                field_name,
                claims,
                evidence,
                packet,
                allowed_cache,
            )
            if outcome is None:
                dropped += 1
                errors.append(reason or f"dropped a {field_name} statement")
                continue
            kept.append(outcome)
        setattr(document, field_name, kept)

    if not document.statements():
        errors.append("every statement was dropped")
        return ValidationResult(ok=False, document=document, errors=errors, dropped=dropped, raw_text=raw)
    return ValidationResult(ok=True, document=document, errors=errors, dropped=dropped, raw_text=raw)


def _screen_statement(statement, field_name, claims, evidence, packet, allowed_cache):
    valid_claims = [claim_id for claim_id in statement.claim_ids if claim_id in claims]
    invented_claims = [claim_id for claim_id in statement.claim_ids if claim_id not in claims]
    valid_evidence = [evidence_id for evidence_id in statement.evidence_ids if evidence_id in evidence]
    invented_evidence = [evidence_id for evidence_id in statement.evidence_ids if evidence_id not in evidence]
    if invented_claims or invented_evidence:
        statement = statement.model_copy(
            update={"claim_ids": valid_claims, "evidence_ids": valid_evidence}
        )
    if not statement.claim_ids:
        return None, "statement dropped because it had no valid claim ids"
    if field_name == "review_questions" and (
        _DEFECT.search(statement.text) or "?" not in statement.text
    ):
        return None, "review question dropped because it asserted a defect or was not a question"
    if field_name != "review_questions" and _DEFECT.search(statement.text):
        return None, "statement dropped because it asserted a defect"
    cited = [claims[claim_id] for claim_id in statement.claim_ids]
    if statement.epistemic == "FACT":
        fact_cited = any(claim.epistemic == "FACT" for claim in cited)
        inference_cited = any(claim.epistemic == "INFERENCE" for claim in cited)
        if not fact_cited and inference_cited:
            statement = statement.model_copy(update={"epistemic": "INFERENCE"})
        elif not fact_cited:
            statement = statement.model_copy(update={"epistemic": "UNKNOWN"})
    invented_symbols = _invented_in_statement(statement, packet, allowed_cache)
    if invented_symbols:
        return None, "statement dropped because it named symbols outside the cited claims"
    return statement, None


def _invented_in_statement(statement: Statement, packet: ExplanationPacket, cache: dict) -> list[str]:
    key = (tuple(statement.claim_ids), tuple(statement.evidence_ids))
    if key not in cache:
        chunks = []
        claim_ids = set(statement.claim_ids)
        evidence_ids = set()
        for claim in packet.claims:
            if claim.id in claim_ids:
                chunks.append(claim.text)
                chunks.append(claim.subject or "")
                evidence_ids.update(claim.evidence_ids)
        for item in packet.evidence:
            if item.id in evidence_ids or item.id in statement.evidence_ids:
                chunks.append(item.description)
                chunks.append(item.symbol or "")
        cache[key] = "\n".join(chunks)
    allowed = cache[key]
    invented = []
    for token in _CAMEL.findall(statement.text):
        if token in _NOT_SYMBOLS or token in allowed or not _is_symbol_identifier(token):
            continue
        invented.append(token)
    packet_names = {symbol.name for symbol in packet.symbols}
    for name in packet_names:
        if name and re.search(rf"\b{re.escape(name)}\b", statement.text) and name not in allowed:
            invented.append(name)
    return sorted(set(invented))


def _invented_names(text: str, packet: ExplanationPacket) -> list[str]:
    blob = "\n".join(
        [claim.text for claim in packet.claims]
        + [symbol.name for symbol in packet.symbols]
        + [item.description for item in packet.evidence]
    )
    return sorted(
        {
            token
            for token in _CAMEL.findall(text)
            if token not in blob and token not in _NOT_SYMBOLS and _is_symbol_identifier(token)
        }
    )


def _is_symbol_identifier(token: str) -> bool:
    """A camelCase or PascalCase name, not an area label such as API or UI.

    The camel scanner also matches short acronyms. Those are labels unless a
    lowercase letter is followed by an uppercase letter, as in createSession.
    """
    seen_lower = False
    for char in token:
        if char.islower():
            seen_lower = True
        elif char.isupper() and seen_lower:
            return True
    return False
