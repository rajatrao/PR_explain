from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GithubInstallation(Base):
    """A GitHub App installation (an account that installed the app) and its repositories."""

    __tablename__ = "github_installations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_login: Mapped[str] = mapped_column(String(255))
    account_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    repositories: Mapped[list[Repository]] = relationship(
        back_populates="installation",
        cascade="all, delete-orphan",
    )


class Repository(Base):
    """A repository the app can see, linked to its installation, with its pull requests."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    installation_id: Mapped[int | None] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    full_name: Mapped[str] = mapped_column(String(512), index=True)
    default_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    installation: Mapped[GithubInstallation | None] = relationship(back_populates="repositories")
    pull_requests: Mapped[list[PullRequest]] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
    )


class PullRequest(Base):
    """A pull request in a repository, with the id of the explanation comment posted on it and its revisions."""

    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repository_id", "number", name="uq_pr_repo_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    explanation_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    repository: Mapped[Repository] = relationship(back_populates="pull_requests")
    revisions: Mapped[list[Revision]] = relationship(
        back_populates="pull_request",
        cascade="all, delete-orphan",
    )


class Revision(Base):
    """One head commit of a pull request (head and base SHA, title, body) and the analysis run for it."""

    __tablename__ = "revisions"
    __table_args__ = (UniqueConstraint("pull_request_id", "head_sha", name="uq_revision_sha"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="CASCADE")
    )
    head_sha: Mapped[str] = mapped_column(String(64))
    base_sha: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        server_default=func.now(),
    )
    pull_request: Mapped[PullRequest] = relationship(back_populates="revisions")
    run: Mapped[AnalysisRun | None] = relationship(
        back_populates="revision",
        cascade="all, delete-orphan",
        uselist=False,
    )
    delta: Mapped[RevisionDelta | None] = relationship(
        back_populates="revision",
        cascade="all, delete-orphan",
        uselist=False,
        foreign_keys="RevisionDelta.revision_id",
    )


class WebhookDelivery(Base):
    """A received GitHub webhook delivery, kept so a redelivered event is processed once."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    delivery_id: Mapped[str] = mapped_column(String(255), unique=True)
    event: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisRun(Base):
    """The analysis of one revision: status of the analysis, explanation, and comment phases, plus the stored symbols, relationships, evidence, claims, packet, explanation, and events."""

    __tablename__ = "analysis_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("revisions.id", ondelete="CASCADE"),
        unique=True,
    )
    analysis_status: Mapped[str] = mapped_column(String(32), default="queued")
    explanation_status: Mapped[str] = mapped_column(String(32), default="not_started")
    comment_status: Mapped[str] = mapped_column(String(32), default="pending")
    language_coverage: Mapped[str] = mapped_column(String(32), default="diff_only")
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_notes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    revision: Mapped[Revision] = relationship(back_populates="run")
    jobs: Mapped[list[AnalysisJob]] = relationship(back_populates="run", cascade="all, delete-orphan")
    symbols: Mapped[list[SymbolRow]] = relationship(back_populates="run", cascade="all, delete-orphan")
    relationships_: Mapped[list[RelationshipRow]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    evidences: Mapped[list[EvidenceRow]] = relationship(back_populates="run", cascade="all, delete-orphan")
    claims: Mapped[list[ClaimRow]] = relationship(back_populates="run", cascade="all, delete-orphan")
    packets: Mapped[list[ExplanationPacketRow]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    explanations: Mapped[list[ExplanationRow]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    events: Mapped[list[PipelineEvent]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class AnalysisJob(Base):
    """A queued unit of work for a run (analyze, explain, or comment) that a worker claims, runs, and retries."""

    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    phase: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        server_default=func.now(),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        server_default=func.now(),
    )
    run: Mapped[AnalysisRun] = relationship(back_populates="jobs")


class SymbolRow(Base):
    """A stored symbol from a run's analysis."""

    __tablename__ = "symbols"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    public_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(String(1024))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    exported: Mapped[bool] = mapped_column(default=False)
    changed: Mapped[bool] = mapped_column(default=False)
    run: Mapped[AnalysisRun] = relationship(back_populates="symbols")


class RelationshipRow(Base):
    """A stored relationship (CALLS, IMPORTS, TESTS, ...) between two symbols from a run's analysis."""

    __tablename__ = "relationships"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    public_id: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(64))
    source_public_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_public_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_name: Mapped[str] = mapped_column(String(512))
    target_name: Mapped[str] = mapped_column(String(512))
    source_file: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    target_file: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    evidence_public_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    run: Mapped[AnalysisRun] = relationship(back_populates="relationships_")


class EvidenceRow(Base):
    """A stored piece of evidence (file, lines, snippet) from a run's analysis."""

    __tablename__ = "evidences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    public_id: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(64))
    repo: Mapped[str] = mapped_column(String(512))
    commit_sha: Mapped[str] = mapped_column(String(64))
    file: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    run: Mapped[AnalysisRun] = relationship(back_populates="evidences")


class ClaimRow(Base):
    """A stored claim from a run's analysis, with the public ids of its evidence and supporting claims."""

    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    public_id: Mapped[str] = mapped_column(String(255))
    epistemic: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    evidence_public_ids: Mapped[list] = mapped_column(JSON, default=list)
    support_public_ids: Mapped[list] = mapped_column(JSON, default=list)
    run: Mapped[AnalysisRun] = relationship(back_populates="claims")


class ClaimEvidence(Base):
    """Link table between a claim and the evidence it cites."""

    __tablename__ = "claim_evidence"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"),
        primary_key=True,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidences.id", ondelete="CASCADE"),
        primary_key=True,
    )


class ClaimSupport(Base):
    """Link table between a claim and another claim that supports it."""

    __tablename__ = "claim_support"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"),
        primary_key=True,
    )
    support_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"),
        primary_key=True,
    )


class ExplanationPacketRow(Base):
    """The explanation packet (the facts sent to the model) stored for a run, one per run."""

    __tablename__ = "explanation_packets"
    __table_args__ = (UniqueConstraint("run_id", name="uq_packet_run"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"))
    payload: Mapped[dict] = mapped_column(JSON)
    selection_notes: Mapped[list] = mapped_column(JSON, default=list)
    run: Mapped[AnalysisRun] = relationship(back_populates="packets")


class ExplanationRow(Base):
    """The model explanation stored for a run, one per run: status, provider, model, prompt version, validated document, raw reply, and error."""

    __tablename__ = "explanations"
    __table_args__ = (UniqueConstraint("run_id", name="uq_explanation_run"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    document: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    run: Mapped[AnalysisRun] = relationship(back_populates="explanations")


class PipelineEvent(Base):
    """One step in a run's pipeline (webhook received, analysis, explanation, comment, ...) with its status and detail, in order."""

    __tablename__ = "pipeline_events"
    __table_args__ = (Index("ix_pipeline_events_run_ordinal", "run_id", "ordinal"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    delivery_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(String(240))
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        server_default=func.now(),
    )
    run: Mapped[AnalysisRun | None] = relationship(back_populates="events")


class RevisionDelta(Base):
    """What changed between a revision and the previous revision of the same pull request: claims added and removed."""

    __tablename__ = "revision_deltas"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("revisions.id", ondelete="CASCADE"),
        unique=True,
    )
    previous_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("revisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    previous_head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    added: Mapped[list] = mapped_column(JSON, default=list)
    removed: Mapped[list] = mapped_column(JSON, default=list)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[Revision] = relationship(
        back_populates="delta",
        foreign_keys=[revision_id],
    )
