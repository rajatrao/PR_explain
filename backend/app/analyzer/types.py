from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileChange:
    path: str
    status: str
    patch: str | None = None


@dataclass
class Snapshot:
    repository: str
    base_sha: str
    head_sha: str
    files: dict[str, str]
    changes: list[FileChange]
    pr_number: int = 0
    pr_title: str | None = None
    pr_body: str | None = None


@dataclass
class Symbol:
    id: str
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    exported: bool
    changed: bool = False


@dataclass
class Relationship:
    id: str
    type: str
    source_id: str | None
    target_id: str | None
    source_name: str
    target_name: str
    source_file: str | None
    target_file: str | None
    evidence_id: str | None = None


@dataclass
class Evidence:
    id: str
    type: str
    repo: str
    commit_sha: str
    file: str | None
    start_line: int | None
    end_line: int | None
    symbol: str | None
    description: str
    snippet: str | None = None


@dataclass
class Claim:
    id: str
    epistemic: str
    kind: str
    text: str
    subject: str | None
    evidence_ids: list[str] = field(default_factory=list)
    support_ids: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    language_coverage: str
    symbols: list[Symbol]
    relationships: list[Relationship]
    evidences: list[Evidence]
    claims: list[Claim]
    context_notes: list[str] = field(default_factory=list)
