import json

import httpx
import pytest

from app.config import Settings
from app.explanation.assemble import build_user_message
from app.explanation.schema import ClaimRef, ExplanationDocument, ExplanationPacket, RevisionRef
from app.llm.ollama import OllamaProvider
from app.llm.openai_compat import OpenAICompatibleProvider
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


def test_missing_ollama_configuration_stays_on_ollama():
    missing = Settings(_env_file=None, llm_provider="ollama", ollama_base_url=None, ollama_model=None)
    with pytest.raises(LLMConfigError, match="Ollama is not configured"):
        create_llm_provider(missing)


def test_unknown_provider_does_not_fall_back(monkeypatch):
    _block_ollama(monkeypatch)
    settings = Settings(
        _env_file=None,
        llm_provider="anthropic",
        ollama_base_url="http://ollama.internal",
        ollama_model="example-model",
        llm_base_url="https://api.example.test/v1",
        llm_api_key="test-key",
        llm_model="gpt-test",
    )
    with pytest.raises(LLMConfigError, match="Unsupported LLM_PROVIDER"):
        create_llm_provider(settings)


@pytest.mark.parametrize(
    ("field", "env_name"),
    [
        ("llm_base_url", "LLM_BASE_URL"),
        ("llm_api_key", "LLM_API_KEY"),
        ("llm_model", "LLM_MODEL"),
    ],
)
def test_openai_missing_setting_does_not_call_ollama(monkeypatch, field, env_name):
    _block_ollama(monkeypatch)
    monkeypatch.setattr(httpx, "Client", _refuse_http)
    values = {
        "llm_base_url": "https://api.example.test/v1",
        "llm_api_key": "test-key",
        "llm_model": "gpt-test",
    }
    values[field] = "  "
    settings = Settings(
        _env_file=None,
        llm_provider="openai",
        ollama_base_url="http://ollama.internal",
        ollama_model="example-model",
        **values,
    )
    with pytest.raises(LLMConfigError, match=env_name):
        create_llm_provider(settings)


def test_openai_sends_packet_prompt_and_parses_json():
    packet = ExplanationPacket(
        revision=RevisionRef(repository="acme/app", pr_number=1, head_sha="b" * 40, base_sha="a" * 40),
        depth="developer",
        claims=[
            ClaimRef(
                id="c1",
                epistemic="FACT",
                kind="symbol_changed",
                text="login changed in auth.py",
            )
        ],
    )
    user_prompt = build_user_message(packet)
    schema = ExplanationDocument.model_json_schema()
    document = {"summary": "login changed in auth.py", "change_flow": []}
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "model": "gpt-test",
                "choices": [{"message": {"role": "assistant", "content": json.dumps(document)}}],
            },
        )

    provider = OpenAICompatibleProvider(
        base_url="https://api.example.test/v1",
        api_key="test-key",
        model="gpt-test",
        timeout_ms=5000,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.explain(
        ExplainRequest(
            packet=packet,
            depth="developer",
            system_prompt="system",
            user_prompt=user_prompt,
            json_schema=schema,
        )
    )
    assert seen["url"] == "https://api.example.test/v1/chat/completions"
    assert "/api/chat" not in seen["url"]
    assert seen["authorization"] == "Bearer test-key"
    assert seen["body"]["model"] == "gpt-test"
    assert seen["body"]["temperature"] == 0
    assert seen["body"]["stream"] is False
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": user_prompt},
    ]
    assert "login changed in auth.py" in seen["body"]["messages"][1]["content"]
    assert seen["body"]["response_format"]["json_schema"]["schema"] == schema
    assert "think" not in seen["body"]
    assert json.loads(result.content) == document
    assert result.model == "gpt-test"


def test_openai_http_error_omits_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    provider = OpenAICompatibleProvider(
        base_url="https://api.example.test/v1",
        api_key="sk-test-secret",
        model="gpt-test",
        timeout_ms=5000,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ExplanationCallError, match="status 401") as raised:
        provider.explain(_request())
    assert "sk-test-secret" not in str(raised.value)


def _block_ollama(monkeypatch) -> None:
    class ExplodingOllama:
        def __init__(self, *args, **kwargs):
            raise AssertionError("OllamaProvider must not be constructed")

    monkeypatch.setattr("app.llm.ollama.OllamaProvider", ExplodingOllama)


def _refuse_http(*args, **kwargs):
    raise AssertionError("HTTP client must not be created")


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
