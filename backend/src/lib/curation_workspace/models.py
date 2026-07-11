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
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

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
    CurationSubmissionAttemptState,
    CurationSubmissionStatus,
    CurationValidationScope,
    CurationValidationSnapshotState,
    SubmissionMode,
)
from src.schemas.domain_envelope import (
    CuratableObjectStatus,
    DomainEnvelopeStatus,
    HistoryActorType,
    HistoryEventKind,
    ValidationFindingSeverity,
    ValidationFindingStatus,
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
    idempotency_key: Mapped[str | None] = mapped_column(String(), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(), nullable=True)
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
        Index(
            "uq_extraction_results_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
    )


class DomainEnvelopeModel(Base):
    """Revisioned semantic source of truth for domain-pack curation state."""

    __tablename__ = "domain_envelopes"

    envelope_id: Mapped[str] = mapped_column(String(), primary_key=True)
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    project_key: Mapped[str] = mapped_column(String(), nullable=False)
    domain_pack_key: Mapped[str] = mapped_column(String(), nullable=False)
    domain_pack_version: Mapped[str | None] = mapped_column(String(), nullable=True)
    adapter_key: Mapped[str] = mapped_column(String(), nullable=False)
    source_extraction_result_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    source_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[DomainEnvelopeStatus] = mapped_column(
        _enum_type(DomainEnvelopeStatus),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("pdf_documents.id"),
        nullable=False,
    )
    session_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("curation_review_sessions.id"),
        nullable=True,
    )
    flow_run_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    schema_provider: Mapped[str | None] = mapped_column(String(), nullable=True)
    schema_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    object_model_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    model_field_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    envelope_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
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
    checkpointed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    objects: Mapped[list["DomainEnvelopeObject"]] = relationship(
        "DomainEnvelopeObject",
        back_populates="envelope",
        order_by="DomainEnvelopeObject.object_index",
    )
    validation_findings: Mapped[list["DomainValidationFinding"]] = relationship(
        "DomainValidationFinding",
        back_populates="envelope",
        order_by="DomainValidationFinding.finding_index",
    )
    history_events: Mapped[list["DomainEnvelopeHistory"]] = relationship(
        "DomainEnvelopeHistory",
        back_populates="envelope",
        order_by="DomainEnvelopeHistory.occurred_at",
    )
    projection_index: Mapped[list["DomainEnvelopeProjectionIndex"]] = relationship(
        "DomainEnvelopeProjectionIndex",
        back_populates="envelope",
        order_by="DomainEnvelopeProjectionIndex.projection_key",
    )

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_domain_envelopes_revision"),
        CheckConstraint(
            "session_id IS NOT NULL OR source_extraction_result_id IS NOT NULL",
            name="ck_domain_envelopes_owner_association",
        ),
        UniqueConstraint(
            "envelope_id",
            "session_id",
            name="uq_domain_envelopes_session_owner",
        ),
        Index("ix_domain_envelopes_document", "document_id"),
        Index(
            "ix_domain_envelopes_session",
            "session_id",
            postgresql_where=text("session_id IS NOT NULL"),
        ),
        Index(
            "ix_domain_envelopes_flow_run",
            "flow_run_id",
            postgresql_where=text("flow_run_id IS NOT NULL"),
        ),
        Index(
            "ix_domain_envelopes_domain_pack_status",
            "project_key",
            "domain_pack_key",
            "status",
        ),
        Index(
            "uq_domain_envelopes_source_scope",
            "source_extraction_result_id",
            "adapter_key",
            "domain_pack_key",
            unique=True,
            postgresql_where=text("source_extraction_result_id IS NOT NULL"),
        ),
    )


