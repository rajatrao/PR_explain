from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

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


class BehaviorChangeFact(BaseModel):
    """One before/after statement pair read from the diff."""

    model_config = ConfigDict(extra="ignore")

    id: str
    claim_id: str
    kind: str
    before: str | None = None
    after: str | None = None
    before_when: str | None = None
    after_when: str | None = None


class BehaviorFunctionFact(BaseModel):
    """A changed function, who reaches it at the head commit, and its statement-level changes."""

    model_config = ConfigDict(extra="ignore")

    function: str
    file: str = ""
    public: bool
    removed: bool = False
    reached_from: list[str] = Field(default_factory=list)
    callers_at_head: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    changes: list[BehaviorChangeFact] = Field(default_factory=list)


class ImpactFact(BaseModel):
    """One rule-derived impact finding: scope, attention (with severity), dependents, verify, or coverage."""

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: str
    text: str
    severity: str | None = None
    behavior_ids: list[str] = Field(default_factory=list)


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
    behavior_facts: list[BehaviorFunctionFact] = Field(default_factory=list)
    impact_facts: list[ImpactFact] = Field(default_factory=list)


class Statement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    epistemic: Epistemic
    text: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class BehaviorChangeNote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    before: str
    after: str
    impact: str = ""
    fact_ids: list[str] = Field(default_factory=list)


class BehaviorWatchNote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    fact_ids: list[str] = Field(default_factory=list)


class BehavioralNarrative(BaseModel):
    """What the system does differently, written from behavior facts only."""

    model_config = ConfigDict(extra="ignore")

    overview: str = ""
    changes: list[BehaviorChangeNote] = Field(default_factory=list)
    watch: list[BehaviorWatchNote] = Field(default_factory=list)


class ImpactAreaNote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    severity: str = "low"
    summary: str
    who_notices: str = ""
    fact_ids: list[str] = Field(default_factory=list)


class ImpactNarrative(BaseModel):
    """What the pull request affects, written from impact and behavior facts only."""

    model_config = ConfigDict(extra="ignore")

    overview: str = ""
    areas: list[ImpactAreaNote] = Field(default_factory=list)


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
    behavioral_changes: BehavioralNarrative | None = None
    # Why the narrative was not kept. Set by the pipeline; hidden from the model's output schema.
    behavior_screening: SkipJsonSchema[list[str]] = Field(default_factory=list)
    impact: ImpactNarrative | None = None
    impact_screening: SkipJsonSchema[list[str]] = Field(default_factory=list)

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
