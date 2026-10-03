"""OpenAI-compatible chat completions. Selected only when LLM_PROVIDER=openai."""

from __future__ import annotations

import time

import httpx

from app.llm.provider import ExplainRequest, ExplanationCallError, LLMResult


class OpenAICompatibleProvider:
    id = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_ms: int,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = httpx.Timeout(timeout_ms / 1000)
        self._client = client

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider(base_url={self._base_url!r}, model={self._model!r})"

    def explain(self, request: ExplainRequest) -> LLMResult:
        client = self._client or httpx.Client(timeout=self._timeout)
        close = self._client is None
        started = time.perf_counter()
        try:
            payload = {
                "model": self._model,
                "stream": False,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "explanation",
                        "schema": request.json_schema,
                    },
                },
            }
            if "think" in payload:
                raise ExplanationCallError("refusing to send a thinking flag")
            response = client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            if response.status_code >= 400:
                raise ExplanationCallError(
                    f"OpenAI-compatible chat failed with status {response.status_code}"
                )
            try:
                body = response.json()
                content = body["choices"][0]["message"]["content"]
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                raise ExplanationCallError(
                    "OpenAI-compatible chat returned a non-JSON body"
                ) from exc
            if not isinstance(content, str):
                raise ExplanationCallError("OpenAI-compatible chat returned an empty completion")
            elapsed = int((time.perf_counter() - started) * 1000)
            model = body.get("model") if isinstance(body, dict) else None
            return LLMResult(content=content, latency_ms=elapsed, model=model or self._model)
        except httpx.HTTPError as exc:
            raise ExplanationCallError(
                f"OpenAI-compatible endpoint is unreachable: {exc.__class__.__name__}"
            ) from exc
        finally:
            if close:
                client.close()