class DomainEnvelopeObject(Base):
    """Regenerated object lookup row for the current envelope revision."""

    __tablename__ = "domain_envelope_objects"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    envelope_id: Mapped[str] = mapped_column(
        String(),
        _fk("domain_envelopes.envelope_id"),
        nullable=False,
    )
    object_id: Mapped[str] = mapped_column(String(), nullable=False)
    pending_ref_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    envelope_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    object_index: Mapped[int] = mapped_column(Integer, nullable=False)
    object_type: Mapped[str] = mapped_column(String(), nullable=False)
    status: Mapped[CuratableObjectStatus] = mapped_column(
        _enum_type(CuratableObjectStatus),
        nullable=False,
    )
    validation_state: Mapped[str] = mapped_column(String(), nullable=False)
    schema_provider: Mapped[str | None] = mapped_column(String(), nullable=True)
    schema_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    object_model_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    model_field_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    object_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
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

    envelope: Mapped[DomainEnvelopeModel] = relationship(
        "DomainEnvelopeModel",
        back_populates="objects",
    )

    __table_args__ = (
        CheckConstraint("envelope_revision >= 1", name="ck_domain_envelope_objects_revision"),
        CheckConstraint("object_index >= 0", name="ck_domain_envelope_objects_index"),
        UniqueConstraint("envelope_id", "object_id", name="uq_domain_envelope_objects_current"),
        Index(
            "ix_domain_envelope_objects_lookup",
            "envelope_id",
            "envelope_revision",
            "object_type",
            "status",
            "validation_state",
        ),
    )


