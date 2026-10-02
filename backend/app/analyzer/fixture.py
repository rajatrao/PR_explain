from __future__ import annotations

import json
from pathlib import Path

from app.analyzer.types import FileChange, Snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]
OAUTH_FIXTURE = REPO_ROOT / "fixtures" / "oauth-ts"


def load_oauth_snapshot(root: Path | None = None) -> Snapshot:
    fixture = root or OAUTH_FIXTURE
    meta = json.loads((fixture / "changes.json").read_text(encoding="utf-8"))
    head = fixture / "head"
    files: dict[str, str] = {}
    for path in head.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(head).as_posix()
        files[rel] = path.read_text(encoding="utf-8")
    changes = [
        FileChange(
            path=item["path"],
            status=item["status"],
            patch=item.get("patch"),
        )
        for item in meta["changes"]
    ]
    return Snapshot(
        repository=meta["repository"],
        base_sha=meta["base_sha"],
        head_sha=meta["head_sha"],
        files=files,
        changes=changes,
        pr_number=int(meta.get("pr_number") or 0),
        pr_title=meta.get("pr_title"),
        pr_body=meta.get("pr_body"),
    )
