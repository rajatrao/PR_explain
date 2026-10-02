"""Deterministic narrator used by tests and the local seed. It is not selected by LLM_PROVIDER."""

from __future__ import annotations

from app.explanation.schema import ExplanationDocument, ExplanationPacket, Statement
from app.llm.provider import ExplainRequest, ExplanationCallError, LLMResult


class ScriptedProvider:
    id = "fake"

    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.calls = 0
        self.requests: list[ExplainRequest] = []

    def explain(self, request: ExplainRequest) -> LLMResult:
        self.calls += 1
        self.requests.append(request)
        if self.error:
            raise ExplanationCallError(self.error)
        document = document_from_packet(request.packet)
        return LLMResult(content=document.model_dump_json(), latency_ms=4, model="fake-model")


def document_from_packet(packet: ExplanationPacket) -> ExplanationDocument:
    def statement(claim) -> Statement:
        return Statement(
            epistemic=claim.epistemic,
            text=claim.text,
            claim_ids=[claim.id],
            evidence_ids=claim.evidence_ids[:1],
        )

    def take(kinds: set[str]) -> list[Statement]:
        return [statement(claim) for claim in packet.claims if claim.kind in kinds]

    flow = take({"calls", "symbol_changed"})
    if not flow and packet.claims:
        flow = [statement(packet.claims[0])]
    return ExplanationDocument(
        summary=packet.revision.title or "Pull request explanation",
        change_flow=flow,
        impacts=take({"reaches_changed", "defines_api"}),
        important_changes=take({"file_changed"}),
        tests=take({"tests", "missing_test"}),
        unchanged=take({"behavior_unchanged", "file_absent"}),
        unknowns=take({"diff_only", "fanout_truncated", "ambiguous_call"}),
        review_questions=[
            Statement(
                epistemic="UNKNOWN",
                text="Which path still has no test?",
                claim_ids=[packet.claims[0].id],
                evidence_ids=packet.claims[0].evidence_ids[:1],
            )
        ]
        if packet.claims
        else [],
    )
