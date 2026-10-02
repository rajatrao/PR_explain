from __future__ import annotations

from app.explanation.schema import EvidenceRef, ExplanationDocument, Statement

_LIMIT = 60000
_SECTIONS = (
    ("Change flow", "change_flow"),
    ("Impacts", "impacts"),
    ("Important changes", "important_changes"),
    ("Tests", "tests"),
    ("Unchanged", "unchanged"),
    ("Unknowns", "unknowns"),
    ("Review questions", "review_questions"),
)
_SHORT_SECTIONS = ("impacts", "unknowns", "review_questions")


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
) -> str:
    marker = f"<!-- pr-explain repo={repo_full_name} pr={pr_number} -->"
    heading = f"## PR Explain for `{head_sha}`"
    footer = _footer(app_base_url, run_id)
    if failure or document is None:
        reason = failure or "Explanation failed for this commit."
        body = "\n\n".join(
            [
                marker,
                heading,
                f"Explanation failed for `{head_sha}`. {reason}",
                "Deterministic claims remain on the explanation page.",
                footer,
            ]
        )
        return body
    parts = [marker, heading, document.summary.strip(), ""]
    for title, field_name in _SECTIONS:
        rendered = _render_section(
            title,
            getattr(document, field_name),
            evidence_by_id,
            repo_full_name,
            head_sha,
        )
        if rendered:
            parts.append(rendered)
    parts.append(footer)
    body = "\n\n".join(part for part in parts if part is not None)
    if len(body) <= _LIMIT:
        return body
    short = [marker, heading, document.summary.strip(), ""]
    for title, field_name in _SECTIONS:
        if field_name not in _SHORT_SECTIONS:
            continue
        rendered = _render_section(
            title,
            getattr(document, field_name),
            evidence_by_id,
            repo_full_name,
            head_sha,
        )
        if rendered:
            short.append(rendered)
    short.append("The full explanation is on the explanation page.")
    short.append(footer)
    return "\n\n".join(part for part in short if part)


def _render_section(title, statements: list[Statement], evidence_by_id, repo: str, sha: str) -> str:
    if not statements:
        return ""
    lines = [f"### {title}"]
    for statement in statements:
        lines.append(f"- **{statement.epistemic}** — {statement.text}")
        for evidence_id in statement.evidence_ids:
            item = evidence_by_id.get(evidence_id)
            if item is None or not item.file:
                continue
            lines.append(f"  - [{_label(item)}]({_blob(repo, sha, item)})")
    return "\n".join(lines)


def _label(item: EvidenceRef) -> str:
    if item.start_line and item.end_line and item.end_line != item.start_line:
        return f"{item.file}:{item.start_line}-{item.end_line}"
    if item.start_line:
        return f"{item.file}:{item.start_line}"
    return item.file or "evidence"


def _blob(repo: str, sha: str, item: EvidenceRef) -> str:
    url = f"https://github.com/{repo}/blob/{sha}/{item.file}"
    if item.start_line and item.end_line and item.end_line != item.start_line:
        return f"{url}#L{item.start_line}-L{item.end_line}"
    if item.start_line:
        return f"{url}#L{item.start_line}"
    return url


def _footer(app_base_url: str, run_id: str) -> str:
    if not app_base_url:
        return "Quick, Deep, and Architecture stay in the explanation app."
    link = f"{app_base_url.rstrip('/')}/runs/{run_id}"
    return f"Open Quick, Deep, and Architecture in the explanation app: {link}"
