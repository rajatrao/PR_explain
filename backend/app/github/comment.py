from __future__ import annotations

from app.explanation.changes import build_file_changes, file_body, render_file_changes_markdown
from app.explanation.details import build_details, render_details_markdown
from app.explanation.schema import EvidenceRef, ExplanationDocument

_LIMIT = 60000

_DETAILS_TITLES = (
    "High-level areas affected",
    "Key Changes",
    "Behavior Changes",
    "Risk Areas",
    "What changed",
    "Change flow",
    "Impact",
    "Shared code",
    "Why a file outside the diff matters",
    "Tests",
    "Unchanged boundary",
)
_REVIEW_TITLES = ("Reviewer Attention", "Review questions")
RETIRED_DETAILS_NOTE = "This content moved to the Explain comment."


def explain_marker(repo_full_name: str, pr_number: int) -> str:
    return f"<!-- pr-explain view=explain repo={repo_full_name} pr={pr_number} -->"


def details_marker(repo_full_name: str, pr_number: int) -> str:
    return f"<!-- pr-explain view=details repo={repo_full_name} pr={pr_number} -->"


def legacy_explain_marker(repo_full_name: str, pr_number: int) -> str:
    return f"<!-- pr-explain repo={repo_full_name} pr={pr_number} -->"


def render_pull_request_comment(
    *,
    document: ExplanationDocument | None,
    failure: str | None,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    app_base_url: str,
    run_id: str,
    evidence_by_id: dict[str, EvidenceRef],
    change_flow: str | None = None,
    bullets: list[str] | None = None,
    mermaid: str | None = None,
) -> str:
    marker = explain_marker(repo_full_name, pr_number)
    heading = f"## Explain for `{head_sha}`"
    footer = _footer(app_base_url, run_id)
    if failure or document is None:
        return _failure_body(marker, heading, failure, footer, "The Explain view is not available for this commit.")
    story = _bullet_block(bullets) or document.summary.strip()
    parts = [marker, heading, story, ""]
    _append_mermaid(parts, mermaid)
    _append_change_flow(parts, change_flow)
    parts.append(footer)
    body = "\n\n".join(part for part in parts if part is not None)
    if len(body) <= _LIMIT:
        return body
    short = [marker, heading, story, ""]
    _append_mermaid(short, mermaid)
    short.append(footer)
    trimmed = "\n\n".join(part for part in short if part)
    if len(trimmed) <= _LIMIT:
        return trimmed
    return trimmed[: _LIMIT - 1].rstrip() + "…"


def render_details_comment(
    *,
    failure: str | None,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    app_base_url: str,
    run_id: str,
    claims=None,
    sections=None,
    evidence=None,
    symbols=None,
    relationships=None,
    document_unknowns: list[str] | None = None,
    review_questions: list[str] | None = None,
) -> str:
    marker = details_marker(repo_full_name, pr_number)
    heading = f"## Details for `{head_sha}`"
    footer = _footer(app_base_url, run_id)
    if failure:
        reason = (failure or "The Details view is not available for this commit.").strip()
        return _bounded("\n\n".join(part for part in (marker, heading, reason, footer) if part))
    details = build_details(
        symbols=symbols or [],
        relationships=relationships or [],
        evidences=evidence or [],
        claims=claims or [],
        sections=sections or [],
        repo=repo_full_name,
        sha=head_sha,
        document_unknowns=document_unknowns,
        review_questions=review_questions,
    )
    details_md = render_details_markdown(details)
    body = "\n\n".join(part for part in (marker, heading, details_md, footer) if part)
    if len(body) <= _LIMIT:
        return body
    kept = "\n\n".join(part for part in (marker, heading, footer) if part)
    room = _LIMIT - len(kept) - 2
    if details_md and room > 80:
        shortened = _clip_preserving_flow(details_md, room)
        return _bounded("\n\n".join(part for part in (marker, heading, shortened, footer) if part))
    return _bounded(kept)


