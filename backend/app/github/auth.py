from __future__ import annotations

import time
from pathlib import Path

import httpx
import jwt

from app.config import Settings


def normalize_private_key(value: str) -> str:
    """Accept a one-line env PEM or a PEM that already contains newlines.

    Wrapping quotes are removed. Literal backslash-n escapes become real
    newlines. A PEM that already contains newlines is left intact.
    """
    key = value.strip(" \t\r\ufeff")
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1].strip(" \t\r\ufeff")
    return key.replace("\\n", "\n")


def load_private_key(settings: Settings) -> str:
    """Use the PEM file when configured, otherwise the env private key."""
    path = (settings.github_app_private_key_file or "").strip()
    if path:
        return _read_private_key_file(path)
    if not settings.github_app_private_key:
        raise RuntimeError("GitHub App credentials are not configured")
    return normalize_private_key(settings.github_app_private_key)


def _read_private_key_file(path: str) -> str:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RuntimeError(f"GitHub App private key file was not found: {path}") from None
    except OSError as exc:
        reason = exc.strerror or "unreadable"
        raise RuntimeError(f"GitHub App private key file could not be read: {path} ({reason})") from None
    except UnicodeDecodeError:
        raise RuntimeError(f"GitHub App private key file could not be read: {path} (not valid text)") from None
    return normalize_private_key(raw)


def app_jwt(settings: Settings, *, now: int | None = None) -> str:
    if not settings.github_app_id:
        raise RuntimeError("GitHub App credentials are not configured")
    issued = int(now if now is not None else time.time())
    private_key = load_private_key(settings)
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
