"""Deterministic narrator used by tests and the local seed. It is not selected by LLM_PROVIDER."""

from __future__ import annotations

from app.explanation.narrate import compose_document
from app.explanation.schema import ExplanationDocument, ExplanationPacket
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
    return compose_document(packet)
