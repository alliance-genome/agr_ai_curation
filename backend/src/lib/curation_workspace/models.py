"""SQLAlchemy models for curation workspace persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.sql.database import Base
from src.schemas.curation_workspace import (
    CurationActionType,
    CurationActorType,
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationEvidenceSource,
    CurationExtractionSourceKind,
    CurationSessionSortField,
    CurationSessionStatus,
    CurationSortDirection,
    CurationSubmissionStatus,
    CurationValidationScope,
    CurationValidationSnapshotState,
    SubmissionMode,
)


JSONB_EMPTY_ARRAY = text("'[]'::jsonb")
JSONB_EMPTY_OBJECT = text("'{}'::jsonb")
FK_ON_DELETE_NO_ACTION = "NO ACTION"


def _enum_type(enum_cls: Any) -> Enum:
    """Build a non-native enum so persisted columns remain VARCHAR-based."""

    return Enum(
        enum_cls,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
    )


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, ondelete=FK_ON_DELETE_NO_ACTION)


class CurationReviewSession(Base):
    """Top-level persisted review session."""

    __tablename__ = "curation_review_sessions"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    status: Mapped[CurationSessionStatus] = mapped_column(
        _enum_type(CurationSessionStatus),
        nullable=False,
        default=CurationSessionStatus.NEW,
        server_default=CurationSessionStatus.NEW.value,
    )
    adapter_key: Mapped[str] = mapped_column(String(), nullable=False)
    profile_key: Mapped[str | None] = mapped_column(String(), nullable=True)
    document_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("pdf_documents.id"),
        nullable=False,
    )
    flow_run_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    # Keep this pointer unconstrained so the session->candidate reference does
    # not create a circular FK dependency with curation_candidates.session_id.
    current_candidate_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        nullable=True,
    )
    assigned_curator_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    session_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    total_candidates: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    reviewed_candidates: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    pending_candidates: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    accepted_candidates: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    rejected_candidates: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    manual_candidates: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_worked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    candidates: Mapped[list["CurationCandidate"]] = relationship(
        "CurationCandidate",
        back_populates="session",
        order_by="CurationCandidate.order",
    )
    validation_snapshots: Mapped[list["CurationValidationSnapshot"]] = relationship(
        "CurationValidationSnapshot",
        back_populates="session",
        order_by="CurationValidationSnapshot.requested_at",
    )
    submissions: Mapped[list["CurationSubmissionRecord"]] = relationship(
        "CurationSubmissionRecord",
        back_populates="session",
        order_by="CurationSubmissionRecord.requested_at",
    )
    action_log_entries: Mapped[list["CurationActionLogEntry"]] = relationship(
        "CurationActionLogEntry",
        back_populates="session",
        order_by="CurationActionLogEntry.occurred_at",
    )

    __table_args__ = (
        CheckConstraint("session_version >= 1", name="ck_curation_review_sessions_version"),
        CheckConstraint(
            "total_candidates >= 0 AND reviewed_candidates >= 0 AND pending_candidates >= 0 "
            "AND accepted_candidates >= 0 AND rejected_candidates >= 0 "
            "AND manual_candidates >= 0",
            name="ck_curation_review_sessions_progress_nonnegative",
        ),
        Index("ix_curation_sessions_status", "status"),
        Index("ix_curation_sessions_adapter_key", "adapter_key"),
        Index(
            "ix_curation_sessions_flow_run_id",
            "flow_run_id",
            postgresql_where=text("flow_run_id IS NOT NULL"),
        ),
        Index("ix_curation_sessions_assigned_curator", "assigned_curator_id"),
        Index("ix_curation_sessions_prepared_at", text("prepared_at DESC")),
        Index("ix_curation_sessions_last_worked", text("last_worked_at DESC NULLS LAST")),
        Index("ix_curation_sessions_document", "document_id"),
    )


class CurationExtractionResultRecord(Base):
    """Persisted extraction envelope used to seed review sessions."""

    __tablename__ = "extraction_results"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    document_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("pdf_documents.id"),
        nullable=False,
    )
    adapter_key: Mapped[str | None] = mapped_column(String(), nullable=True)
    profile_key: Mapped[str | None] = mapped_column(String(), nullable=True)
    domain_key: Mapped[str | None] = mapped_column(String(), nullable=True)
    agent_key: Mapped[str] = mapped_column(String(), nullable=False)
    source_kind: Mapped[CurationExtractionSourceKind] = mapped_column(
        _enum_type(CurationExtractionSourceKind),
        nullable=False,
    )
    origin_session_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    flow_run_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    candidate_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    conversation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSONB, nullable=False)
    extraction_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    candidates: Mapped[list["CurationCandidate"]] = relationship(
        "CurationCandidate",
        back_populates="extraction_result",
    )

    __table_args__ = (
        CheckConstraint("candidate_count >= 0", name="ck_extraction_results_candidate_count"),
        Index("ix_extraction_results_document", "document_id"),
        Index(
            "ix_extraction_results_flow_run",
            "flow_run_id",
            postgresql_where=text("flow_run_id IS NOT NULL"),
        ),
    )


class CurationCandidate(Base):
    """Curator-facing candidate record within a review session."""

    __tablename__ = "curation_candidates"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("curation_review_sessions.id"),
        nullable=False,
    )
    source: Mapped[CurationCandidateSource] = mapped_column(
        _enum_type(CurationCandidateSource),
        nullable=False,
    )
    status: Mapped[CurationCandidateStatus] = mapped_column(
        _enum_type(CurationCandidateStatus),
        nullable=False,
        default=CurationCandidateStatus.PENDING,
        server_default=CurationCandidateStatus.PENDING.value,
    )
    order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    adapter_key: Mapped[str] = mapped_column(String(), nullable=False)
    profile_key: Mapped[str | None] = mapped_column(String(), nullable=True)
    display_label: Mapped[str | None] = mapped_column(String(), nullable=True)
    secondary_label: Mapped[str | None] = mapped_column(String(), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    conversation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    unresolved_ambiguities: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    extraction_result_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("extraction_results.id"),
        nullable=True,
    )
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    candidate_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[CurationReviewSession] = relationship(
        "CurationReviewSession",
        back_populates="candidates",
    )
    extraction_result: Mapped[CurationExtractionResultRecord | None] = relationship(
        "CurationExtractionResultRecord",
        back_populates="candidates",
    )
    draft: Mapped["CurationDraft | None"] = relationship(
        "CurationDraft",
        back_populates="candidate",
        uselist=False,
    )
    evidence_anchors: Mapped[list["CurationEvidenceRecord"]] = relationship(
        "CurationEvidenceRecord",
        back_populates="candidate",
        order_by="CurationEvidenceRecord.created_at",
    )
    validation_snapshots: Mapped[list["CurationValidationSnapshot"]] = relationship(
        "CurationValidationSnapshot",
        back_populates="candidate",
        order_by="CurationValidationSnapshot.requested_at",
    )
    action_log_entries: Mapped[list["CurationActionLogEntry"]] = relationship(
        "CurationActionLogEntry",
        back_populates="candidate",
        order_by="CurationActionLogEntry.occurred_at",
    )

    __table_args__ = (
        CheckConstraint('"order" >= 0', name="ck_curation_candidates_order"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_curation_candidates_confidence",
        ),
        Index("ix_curation_candidates_session", "session_id", "order"),
        Index("ix_curation_candidates_status", "session_id", "status"),
    )


class CurationEvidenceRecord(Base):
    """Evidence anchor persisted against a candidate."""

    __tablename__ = "evidence_anchors"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    candidate_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("curation_candidates.id"),
        nullable=False,
    )
    source: Mapped[CurationEvidenceSource] = mapped_column(
        _enum_type(CurationEvidenceSource),
        nullable=False,
    )
    field_keys: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    field_group_keys: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    anchor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    candidate: Mapped[CurationCandidate] = relationship(
        "CurationCandidate",
        back_populates="evidence_anchors",
    )

    __table_args__ = (
        Index("ix_evidence_anchors_candidate", "candidate_id"),
    )


class CurationDraft(Base):
    """Editable draft content for a candidate."""

    __tablename__ = "annotation_drafts"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    candidate_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("curation_candidates.id"),
        nullable=False,
        unique=True,
    )
    adapter_key: Mapped[str] = mapped_column(String(), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    title: Mapped[str | None] = mapped_column(String(), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )

    candidate: Mapped[CurationCandidate] = relationship(
        "CurationCandidate",
        back_populates="draft",
    )
    action_log_entries: Mapped[list["CurationActionLogEntry"]] = relationship(
        "CurationActionLogEntry",
        back_populates="draft",
        order_by="CurationActionLogEntry.occurred_at",
    )

    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_annotation_drafts_version"),
    )


class CurationValidationSnapshot(Base):
    """Persisted validation results for a session or candidate."""

    __tablename__ = "validation_snapshots"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    scope: Mapped[CurationValidationScope] = mapped_column(
        _enum_type(CurationValidationScope),
        nullable=False,
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("curation_review_sessions.id"),
        nullable=False,
    )
    candidate_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("curation_candidates.id"),
        nullable=True,
    )
    adapter_key: Mapped[str | None] = mapped_column(String(), nullable=True)
    state: Mapped[CurationValidationSnapshotState] = mapped_column(
        _enum_type(CurationValidationSnapshotState),
        nullable=False,
        default=CurationValidationSnapshotState.NOT_REQUESTED,
        server_default=CurationValidationSnapshotState.NOT_REQUESTED.value,
    )
    field_results: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    # Validation snapshots must persist the explicit API summary payload.
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[CurationReviewSession] = relationship(
        "CurationReviewSession",
        back_populates="validation_snapshots",
    )
    candidate: Mapped[CurationCandidate | None] = relationship(
        "CurationCandidate",
        back_populates="validation_snapshots",
    )

    __table_args__ = (
        CheckConstraint(
            "scope <> 'candidate' OR candidate_id IS NOT NULL",
            name="ck_validation_snapshots_candidate_scope",
        ),
        Index("ix_validation_snapshots_session", "session_id"),
        Index(
            "ix_validation_snapshots_candidate",
            "candidate_id",
            postgresql_where=text("candidate_id IS NOT NULL"),
        ),
    )


class CurationSubmissionRecord(Base):
    """Submission, export, or preview record for a session."""

    __tablename__ = "curation_submissions"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("curation_review_sessions.id"),
        nullable=False,
    )
    adapter_key: Mapped[str] = mapped_column(String(), nullable=False)
    mode: Mapped[SubmissionMode] = mapped_column(
        _enum_type(SubmissionMode),
        nullable=False,
    )
    target_key: Mapped[str] = mapped_column(String(), nullable=False)
    status: Mapped[CurationSubmissionStatus] = mapped_column(
        _enum_type(CurationSubmissionStatus),
        nullable=False,
    )
    readiness: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    payload: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB, nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(), nullable=True)
    response_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_errors: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    warnings: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[CurationReviewSession] = relationship(
        "CurationReviewSession",
        back_populates="submissions",
    )

    __table_args__ = (
        Index("ix_submissions_session", "session_id", text("requested_at DESC")),
    )


class CurationActionLogEntry(Base):
    """Immutable action log entry for session and candidate changes."""

    __tablename__ = "curation_action_log"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("curation_review_sessions.id"),
        nullable=False,
    )
    candidate_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("curation_candidates.id"),
        nullable=True,
    )
    draft_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("annotation_drafts.id"),
        nullable=True,
    )
    action_type: Mapped[CurationActionType] = mapped_column(
        _enum_type(CurationActionType),
        nullable=False,
    )
    actor_type: Mapped[CurationActorType] = mapped_column(
        _enum_type(CurationActorType),
        nullable=False,
    )
    actor: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_session_status: Mapped[CurationSessionStatus | None] = mapped_column(
        _enum_type(CurationSessionStatus),
        nullable=True,
    )
    new_session_status: Mapped[CurationSessionStatus | None] = mapped_column(
        _enum_type(CurationSessionStatus),
        nullable=True,
    )
    previous_candidate_status: Mapped[CurationCandidateStatus | None] = mapped_column(
        _enum_type(CurationCandidateStatus),
        nullable=True,
    )
    new_candidate_status: Mapped[CurationCandidateStatus | None] = mapped_column(
        _enum_type(CurationCandidateStatus),
        nullable=True,
    )
    changed_field_keys: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    evidence_anchor_ids: Mapped[list[UUID]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )

    session: Mapped[CurationReviewSession] = relationship(
        "CurationReviewSession",
        back_populates="action_log_entries",
    )
    candidate: Mapped[CurationCandidate | None] = relationship(
        "CurationCandidate",
        back_populates="action_log_entries",
    )
    draft: Mapped[CurationDraft | None] = relationship(
        "CurationDraft",
        back_populates="action_log_entries",
    )

    __table_args__ = (
        Index("ix_action_log_session", "session_id", text("occurred_at DESC")),
        Index(
            "ix_action_log_candidate",
            "candidate_id",
            text("occurred_at DESC"),
            postgresql_where=text("candidate_id IS NOT NULL"),
        ),
    )


class CurationSavedView(Base):
    """Persisted inventory filter and sort preset."""

    __tablename__ = "curation_saved_views"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(String(), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sort_by: Mapped[CurationSessionSortField] = mapped_column(
        _enum_type(CurationSessionSortField),
        nullable=False,
    )
    sort_direction: Mapped[CurationSortDirection] = mapped_column(
        _enum_type(CurationSortDirection),
        nullable=False,
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    created_by_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_saved_views_created_by", "created_by_id"),
    )


__all__ = [
    "CurationActionLogEntry",
    "CurationCandidate",
    "CurationDraft",
    "CurationEvidenceRecord",
    "CurationExtractionResultRecord",
    "CurationReviewSession",
    "CurationSavedView",
    "CurationSubmissionRecord",
    "CurationValidationSnapshot",
]
