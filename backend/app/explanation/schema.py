from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ExplanationDepth = Literal["quick", "developer", "deep", "architecture"]
Epistemic = Literal["FACT", "INFERENCE", "UNKNOWN"]


class RevisionRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repository: str
    pr_number: int
    head_sha: str
    base_sha: str
    title: str | None = None
    body: str | None = None


class ClaimRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    epistemic: Epistemic
    kind: str
    text: str
    subject: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class SymbolRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    exported: bool
    changed: bool


class RelationshipRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: str
    source: str
    target: str
    source_file: str | None = None
    target_file: str | None = None
    evidence_id: str | None = None


class ImpactRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    area: str
    summary: str
    claim_ids: list[str] = Field(default_factory=list)


class TestRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    file_path: str
    symbol: str
    present: bool
    claim_id: str | None = None


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: str
    repo: str
    commit_sha: str
    file: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None
    description: str
    snippet: str | None = None


class UnknownRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    text: str
    claim_id: str


class ContextNote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    text: str


class ExplanationPacket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    revision: RevisionRef
    depth: ExplanationDepth
    claims: list[ClaimRef] = Field(default_factory=list)
    symbols: list[SymbolRef] = Field(default_factory=list)
    relationships: list[RelationshipRef] = Field(default_factory=list)
    impact: list[ImpactRef] = Field(default_factory=list)
    tests: list[TestRef] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    unknowns: list[UnknownRef] = Field(default_factory=list)
    context_notes: list[ContextNote] = Field(default_factory=list)


class Statement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    epistemic: Epistemic
    text: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ExplanationDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str
    change_flow: list[Statement] = Field(default_factory=list)
    impacts: list[Statement] = Field(default_factory=list)
    important_changes: list[Statement] = Field(default_factory=list)
    tests: list[Statement] = Field(default_factory=list)
    unchanged: list[Statement] = Field(default_factory=list)
    unknowns: list[Statement] = Field(default_factory=list)
    review_questions: list[Statement] = Field(default_factory=list)

    def statements(self) -> list[Statement]:
        return [
            *self.change_flow,
            *self.impacts,
            *self.important_changes,
            *self.tests,
            *self.unchanged,
            *self.unknowns,
            *self.review_questions,
        ]
