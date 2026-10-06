from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

Epistemic = Literal["FACT", "INFERENCE", "UNKNOWN"]


class RevisionRef(BaseModel):
    """The pull request revision a packet describes: repository, number, head and base SHA, title, and body."""

    model_config = ConfigDict(extra="ignore")

    repository: str
    pr_number: int
    head_sha: str
    base_sha: str
    title: str | None = None
    body: str | None = None


class ClaimRef(BaseModel):
    """A claim as sent to the model in the packet."""

    model_config = ConfigDict(extra="ignore")

    id: str
    epistemic: Epistemic
    kind: str
    text: str
    subject: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class SymbolRef(BaseModel):
    """A symbol as sent to the model in the packet."""

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
    """A relationship as sent to the model in the packet."""

    model_config = ConfigDict(extra="ignore")

    id: str
    type: str
    source: str
    target: str
    source_file: str | None = None
    target_file: str | None = None
    evidence_id: str | None = None


class ImpactRef(BaseModel):
    """An impact area in the packet with the claims it rests on."""

    model_config = ConfigDict(extra="ignore")

    id: str
    area: str
    summary: str
    claim_ids: list[str] = Field(default_factory=list)


class TestRef(BaseModel):
    """A test that references a changed symbol, or a missing test, in the packet."""

    model_config = ConfigDict(extra="ignore")

    id: str
    file_path: str
    symbol: str
    present: bool
    claim_id: str | None = None


class EvidenceRef(BaseModel):
    """A piece of evidence as sent to the model in the packet."""

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
    """Something the analysis could not determine, with the claim that says so."""

    model_config = ConfigDict(extra="ignore")

    id: str
    text: str
    claim_id: str


class ContextNote(BaseModel):
    """A note about how the packet was built, such as claims omitted for the size budget."""

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
    # Where the change is in the diff, for evidence links. Not sent to the model.
    location: str | None = Field(default=None, exclude=True)
    href: str | None = Field(default=None, exclude=True)


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
    """The facts sent to the model for one revision: claims, symbols, evidence, tests, unknowns, behavior facts, and impact facts, fitted to a size budget."""

    model_config = ConfigDict(extra="ignore")

    revision: RevisionRef
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
    """One sentence of an explanation, labeled FACT, INFERENCE, or UNKNOWN, with the claims and evidence it cites."""

    model_config = ConfigDict(extra="ignore")

    epistemic: Epistemic
    text: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class BehaviorChangeNote(BaseModel):
    """One model-written behavioral change: title, before, after, who notices, and the behavior facts it cites."""

    model_config = ConfigDict(extra="ignore")

    title: str
    before: str
    after: str
    impact: str = ""
    fact_ids: list[str] = Field(default_factory=list)


class BehaviorWatchNote(BaseModel):
    """A question the model suggests a reviewer check, with the facts it cites."""

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
    """One model-written impact area: title, severity, summary, and the facts it cites."""

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


# --- Review tab -----------------------------------------------------------------------------

Priority = Literal["Critical", "High", "Medium", "Low"]
Confidence = Literal["High", "Medium", "Low"]
TestGroup = Literal[
    "Happy path",
    "Boundary cases",
    "Error/failure paths",
    "Regression cases",
    "Concurrency/async cases",
    "Data migration/backward compatibility",
    "Security/authorization cases",
]


class ReviewFact(BaseModel):
    """One rule-derived fact the review may rest on (r1, r2, …)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: str
    text: str
    location: str | None = None
    severity: str | None = None


class _Grounded(BaseModel):
    """Base for Review items: the fact ids and diff locations an item rests on, and whether rules or the model wrote it."""

    model_config = ConfigDict(extra="ignore")

    # Fact ids (b…, i…, r…) and diff locations ("path:line") the item rests on.
    fact_ids: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    # Set when the item comes from rules rather than the model. Hidden from the model's schema.
    source: SkipJsonSchema[str] = "model"


class AttentionArea(_Grounded):
    """A Review attention area: why it matters, what changed, what could go wrong, the files or functions involved, and a priority."""

    area: str
    why_it_matters: str
    what_changed: str
    what_could_go_wrong: str
    involved: list[str] = Field(default_factory=list)
    priority: Priority = "Medium"


class ReviewerQuestion(_Grounded):
    """A question a reviewer can ask the author."""

    question: str


class PotentialBug(_Grounded):
    """A possible bug or regression: the finding, its evidence, a triggering scenario, the impact, a confidence, and whether a rule confirms it."""

    finding: str
    evidence: str
    scenario: str
    impact: str
    confidence: Confidence = "Low"
    status: Literal["confirmed", "possible"] = "possible"


class MissingTest(_Grounded):
    """A test scenario that should exist and does not appear to, grouped by kind."""

    group: TestGroup
    scenario: str
    verifies: str


class SafeArea(_Grounded):
    """An area reviewed that looks reasonable, and why."""

    area: str
    why: str


class TopQuestion(_Grounded):
    """One of the ranked top review questions, with why to ask it and the relevant code."""

    question: str
    why_ask: str
    relevant_code: str


class RiskDriver(BaseModel):
    """One piece of evidence behind the overall review risk."""

    model_config = ConfigDict(extra="ignore")

    text: str
    level: Priority
    location: str | None = None
    fact_ids: list[str] = Field(default_factory=list)
    source: str = "rules"


class ReviewReport(BaseModel):
    """The Review tab: where to spend review time, written from the diff and the review facts."""

    model_config = ConfigDict(extra="ignore")

    attention: list[AttentionArea] = Field(default_factory=list)
    questions: list[ReviewerQuestion] = Field(default_factory=list)
    bugs: list[PotentialBug] = Field(default_factory=list)
    missing_tests: list[MissingTest] = Field(default_factory=list)
    safe: list[SafeArea] = Field(default_factory=list)
    top_questions: list[TopQuestion] = Field(default_factory=list)
    undetermined: list[str] = Field(default_factory=list)
    overall_risk: Priority = "Low"
    risk_reason: str = ""
    # The evidence the risk level rests on, computed by rule. Hidden from the model's schema.
    risk_drivers: SkipJsonSchema[list[RiskDriver]] = Field(default_factory=list)


class ExplanationDocument(BaseModel):
    """The validated explanation stored for a run: summary and statements, plus the Review report and screening notes added by the pipeline."""

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
    # Review tab report; written by a separate model call, so hidden from this schema.
    review: SkipJsonSchema[ReviewReport | None] = None
    review_screening: SkipJsonSchema[list[str]] = Field(default_factory=list)

    def statements(self) -> list[Statement]:
        """Every statement in the document, across all statement lists."""
        return [
            *self.change_flow,
            *self.impacts,
            *self.important_changes,
            *self.tests,
            *self.unchanged,
            *self.unknowns,
            *self.review_questions,
        ]
