"""Unified diffs from a GitHub compare payload.

The analyzer already asks GitHub for this compare. The patch text is not
stored on the run, so the details and comment paths read it again through
the same client. This is not a new analysis stage.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.github.auth import installation_token
from app.github.client import GitHubClient

logger = logging.getLogger(__name__)

_CACHE: dict[tuple[str, str, str], dict[str, str]] = {}


def patches_from_compare(payload: dict | None) -> dict[str, str]:
    """Map each changed path to the unified diff GitHub returned."""
    found: dict[str, str] = {}
    for item in (payload or {}).get("files") or []:
        if not isinstance(item, dict):
            continue
        path = item.get("filename") or item.get("path")
        patch = item.get("patch")
        if not isinstance(path, str) or not path:
            continue
        if not isinstance(patch, str) or not patch.strip():
            continue
        found[path] = patch.strip("\n")
    return found


def fetch_compare_patches(
    settings: Settings,
    full_name: str,
    base_sha: str,
    head_sha: str,
    installation_id: int | None,
) -> dict[str, str]:
    """Return per-file patches for this base/head pair. Empty when GitHub is unavailable."""
    if not full_name or not base_sha or not head_sha or not installation_id:
        return {}
    if not (settings.github_app_id or "").strip():
        return {}
    key = (full_name, base_sha, head_sha)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    client = None
    try:
        token = installation_token(settings, int(installation_id))
        client = GitHubClient(token=token, api_url=settings.github_api_url)
        patches = patches_from_compare(client.compare(full_name, base_sha, head_sha))
    except Exception as exc:
        logger.info("compare patches unavailable: %s", type(exc).__name__)
        return {}
    finally:
        if client is not None:
            client.close()
    _CACHE[key] = patches
    return patches
