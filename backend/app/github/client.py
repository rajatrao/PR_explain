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


class GitHubNotFound(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, *, token: str, api_url: str, http: httpx.Client | None = None) -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._http = http or httpx.Client(timeout=60, follow_redirects=True)
        self._owns_client = http is None

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def request(self, method: str, path: str, **kwargs):
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
        return self.request("GET", f"/repos/{full_name}/compare/{base_sha}...{head_sha}")

    def download_tarball(self, full_name: str, sha: str) -> bytes:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }
        response = self._http.get(
            f"{self._api_url}/repos/{full_name}/tarball/{sha}",
            headers=headers,
        )
        response.raise_for_status()
        return response.content

    def create_comment(self, full_name: str, pr_number: int, body: str) -> int:
        payload = self.request(
            "POST",
            f"/repos/{full_name}/issues/{pr_number}/comments",
            json={"body": body},
        )
        return int(payload["id"])

    def update_comment(self, full_name: str, comment_id: int, body: str) -> None:
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
    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def fetch(self, full_name: str, base_sha: str, head_sha: str) -> Snapshot:
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
