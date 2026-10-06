"""Load GitHub App installations and repositories into the database."""

from __future__ import annotations

import logging
import os
from typing import Protocol

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import session_factory
from app.github.auth import app_jwt, installation_token
from app.github.events import _upsert_installation, _upsert_repository

logger = logging.getLogger("app.bootstrap")


class InstallationCatalog(Protocol):
    """Source of the GitHub App's installations and their repositories, used to seed the database at startup."""

    def list_installations(self) -> list[dict]:
        """Return every installation of the GitHub App."""
        ...

    def list_repositories(self, installation_id: int) -> list[dict]:
        """Return the repositories an installation can access."""


def github_app_configured(settings: Settings) -> bool:
    if not (settings.github_app_id or "").strip():
        return False
    if (settings.github_app_private_key_file or "").strip():
        return True
    return bool((settings.github_app_private_key or "").strip())


def public_error(exc: Exception) -> str:
    """Error text safe to log. Omits credentials and response bodies."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    text = str(exc)
    lowered = text.lower()
    if (
        "bearer " in lowered
        or "private key" in lowered
        or "begin " in lowered
        or "ghs_" in lowered
        or "ghp_" in lowered
    ):
        return type(exc).__name__
    return f"{type(exc).__name__}: {text[:200]}"


class GithubAppCatalog:
    """Live installation and repository list for this GitHub App."""

    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.Client(timeout=30.0)
        self._owns_client = http is None

    def close(self) -> None:
        """Close the HTTP client when this catalog created it."""
        if self._owns_client:
            self._http.close()

    def list_installations(self) -> list[dict]:
        """Return every installation of the GitHub App, authenticated with the app's JWT."""
        token = app_jwt(self._settings)
        return _get_pages(self._http, self._settings.github_api_url, "/app/installations", token)

    def list_repositories(self, installation_id: int) -> list[dict]:
        """Return the repositories an installation can access, authenticated with an installation token."""
        try:
            token = installation_token(self._settings, installation_id, http=self._http)
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"GitHub installation token request failed with HTTP {exc.response.status_code}"
            ) from None
        return _get_pages(
            self._http,
            self._settings.github_api_url,
            "/installation/repositories",
            token,
            key="repositories",
        )


def sync_installed_repositories(session: Session, catalog: InstallationCatalog) -> dict:
    """Upsert installations and repositories returned by ``catalog``."""
    installation_ids: set[int] = set()
    repository_count = 0
    for installation in catalog.list_installations():
        installation_id = _as_int(installation.get("id"))
        if installation_id is None:
            continue
        try:
            repositories = catalog.list_repositories(installation_id)
        except Exception as exc:
            logger.error(
                "github bootstrap skipped installation %s: %s",
                installation_id,
                public_error(exc),
            )
            _save_installation(session, installation, installation_id)
            installation_ids.add(installation_id)
            continue
        _save_installation(session, installation, installation_id)
        installation_ids.add(installation_id)
        for repo in repositories:
            saved = _save_repository(session, installation_id, repo)
            if saved is None:
                continue
            repository_count += 1
            logger.info(
                "github bootstrap repository id=%s full_name=%s",
                saved.id,
                saved.full_name,
            )
    session.flush()
    return {
        "installations": len(installation_ids),
        "repositories": repository_count,
        "skipped": False,
    }


def run_bootstrap(
    settings: Settings | None = None,
    catalog: InstallationCatalog | None = None,
    session: Session | None = None,
) -> dict:
    """Sync the catalog. Opens and commits a session when one is not provided."""
    settings = settings or get_settings()
    owned_catalog = False
    if catalog is None:
        if not github_app_configured(settings):
            logger.info("github bootstrap skipped because app credentials are not configured")
            return {"installations": 0, "repositories": 0, "skipped": True}
        catalog = GithubAppCatalog(settings)
        owned_catalog = True
    try:
        if session is not None:
            return sync_installed_repositories(session, catalog)
        factory = session_factory()
        with factory() as owned:
            summary = sync_installed_repositories(owned, catalog)
            owned.commit()
            return summary
    finally:
        if owned_catalog:
            catalog.close()


def bootstrap_on_startup(settings: Settings | None = None) -> None:
    """Best-effort sync after migrations. A GitHub failure does not stop the process."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    settings = settings or get_settings()
    try:
        summary = run_bootstrap(settings)
    except Exception as exc:
        logger.error("github bootstrap failed: %s", public_error(exc))
        return
    if summary.get("skipped"):
        return
    logger.info(
        "github bootstrap stored installations=%s repositories=%s",
        summary["installations"],
        summary["repositories"],
    )


def _save_installation(session: Session, installation: dict, installation_id: int):
    payload = {**installation, "id": installation_id}
    try:
        with session.begin_nested():
            return _upsert_installation(session, payload)
    except IntegrityError:
        return _upsert_installation(session, payload)


def _save_repository(session: Session, installation_id: int, repo: dict):
    try:
        with session.begin_nested():
            return _upsert_repository(session, installation_id, repo)
    except IntegrityError:
        return _upsert_repository(session, installation_id, repo)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _next_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' not in section:
            continue
        start = section.find("<")
        end = section.find(">")
        if start != -1 and end > start:
            return section[start + 1 : end]
    return None


def _get_pages(
    http: httpx.Client,
    api_url: str,
    path: str,
    token: str,
    key: str | None = None,
) -> list[dict]:
    url: str | None = f"{api_url.rstrip('/')}{path}"
    params: dict[str, int] | None = {"per_page": 100}
    collected: list[dict] = []
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    while url:
        response = http.get(url, headers=headers, params=params)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            raise RuntimeError(f"GitHub API {path} failed with HTTP {response.status_code}") from None
        body = response.json()
        if key and isinstance(body, dict):
            chunk = body.get(key) or []
        elif isinstance(body, list):
            chunk = body
        else:
            chunk = []
        if isinstance(chunk, list):
            collected.extend(item for item in chunk if isinstance(item, dict))
        url = _next_url(response.headers.get("Link"))
        params = None
    return collected


def main() -> None:
    settings = get_settings()
    if settings.run_migrations:
        from app.db.migrate import upgrade

        upgrade()
    from app.logsetup import configure_logging

    configure_logging()
    try:
        summary = run_bootstrap(settings)
    except Exception as exc:
        logger.error("github bootstrap failed: %s", public_error(exc))
        raise SystemExit(1) from None
    if summary.get("skipped"):
        logger.error("github bootstrap skipped because app credentials are not configured")
        raise SystemExit(1)
    logger.info(
        "github bootstrap stored installations=%s repositories=%s",
        summary["installations"],
        summary["repositories"],
    )


if __name__ == "__main__":
    main()
