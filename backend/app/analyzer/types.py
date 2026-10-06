from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileChange:
    """One file in the compare diff: its path, status (added, modified, removed, renamed), and unified patch text."""

    path: str
    status: str
    patch: str | None = None


@dataclass
class Snapshot:
    """Everything the analyzer reads for one pull request revision: the head-commit file contents, the changed files with patches, and the pull request metadata."""

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
    """A function, class, or other definition found at the head commit, with its location, whether it is exported, and whether the diff changes it."""

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
    """A resolved edge between two symbols (CALLS, IMPORTS, TESTS, and similar) with the evidence that shows it."""

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
    """A located piece of source that backs a claim or relationship: repository, commit, file, line range, and snippet."""

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
    """A statement the analyzer makes about the change, labeled FACT, INFERENCE, or UNKNOWN, with the evidence and supporting claims it rests on."""

    id: str
    epistemic: str
    kind: str
    text: str
    subject: str | None
    evidence_ids: list[str] = field(default_factory=list)
    support_ids: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """The output of one analysis: symbols, relationships, evidence, claims, language coverage, and notes about what was analyzed."""

    language_coverage: str
    symbols: list[Symbol]
    relationships: list[Relationship]
    evidences: list[Evidence]
    claims: list[Claim]
    context_notes: list[str] = field(default_factory=list)
