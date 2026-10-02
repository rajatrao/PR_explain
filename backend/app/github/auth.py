from __future__ import annotations

import time

import httpx
import jwt

from app.config import Settings


def app_jwt(settings: Settings, *, now: int | None = None) -> str:
    if not settings.github_app_id or not settings.github_app_private_key:
        raise RuntimeError("GitHub App credentials are not configured")
    issued = int(now if now is not None else time.time())
    private_key = settings.github_app_private_key.replace("\\n", "\n")
    payload = {"iat": issued - 60, "exp": issued + 540, "iss": settings.github_app_id}
    token = jwt.encode(payload, private_key, algorithm="RS256")
    if isinstance(token, bytes):
        return token.decode("utf-8")
    return token


def installation_token(settings: Settings, installation_id: int, http: httpx.Client | None = None) -> str:
    encoded = app_jwt(settings)
    client = http or httpx.Client(timeout=30)
    close = http is None
    try:
        response = client.post(
            f"{settings.github_api_url.rstrip('/')}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {encoded}",
                "Accept": "application/vnd.github+json",
            },
        )
        response.raise_for_status()
        return response.json()["token"]
    finally:
        if close:
            client.close()