def render_combined_comment(
    *,
    document: ExplanationDocument | None,
    failure: str | None,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    app_base_url: str,
    run_id: str,
    evidence_by_id: dict[str, EvidenceRef],
    change_flow: str | None = None,
    bullets: list[str] | None = None,
    mermaid: str | None = None,
    claims=None,
    sections=None,
    evidence=None,
    symbols=None,
    relationships=None,
    document_unknowns: list[str] | None = None,
    review_questions: list[str] | None = None,
    patches: dict[str, str] | None = None,
) -> str:
    """One comment: Explain, then Details, then Review. The head SHA is in each heading."""
    del evidence_by_id
    marker = explain_marker(repo_full_name, pr_number)
    explain_heading = f"## Explain for `{head_sha}`"
    details_heading = f"## Details for `{head_sha}`"
    review_heading = f"## Review for `{head_sha}`"
    footer = _footer(app_base_url, run_id)
    tail = [footer] if footer else []
    if failure or document is None:
        explain_reason = (failure or "The Explain view is not available for this commit.").strip()
        details_reason = (failure or "The Details view is not available for this commit.").strip()
        review_reason = (failure or "The Review view is not available for this commit.").strip()
        return _bounded(
            "\n\n".join(
                [
                    marker,
                    explain_heading,
                    explain_reason,
                    details_heading,
                    details_reason,
                    review_heading,
                    review_reason,
                    *tail,
                ]
            )
        )
    story = _bullet_block(bullets) or document.summary.strip()
    explain_parts = [marker, explain_heading, story]
    _append_mermaid(explain_parts, mermaid)
    flow_parts: list[str] = []
    _append_change_flow(flow_parts, change_flow)
    built = build_details(
        symbols=symbols or [],
        relationships=relationships or [],
        evidences=evidence or [],
        claims=claims or [],
        sections=sections or [],
        repo=repo_full_name,
        sha=head_sha,
        document_unknowns=document_unknowns,
        review_questions=review_questions,
    )
    details_md = _markdown_for(built, _DETAILS_TITLES)
    review_md = _markdown_for(built, _REVIEW_TITLES)
    changes = build_file_changes(evidence or [], claims or [], patches)

    def assemble(flow: list[str], details_text: str, review_text: str) -> str:
        parts = [*explain_parts, *flow, details_heading, details_text, review_heading, review_text, *tail]
        return "\n\n".join(part for part in parts if part)

    if changes:
        fitted = _fit_file_changes(changes, details_md, review_md, flow_parts, assemble)
        if fitted is not None:
            return fitted

    body = assemble(flow_parts, details_md, review_md)
    if len(body) <= _LIMIT:
        return body
    without_flow = assemble([], details_md, review_md)
    if len(without_flow) <= _LIMIT:
        return without_flow
    head = "\n\n".join(part for part in (*explain_parts, details_heading) if part)
    tail_text = "\n\n".join(tail)
    room = _LIMIT - len(head) - len(review_heading) - len(tail_text) - 8
    if room < 120:
        return _bounded(assemble([], "", ""))
    return _bounded(
        assemble([], _clip_preserving_flow(details_md, room // 2), _clip_preserving_flow(review_md, room - room // 2))
    )


def _fit_file_changes(changes, details_md: str, review_md: str, flow: list[str], assemble) -> str | None:
    """Shrink file diff bodies until the comment fits. Drop a whole file block only when it still does not fit."""
    limits: dict[str, int] = {}
    seen: set[tuple[str, int]] = set()
    for _ in range(len(changes) * 16 + 4):
        block = render_file_changes_markdown(changes, limits or None)
        body = assemble(flow, _insert_changes(details_md, block), review_md)
        if len(body) <= _LIMIT:
            return body
        path = _largest_change(changes, limits)
        if path is None:
            return None
        current = limits.get(path, len(file_body(next(item for item in changes if item["path"] == path))))
        if current <= 1:
            nxt = 0
        else:
            overflow = len(body) - _LIMIT
            nxt = max(1, current - max(overflow, 64))
            if nxt >= current:
                nxt = current - 1
        mark = (path, nxt)
        if mark in seen:
            return None
        seen.add(mark)
        limits[path] = nxt
    return None


def _largest_change(changes: list[dict], limits: dict[str, int]) -> str | None:
    ranked = []
    for item in changes:
        size = limits.get(item["path"], len(file_body(item)))
        if size >= 1:
            ranked.append((size, item["path"]))
    if not ranked:
        return None
    ranked.sort()
    return ranked[-1][1]


def _insert_changes(details_md: str, changes_md: str) -> str:
    if not changes_md:
        return details_md
    heading = "### What changed"
    start = details_md.find(heading)
    if start == -1:
        return f"{changes_md}\n\n{details_md}" if details_md else changes_md
    next_heading = details_md.find("\n### ", start + len(heading))
    if next_heading == -1:
        return f"{details_md.rstrip()}\n\n{changes_md.rstrip()}\n"
    # A blank line after </details> ends the HTML block. Without it GitHub
    # prints the next section, including Change flow, as one line of raw text.
    return (
        f"{details_md[:next_heading].rstrip()}\n\n"
        f"{changes_md.rstrip()}\n\n"
        f"{details_md[next_heading:].lstrip()}"
    )


def _markdown_for(details: dict, titles: tuple[str, ...]) -> str:
    by_title = {section.get("title"): section for section in details.get("sections") or []}
    chosen = [by_title[title] for title in titles if title in by_title]
    if not chosen:
        return ""
    return render_details_markdown({"sections": chosen})


def _clip_preserving_flow(text: str, room: int) -> str:
    """Shorten text without cutting a Change flow item or a URL in half."""
    if room <= 1 or not text:
        return ""
    if len(text) <= room:
        return text
    prefix = _safe_prefix(text, room - 2)
    if not prefix:
        return "…"
    return prefix.rstrip() + "\n…"


def _safe_prefix(text: str, room: int) -> str:
    if room <= 0:
        return ""
    prefix = text[:room]
    prefix = _rewind_change_flow(text, prefix)
    if len(prefix) < len(text):
        newline = prefix.rfind("\n")
        prefix = prefix[:newline] if newline >= 0 else ""
    return prefix.rstrip()


def _rewind_change_flow(text: str, prefix: str) -> str:
    """If the cut lands inside a Change flow section, keep only whole items."""
    search = 0
    while True:
        start = text.find("### Change flow", search)
        if start == -1 or start >= len(prefix):
            return prefix
        end = _flow_section_end(text, start)
        if len(prefix) >= end:
            search = end
            continue
        kept = _complete_lines(text[start:end], len(prefix) - start)
        if not kept.strip():
            return text[:start]
        return text[:start] + kept


def _flow_section_end(text: str, start: int) -> int:
    rest = start + len("### Change flow")
    ends = [text.find(marker, rest) for marker in ("\n### ", "\n## ")]
    found = [index for index in ends if index != -1]
    return min(found) if found else len(text)


def _complete_lines(section: str, budget: int) -> str:
    if budget <= 0:
        return ""
    kept: list[str] = []
    used = 0
    for line in section.splitlines(keepends=True):
        if used + len(line) > budget:
            break
        kept.append(line)
        used += len(line)
    return "".join(kept)


def upsert_marked_comment(
    comment_client,
    full_name: str,
    pr_number: int,
    body: str,
    marker: str,
    *,
    fallback_id: int | None = None,
    legacy_marker: str | None = None,
) -> int:
    """PATCH the comment that already carries this marker. Create it once if missing."""
    from app.github.client import GitHubNotFound

    listed = _listed_comments(comment_client, full_name, pr_number)
    comment_id = _match_comment(listed, marker, legacy_marker, fallback_id)
    if comment_id is not None:
        try:
            comment_client.update_comment(full_name, comment_id, body)
            return comment_id
        except GitHubNotFound:
            pass
    return int(comment_client.create_comment(full_name, pr_number, body))


def publish_combined_comment(
    comment_client,
    full_name: str,
    pr_number: int,
    body: str,
    *,
    fallback_id: int | None = None,
) -> int:
    """Write Explain, Details, and Review onto the Explain comment. Retire a leftover Details comment."""
    comment_id = upsert_marked_comment(
        comment_client,
        full_name,
        pr_number,
        body,
        explain_marker(full_name, pr_number),
        fallback_id=fallback_id,
        legacy_marker=legacy_explain_marker(full_name, pr_number),
    )
    _retire_details_comment(comment_client, full_name, pr_number, keep_id=comment_id)
    return comment_id


def _retire_details_comment(comment_client, full_name: str, pr_number: int, *, keep_id: int) -> None:
    marker = details_marker(full_name, pr_number)
    delete_comment = getattr(comment_client, "delete_comment", None)
    for item in _listed_comments(comment_client, full_name, pr_number):
        comment_id = int(item["id"])
        body = item.get("body") or ""
        if comment_id == int(keep_id) or marker not in body:
            continue
        if callable(delete_comment):
            delete_comment(full_name, comment_id)
            continue
        if body.strip() == RETIRED_DETAILS_NOTE:
            continue
        comment_client.update_comment(full_name, comment_id, RETIRED_DETAILS_NOTE)


def _listed_comments(comment_client, full_name: str, pr_number: int) -> list[dict]:
    list_comments = getattr(comment_client, "list_comments", None)
    if list_comments is None:
        return []
    return list(list_comments(full_name, pr_number) or [])


def _match_comment(listed: list[dict], marker: str, legacy_marker: str | None, fallback_id: int | None) -> int | None:
    rows = [(int(item["id"]), item.get("body") or "") for item in listed]
    for comment_id, body in rows:
        if marker in body:
            return comment_id
    if legacy_marker:
        for comment_id, body in rows:
            if legacy_marker in body and "view=explain" not in body and "view=details" not in body:
                return comment_id
    if fallback_id is None:
        return None
    fallback = int(fallback_id)
    for comment_id, body in rows:
        if comment_id != fallback:
            continue
        if "view=details" in body and "view=explain" not in marker:
            return None
        if "view=explain" in body and "view=details" in marker:
            return None
        return comment_id
    return fallback


def _failure_body(marker: str, heading: str, failure: str | None, footer: str, fallback: str) -> str:
    reason = (failure or fallback).strip()
    parts = [marker, heading, reason]
    if footer:
        parts.append(footer)
    return "\n\n".join(parts)


def _bullet_block(bullets: list[str] | None) -> str:
    lines = [line.strip() for line in (bullets or []) if line and line.strip()]
    return "\n".join(f"- {line}" for line in lines)


def _append_mermaid(parts: list[str], mermaid: str | None) -> None:
    text = (mermaid or "").strip()
    if not text:
        return
    fenced = text.replace("```", "'''")
    parts.append("```mermaid\n" + fenced + "\n```")


def _append_change_flow(parts: list[str], change_flow: str | None) -> None:
    text = (change_flow or "").strip()
    if not text:
        return
    parts.append("### Change flow\n\n" + _flow_markdown(text))


def _flow_markdown(text: str) -> str:
    """Turn the indented change-flow blob into nested markdown bullets.

    A heading sits at column 0, each item is indented two spaces, and a
    file:line detail is indented four. Blank lines stay out so the list
    nests. Nothing is added that the blob does not already say.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        level = indent // 2
        lines.append(f"{'  ' * level}- {raw.strip()}")
    return "\n".join(lines)


def _bounded(body: str) -> str:
    return _clip_preserving_flow(body, _LIMIT)


def _footer(app_base_url: str, run_id: str) -> str:
    if not app_base_url:
        return "Explain and Details stay in the explanation app."
    link = f"{app_base_url.rstrip('/')}/runs/{run_id}"
    return f"Open Explain and Details in the explanation app: {link}"