class DomainValidationFinding(Base):
    """Regenerated validation finding lookup row for the current envelope revision."""

    __tablename__ = "domain_validation_findings"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    envelope_id: Mapped[str] = mapped_column(
        String(),
        _fk("domain_envelopes.envelope_id"),
        nullable=False,
    )
    finding_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    envelope_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_index: Mapped[int] = mapped_column(Integer, nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    field_path: Mapped[str | None] = mapped_column(String(), nullable=True)
    severity: Mapped[ValidationFindingSeverity] = mapped_column(
        _enum_type(ValidationFindingSeverity),
        nullable=False,
    )
    status: Mapped[ValidationFindingStatus] = mapped_column(
        _enum_type(ValidationFindingStatus),
        nullable=False,
    )
    code: Mapped[str | None] = mapped_column(String(), nullable=True)
    object_model_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    model_field_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    finding_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
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

    envelope: Mapped[DomainEnvelopeModel] = relationship(
        "DomainEnvelopeModel",
        back_populates="validation_findings",
    )

    __table_args__ = (
        CheckConstraint("envelope_revision >= 1", name="ck_domain_validation_findings_revision"),
        CheckConstraint("finding_index >= 0", name="ck_domain_validation_findings_index"),
        UniqueConstraint(
            "envelope_id",
            "envelope_revision",
            "finding_index",
            name="uq_domain_validation_findings_revision_index",
        ),
        Index(
            "ix_domain_validation_findings_lookup",
            "envelope_id",
            "object_id",
            "field_path",
            "status",
            "severity",
        ),
    )


class DomainEnvelopeHistory(Base):
    """Append-only history event index keyed by provider-neutral event_id."""

    __tablename__ = "domain_envelope_history"

    envelope_id: Mapped[str] = mapped_column(
        String(),
        _fk("domain_envelopes.envelope_id"),
        primary_key=True,
    )
    event_id: Mapped[str] = mapped_column(String(), primary_key=True)
    envelope_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    event_index: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[HistoryEventKind] = mapped_column(
        _enum_type(HistoryEventKind),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[HistoryActorType] = mapped_column(
        _enum_type(HistoryActorType),
        nullable=False,
    )
    actor_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    field_path: Mapped[str | None] = mapped_column(String(), nullable=True)
    model_field_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    event_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    envelope: Mapped[DomainEnvelopeModel] = relationship(
        "DomainEnvelopeModel",
        back_populates="history_events",
    )

    __table_args__ = (
        CheckConstraint("envelope_revision >= 1", name="ck_domain_envelope_history_revision"),
        CheckConstraint("event_index >= 0", name="ck_domain_envelope_history_index"),
        Index("ix_domain_envelope_history_time", "envelope_id", text("occurred_at DESC")),
    )


class DomainEnvelopeProjectionIndex(Base):
    """Regenerated materialized projection index for workspace/export surfaces."""

    __tablename__ = "domain_envelope_projection_index"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    envelope_id: Mapped[str] = mapped_column(
        String(),
        _fk("domain_envelopes.envelope_id"),
        nullable=False,
    )
    object_id: Mapped[str] = mapped_column(String(), nullable=False)
    envelope_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    object_type: Mapped[str | None] = mapped_column(String(), nullable=True)
    projection_type: Mapped[str] = mapped_column(String(), nullable=False)
    projection_key: Mapped[str] = mapped_column(String(), nullable=False)
    projection_status: Mapped[str | None] = mapped_column(String(), nullable=True)
    schema_provider: Mapped[str | None] = mapped_column(String(), nullable=True)
    schema_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    object_model_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    model_field_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    projection_json: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSONB, nullable=False)
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

    envelope: Mapped[DomainEnvelopeModel] = relationship(
        "DomainEnvelopeModel",
        back_populates="projection_index",
    )

    __table_args__ = (
        CheckConstraint("envelope_revision >= 1", name="ck_domain_projection_index_revision"),
        UniqueConstraint(
            "envelope_id",
            "object_id",
            "projection_type",
            "projection_key",
            name="uq_domain_projection_index_key",
        ),
        Index(
            "ix_domain_projection_index_lookup",
            "envelope_id",
            "object_id",
            "envelope_revision",
            "projection_type",
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
    conversation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_result_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        _fk("extraction_results.id"),
        nullable=True,
    )
    envelope_id: Mapped[str | None] = mapped_column(
        String(),
        nullable=True,
    )
    object_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    envelope_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    domain_envelope: Mapped[DomainEnvelopeModel | None] = relationship(
        "DomainEnvelopeModel",
        primaryjoin=lambda: foreign(CurationCandidate.envelope_id)
        == DomainEnvelopeModel.envelope_id,
        viewonly=True,
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
        ForeignKeyConstraint(
            ["envelope_id", "session_id"],
            ["domain_envelopes.envelope_id", "domain_envelopes.session_id"],
            name="fk_curation_candidates_envelope_session_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint('"order" >= 0', name="ck_curation_candidates_order"),
        CheckConstraint(
            "(envelope_id IS NULL AND object_id IS NULL AND envelope_revision IS NULL) "
            "OR (envelope_id IS NOT NULL AND object_id IS NOT NULL "
            "AND envelope_revision IS NOT NULL AND envelope_revision >= 1)",
            name="ck_curation_candidates_domain_projection_ref",
        ),
        Index("ix_curation_candidates_session", "session_id", "order"),
        Index("ix_curation_candidates_status", "session_id", "status"),
        Index(
            "ix_curation_candidates_domain_projection",
            "envelope_id",
            "object_id",
            "envelope_revision",
            postgresql_where=text("envelope_id IS NOT NULL"),
        ),
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
    envelope_id: Mapped[str | None] = mapped_column(
        String(),
        _fk("domain_envelopes.envelope_id"),
        nullable=True,
    )
    envelope_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
        CheckConstraint(
            "(envelope_id IS NULL AND envelope_revision IS NULL) "
            "OR (envelope_id IS NOT NULL AND envelope_revision IS NOT NULL "
            "AND envelope_revision >= 1)",
            name="ck_validation_snapshots_envelope_revision",
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
    idempotency_key: Mapped[str | None] = mapped_column(String(), nullable=True)
    attempt_state: Mapped[CurationSubmissionAttemptState | None] = mapped_column(
        _enum_type(CurationSubmissionAttemptState),
        nullable=True,
    )
    attempt_state_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=JSONB_EMPTY_ARRAY,
    )
    retention_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
    submission_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=JSONB_EMPTY_OBJECT,
    )
    target_result_history: Mapped[list[dict[str, Any]]] = mapped_column(
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
        UniqueConstraint("idempotency_key", name="uq_curation_submissions_idempotency_key"),
        Index("ix_submissions_session", "session_id", text("requested_at DESC")),
        Index("ix_submissions_retention", "retention_expires_at"),
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
    "DomainEnvelopeHistory",
    "DomainEnvelopeModel",
    "DomainEnvelopeObject",
    "DomainEnvelopeProjectionIndex",
    "DomainValidationFinding",
]
