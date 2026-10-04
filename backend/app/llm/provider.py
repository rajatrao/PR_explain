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
    if provider == "ollama":
        base_url = _configured(settings.ollama_base_url)
        model = _configured(settings.ollama_model)
        if not base_url or not model:
            raise LLMConfigError("Ollama is not configured")
        from app.llm.ollama import OllamaProvider

        return OllamaProvider(
            base_url=base_url,
            model=model,
            timeout_ms=settings.ollama_timeout_ms,
        )
    if provider == "openai":
        return _openai_provider(settings)
    raise LLMConfigError(
        f"Unsupported LLM_PROVIDER '{settings.llm_provider}'. "
        "Expected ollama or openai. There is no fallback."
    )


def _configured(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _openai_provider(settings: Settings) -> LLMProvider:
    base_url = _configured(settings.llm_base_url)
    api_key = _configured(settings.llm_api_key)
    model = _configured(settings.llm_model)
    missing = [
        name
        for name, value in (
            ("LLM_BASE_URL", base_url),
            ("LLM_API_KEY", api_key),
            ("LLM_MODEL", model),
        )
        if not value
    ]
    if missing or not base_url or not api_key or not model:
        joined = ", ".join(missing) or "LLM_BASE_URL, LLM_API_KEY, LLM_MODEL"
        raise LLMConfigError(
            f"LLM_PROVIDER=openai is missing {joined}. "
            "The explanation step was not sent to Ollama."
        )
    from app.llm.openai_compat import OpenAICompatibleProvider

    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_ms=settings.llm_timeout_ms,
    )
