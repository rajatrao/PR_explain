from __future__ import annotations

import time

import httpx

from app.llm.provider import ExplainRequest, ExplanationCallError, LLMResult


class OllamaProvider:
    """LLM provider that calls a local Ollama server's chat API with the output JSON schema and temperature 0."""

    id = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_ms: int,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = httpx.Timeout(timeout_ms / 1000)
        self._client = client

    def explain(self, request: ExplainRequest) -> LLMResult:
        """Send the request to Ollama after checking the model is available, and return the reply text, model, and latency."""
        client = self._client or httpx.Client(timeout=self._timeout)
        close = self._client is None
        started = time.perf_counter()
        try:
            self._check_ready(client)
            payload = {
                "model": self._model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                "options": {"temperature": 0},
                "format": request.json_schema,
            }
            if "think" in payload or "think" in payload.get("options", {}):
                raise ExplanationCallError("refusing to send a thinking flag")
            response = client.post(f"{self._base_url}/api/chat", json=payload)
            if response.status_code >= 400:
                raise ExplanationCallError(
                    f"Ollama chat failed with status {response.status_code}"
                )
            try:
                body = response.json()
                content = body["message"]["content"]
            except (ValueError, KeyError, TypeError) as exc:
                raise ExplanationCallError("Ollama returned a non-JSON chat body") from exc
            if not isinstance(content, str):
                raise ExplanationCallError("Ollama returned an empty completion")
            elapsed = int((time.perf_counter() - started) * 1000)
            return LLMResult(content=content, latency_ms=elapsed, model=body.get("model") or self._model)
        except httpx.HTTPError as exc:
            raise ExplanationCallError(f"Ollama is unreachable: {exc.__class__.__name__}") from exc
        finally:
            if close:
                client.close()

    def _check_ready(self, client: httpx.Client) -> None:
        try:
            response = client.get(f"{self._base_url}/api/tags")
        except httpx.HTTPError as exc:
            raise ExplanationCallError(f"Ollama is unreachable: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            raise ExplanationCallError(f"Ollama tags failed with status {response.status_code}")
        try:
            names = [item.get("name", "") for item in response.json().get("models", [])]
        except ValueError as exc:
            raise ExplanationCallError("Ollama tags response was not JSON") from exc
        if self._model not in names:
            raise ExplanationCallError("Ollama model is not available")
