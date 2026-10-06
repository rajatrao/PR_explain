from __future__ import annotations

import io
import posixpath
import shutil
import tarfile
import tempfile
from pathlib import Path

import httpx

from app.analyzer.types import FileChange, Snapshot

SKIP_DIRS = {"node_modules", "dist", "build", "coverage", ".git"}
_MAX_FILE = 1_000_000
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5


class GitHubNotFound(RuntimeError):
    """Raised when a GitHub API call returns 404."""

    pass


class GitHubClient:
    """Minimal GitHub REST client authenticated with an installation token: compare, tarball download, and pull request comments."""

    def __init__(self, *, token: str, api_url: str, http: httpx.Client | None = None) -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._http = http or httpx.Client(timeout=60, follow_redirects=True)
        self._owns_client = http is None

    def close(self) -> None:
        """Close the HTTP client when this client created it."""
        if self._owns_client:
            self._http.close()

    def request(self, method: str, path: str, **kwargs):
        """Send an authenticated API request and return the JSON body (None when empty); raises GitHubNotFound on 404."""
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._token}"
        headers["Accept"] = "application/vnd.github+json"
        response = self._http.request(method, f"{self._api_url}{path}", headers=headers, **kwargs)
        if response.status_code == 404:
            raise GitHubNotFound(response.text[:500])
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    def compare(self, full_name: str, base_sha: str, head_sha: str) -> dict:
        """Return the compare payload (changed files and patches) between two commits."""
        return self.request("GET", f"/repos/{full_name}/compare/{base_sha}...{head_sha}")

    def download_tarball(self, full_name: str, sha: str) -> bytes:
        """Download the repository tarball at a commit, dropping the auth header on cross-host redirects."""
        owner, repo = full_name.split("/", 1)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }
        response = self._get_without_cross_host_auth(
            f"{self._api_url}/repos/{owner}/{repo}/tarball/{sha}",
            headers,
        )
        response.raise_for_status()
        return response.content

    def _get_without_cross_host_auth(self, url: str, headers: dict[str, str]) -> httpx.Response:
        """Follow archive redirects ourselves.

        GitHub answers the tarball route with 302 to codeload.github.com and
        puts a credential in that Location query. codeload rejects the request
        when the original Authorization header is forwarded.
        """
        current = url
        current_headers = dict(headers)
        response: httpx.Response | None = None
        for _ in range(_MAX_REDIRECTS):
            response = self._http.get(current, headers=current_headers, follow_redirects=False)
            if response.status_code not in _REDIRECT_STATUSES:
                return response
            location = response.headers.get("Location")
            if not location:
                return response
            nxt = httpx.URL(location)
            base = httpx.URL(current)
            if nxt.is_relative_url:
                nxt = base.join(nxt)
            if nxt.host != base.host:
                current_headers = {
                    key: value
                    for key, value in current_headers.items()
                    if key.lower() != "authorization"
                }
            current = str(nxt)
        assert response is not None
        return response

    def list_comments(self, full_name: str, pr_number: int) -> list[dict]:
        """Return the pull request's issue comments (id and body), up to 20 pages."""
        found: list[dict] = []
        page = 1
        while page <= 20:
            batch = self.request(
                "GET",
                f"/repos/{full_name}/issues/{pr_number}/comments",
                params={"per_page": 100, "page": page},
            )
            if not batch:
                break
            for item in batch:
                found.append({"id": int(item["id"]), "body": item.get("body") or ""})
            if len(batch) < 100:
                break
            page += 1
        return found

    def create_comment(self, full_name: str, pr_number: int, body: str) -> int:
        """Post a comment on the pull request and return its id."""
        payload = self.request(
            "POST",
            f"/repos/{full_name}/issues/{pr_number}/comments",
            json={"body": body},
        )
        return int(payload["id"])

    def update_comment(self, full_name: str, comment_id: int, body: str) -> None:
        """Replace the body of an existing comment."""
        self.request(
            "PATCH",
            f"/repos/{full_name}/issues/comments/{comment_id}",
            json={"body": body},
        )


def files_from_tarball(blob: bytes) -> dict[str, str]:
    root = Path(tempfile.mkdtemp(prefix="pr-explain-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            archive.extractall(root, filter="data")
        files: dict[str, str] = {}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(root).parts
            if len(relative_parts) <= 1:
                rel = path.name
            else:
                rel = posixpath.join(*relative_parts[1:])
            if any(part in SKIP_DIRS for part in rel.split("/")):
                continue
            if path.stat().st_size > _MAX_FILE:
                continue
            try:
                files[rel] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        return files
    finally:
        shutil.rmtree(root, ignore_errors=True)


def snapshot_at_sha(
    *,
    repository: str,
    base_sha: str,
    head_sha: str,
    files: dict[str, str],
    compare_payload: dict,
    pr_number: int = 0,
    title: str | None = None,
    body: str | None = None,
) -> Snapshot:
    changes = []
    for item in compare_payload.get("files") or []:
        changes.append(
            FileChange(
                path=item.get("filename") or item.get("path"),
                status=item.get("status") or "modified",
                patch=item.get("patch"),
            )
        )
    return Snapshot(
        repository=repository,
        base_sha=base_sha,
        head_sha=head_sha,
        files=files,
        changes=changes,
        pr_number=pr_number,
        pr_title=title,
        pr_body=body,
    )


class GithubSnapshotSource:
    """Builds a Snapshot from GitHub: head-commit files from the tarball and the changes from the compare API."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def fetch(self, full_name: str, base_sha: str, head_sha: str) -> Snapshot:
        """Return the Snapshot for a repository between two commits."""
        blob = self._client.download_tarball(full_name, head_sha)
        files = files_from_tarball(blob)
        compare = self._client.compare(full_name, base_sha, head_sha)
        return snapshot_at_sha(
            repository=full_name,
            base_sha=base_sha,
            head_sha=head_sha,
            files=files,
            compare_payload=compare,
        )
