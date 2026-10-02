from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from app.config import Settings
from app.explanation.schema import ExplanationDepth, ExplanationPacket


class ExplainRequest(BaseModel):
    packet: ExplanationPacket
    depth: ExplanationDepth
    system_prompt: str
    user_prompt: str
    json_schema: dict
    repair_errors: list[str] | None = None


class LLMResult(BaseModel):
    content: str
    latency_ms: int
    model: str


class LLMProvider(Protocol):
    id: str

    def explain(self, request: ExplainRequest) -> LLMResult: ...


class LLMConfigError(RuntimeError):
    """The explanation step is misconfigured. Analysis is left intact."""


class ExplanationCallError(RuntimeError):
    """The provider failed. Analysis rows stay in place."""


def create_llm_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.strip().lower()
    if provider != "ollama":
        raise LLMConfigError(
            f"Unsupported LLM_PROVIDER '{settings.llm_provider}'. "
            "Only the local Ollama provider is implemented; there is no hosted fallback."
        )
    if not settings.ollama_base_url or not settings.ollama_model:
        raise LLMConfigError("Ollama is not configured")
    from app.llm.ollama import OllamaProvider

    return OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_ms=settings.ollama_timeout_ms,
    )
