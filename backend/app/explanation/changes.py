"""Per-file source diffs for the Details tab and the GitHub comment.

The body of each file is a unified diff (added and removed lines), not a
line-range label. Patch text comes from the compare payload when the caller
has it.
"""

from __future__ import annotations

_REST_NOTE = "The rest of this file is on the web run page."


def build_file_changes(evidences, claims, patches: dict[str, str] | None = None) -> list[dict]:
    """One entry per changed path that has a unified diff."""
    del evidences
    stored = patches or {}
    paths: set[str] = set()
    for claim in claims or []:
        if _field(claim, "kind") != "file_changed":
            continue
        path = _field(claim, "subject")
        if isinstance(path, str) and path:
            paths.add(path)
    for path, patch in stored.items():
        if isinstance(path, str) and path and isinstance(patch, str) and patch.strip():
            paths.add(path)
    changes = []
    for path in sorted(paths):
        diff = _text(stored.get(path))
        if not diff:
            continue
        changes.append({"path": path, "diff": diff})
    return changes


def file_body(change: dict) -> str:
    """Unified diff for one file."""
    return _text(change.get("diff"))


def render_file_changes_markdown(changes: list[dict], limits: dict[str, int] | None = None) -> str:
    """Collapsed HTML details, one per path, with the diff in a code fence."""
    if not changes:
        return ""
    blocks = []
    for change in changes:
        limit = None if limits is None else limits.get(change["path"])
        if limit == 0:
            continue
        blocks.append(_details_block(change, limit))
    if not blocks:
        return ""
    return "### Changes\n\n" + "\n\n".join(blocks)


def _details_block(change: dict, limit: int | None) -> str:
    body = _limited(file_body(change), limit) or "No patch text was returned for this file."
    return (
        "<details>\n"
        f"<summary>{_escape(change['path'])}</summary>\n\n"
        f"{_fence(body)}\n"
        "</details>"
    )


def _limited(text: str, limit: int | None) -> str:
    if limit is None or len(text) <= limit:
        return text
    if limit <= len(_REST_NOTE):
        return _REST_NOTE
    return text[: limit - len(_REST_NOTE) - 1].rstrip() + "\n" + _REST_NOTE


def _fence(text: str) -> str:
    marker = "````" if "```" in text else "```"
    return f"{marker}diff\n{text.rstrip()}\n{marker}"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _field(item, name: str):
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _text(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip("\n")
