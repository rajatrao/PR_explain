import json

import httpx
import pytest

from app.config import Settings
from app.explanation.schema import ExplanationDocument, ExplanationPacket, RevisionRef
from app.llm.ollama import OllamaProvider
from app.llm.provider import ExplainRequest, ExplanationCallError, LLMConfigError, create_llm_provider


def _request() -> ExplainRequest:
    packet = ExplanationPacket(
        revision=RevisionRef(repository="acme/app", pr_number=1, head_sha="b" * 40, base_sha="a" * 40),
        depth="developer",
    )
    return ExplainRequest(
        packet=packet,
        depth="developer",
        system_prompt="system",
        user_prompt="user",
        json_schema=ExplanationDocument.model_json_schema(),
    )


def test_ollama_payload_omits_thinking_flag_and_uses_schema():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "example-model"}]})
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"message": {"content": "{}"}, "model": "example-model"})

    provider = OllamaProvider(
        base_url="http://ollama.internal",
        model="example-model",
        timeout_ms=5000,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.explain(_request())
    assert seen["body"]["stream"] is False
    assert seen["body"]["options"]["temperature"] == 0
    assert seen["body"]["model"] == "example-model"
    assert "format" in seen["body"]
    assert "think" not in seen["body"]
    assert "think" not in seen["body"]["options"]
    assert result.model == "example-model"
    assert result.content == "{}"


def test_missing_configuration_does_not_invent_a_hosted_client():
    settings = Settings(llm_provider="openai", ollama_base_url="http://ollama.internal", ollama_model="example-model")
    with pytest.raises(LLMConfigError, match="no hosted fallback"):
        create_llm_provider(settings)
    missing = Settings(llm_provider="ollama", ollama_base_url=None, ollama_model=None)
    with pytest.raises(LLMConfigError, match="Ollama is not configured"):
        create_llm_provider(missing)


def test_connection_refusal_is_an_explanation_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = OllamaProvider(
        base_url="http://ollama.internal",
        model="example-model",
        timeout_ms=1000,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ExplanationCallError, match="unreachable"):
        provider.explain(_request())
