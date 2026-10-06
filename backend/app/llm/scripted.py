"""Deterministic narrator used by tests and the local seed. It is not selected by LLM_PROVIDER."""

from __future__ import annotations

from app.explanation.narrate import compose_full_document
from app.explanation.schema import ExplanationDocument, ExplanationPacket
from app.llm.provider import ExplainRequest, ExplanationCallError, LLMResult


class ScriptedProvider:
    """Deterministic provider for tests and the local seed: answers with a document built from the packet, or raises a set error."""

    id = "fake"

    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.calls = 0
        self.requests: list[ExplainRequest] = []

    def explain(self, request: ExplainRequest) -> LLMResult:
        """Record the request and return a document built from its packet, or raise the configured error."""
        self.calls += 1
        self.requests.append(request)
        if self.error:
            raise ExplanationCallError(self.error)
        document = document_from_packet(request.packet)
        return LLMResult(content=document.model_dump_json(), latency_ms=4, model="fake-model")


def document_from_packet(packet: ExplanationPacket) -> ExplanationDocument:
    return compose_full_document(packet)
