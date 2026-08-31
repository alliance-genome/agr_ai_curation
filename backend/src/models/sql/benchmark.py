"""SQLAlchemy models for durable benchmark execution state."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class BenchmarkJobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BenchmarkCellStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BenchmarkInvocationStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _status_enum(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda values: [value.value for value in values],
        native_enum=False,
        create_constraint=True,
        name=name,
    )


class BenchmarkJob(Base):
    """One immutable-on-completion benchmark plan and its execution counters."""

    __tablename__ = "benchmark_jobs"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[BenchmarkJobStatus] = mapped_column(
        _status_enum(BenchmarkJobStatus, "ck_benchmark_jobs_status_values"),
        nullable=False,
        default=BenchmarkJobStatus.QUEUED,
    )
    suite_id: Mapped[str] = mapped_column(String(128), nullable=False)
    suite_specification: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    resolved_plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    suite_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    catalog_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    inputs_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    rerun_of_job_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True), nullable=True
    )
    total_cells: Mapped[int] = mapped_column(Integer, nullable=False)
    queued_cells: Mapped[int] = mapped_column(Integer, nullable=False)
    running_cells: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_cells: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_cells: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_cells: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cells: Mapped[list["BenchmarkCell"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )
    events: Mapped[list["BenchmarkEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("id", "owner_subject", name="uq_benchmark_jobs_id_owner"),
        ForeignKeyConstraint(
            ["rerun_of_job_id", "owner_subject"],
            ["benchmark_jobs.id", "benchmark_jobs.owner_subject"],
            name="fk_benchmark_jobs_rerun_same_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("char_length(owner_subject) > 0", name="ck_benchmark_jobs_owner"),
        CheckConstraint(
            "jsonb_typeof(suite_specification) = 'object'",
            name="ck_benchmark_jobs_suite_object",
        ),
        CheckConstraint(
            "jsonb_typeof(resolved_plan) = 'object'",
            name="ck_benchmark_jobs_plan_object",
        ),
        CheckConstraint(
            "total_cells > 0 AND queued_cells >= 0 AND running_cells >= 0 "
            "AND succeeded_cells >= 0 AND failed_cells >= 0 AND cancelled_cells >= 0 "
            "AND queued_cells + running_cells + succeeded_cells + failed_cells + "
            "cancelled_cells = total_cells",
            name="ck_benchmark_jobs_counters",
        ),
        CheckConstraint(
            "rerun_of_job_id IS NULL OR rerun_of_job_id <> id",
            name="ck_benchmark_jobs_not_self_rerun",
        ),
        Index(
            "ix_benchmark_jobs_owner_status_created",
            "owner_subject",
            "status",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_benchmark_jobs_queued_claim",
            "created_at",
            "id",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_benchmark_jobs_expired_lease_claim",
            "lease_expires_at",
            "id",
            postgresql_where=text("status IN ('running', 'cancel_requested')"),
        ),
    )


class BenchmarkCell(Base):
    """A relational projection of one frozen plan cell and its result."""

    __tablename__ = "benchmark_cells"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    job_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("benchmark_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    cell_key: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    configuration_id: Mapped[str] = mapped_column(String(128), nullable=False)
    repetition: Mapped[int] = mapped_column(Integer, nullable=False)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    routes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_resolver: Mapped[str] = mapped_column(String(128), nullable=False)
    input_reference: Mapped[str] = mapped_column(String(1024), nullable=False)
    input_version: Mapped[str] = mapped_column(String(255), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[BenchmarkCellStatus] = mapped_column(
        _status_enum(BenchmarkCellStatus, "ck_benchmark_cells_status_values"),
        nullable=False,
        default=BenchmarkCellStatus.QUEUED,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_cell_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    source_job_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_envelope: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    envelope_size_bytes: Mapped[int | None] = mapped_column(Integer)
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    job: Mapped[BenchmarkJob] = relationship(back_populates="cells")
    invocations: Mapped[list["BenchmarkInvocation"]] = relationship(
        back_populates="cell", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("id", "job_id", name="uq_benchmark_cells_id_job"),
        UniqueConstraint("job_id", "cell_key", name="uq_benchmark_cells_job_key"),
        UniqueConstraint("job_id", "position", name="uq_benchmark_cells_job_position"),
        ForeignKeyConstraint(
            ["source_cell_id", "source_job_id"],
            ["benchmark_cells.id", "benchmark_cells.job_id"],
            name="fk_benchmark_cells_source",
            ondelete="RESTRICT",
        ),
        CheckConstraint("position >= 0 AND repetition >= 1", name="ck_benchmark_cells_position"),
        CheckConstraint("attempt_count >= 0", name="ck_benchmark_cells_attempt_count"),
        CheckConstraint("target_kind IN ('agent', 'flow')", name="ck_benchmark_cells_target_kind"),
        CheckConstraint("jsonb_typeof(routes) = 'object'", name="ck_benchmark_cells_routes_object"),
        CheckConstraint(
            "(source_cell_id IS NULL) = (source_job_id IS NULL)",
            name="ck_benchmark_cells_source_pair",
        ),
        CheckConstraint(
            "(generated_envelope IS NULL AND envelope_size_bytes IS NULL) OR "
            "(generated_envelope IS NOT NULL AND jsonb_typeof(generated_envelope) = 'object' "
            "AND envelope_size_bytes > 0)",
            name="ck_benchmark_cells_envelope_pair",
        ),
        CheckConstraint(
            "failure IS NULL OR jsonb_typeof(failure) = 'object'",
            name="ck_benchmark_cells_failure_object",
        ),
        Index("ix_benchmark_cells_job_page", "job_id", "position", "id"),
        Index(
            "ix_benchmark_cells_queued_claim",
            "job_id",
            "position",
            "id",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_benchmark_cells_expired_lease_claim",
            "job_id",
            "lease_expires_at",
            "id",
            postgresql_where=text("status = 'running'"),
        ),
    )


class BenchmarkInvocation(Base):
    """One ordered target invocation belonging to a benchmark cell."""

    __tablename__ = "benchmark_invocations"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    cell_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("benchmark_cells.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    route_slot: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    response_digest: Mapped[str | None] = mapped_column(String(71))
    status: Mapped[BenchmarkInvocationStatus] = mapped_column(
        _status_enum(
            BenchmarkInvocationStatus, "ck_benchmark_invocations_status_values"
        ),
        nullable=False,
    )
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cell: Mapped[BenchmarkCell] = relationship(back_populates="invocations")

    __table_args__ = (
        UniqueConstraint("cell_id", "ordinal", name="uq_benchmark_invocations_cell_ordinal"),
        CheckConstraint("ordinal >= 0 AND attempt >= 1", name="ck_benchmark_invocations_order"),
        CheckConstraint(
            "failure IS NULL OR jsonb_typeof(failure) = 'object'",
            name="ck_benchmark_invocations_failure_object",
        ),
        Index("ix_benchmark_invocations_cell_order", "cell_id", "ordinal", "id"),
    )


class BenchmarkEvent(Base):
    """Replayable event in a job-local total order."""

    __tablename__ = "benchmark_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("benchmark_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    job: Mapped[BenchmarkJob] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_benchmark_events_job_sequence"),
        CheckConstraint("sequence >= 1", name="ck_benchmark_events_sequence"),
        CheckConstraint("char_length(event_type) > 0", name="ck_benchmark_events_type"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_benchmark_events_payload_object"),
        Index("ix_benchmark_events_replay", "job_id", "sequence", "id"),
    )
