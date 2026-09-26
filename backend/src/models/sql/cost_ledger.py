"""Shared accounting identities, independent of scientific result retention.

Usage and valuation ownership are introduced by a coordinated consumer cutover;
these tables alone do not constitute an authoritative spend report.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class CostAttempt(Base):
    __tablename__ = "cost_attempts"

    deployment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True)
    owner_subject: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CostSourceReference(Base):
    __tablename__ = "cost_source_references"

    deployment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_system: Mapped[str] = mapped_column(Text, primary_key=True)
    source_namespace: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    attempt_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Neither table has a foreign key to benchmark jobs, sessions or artifacts:
    # deleting scientific execution data must not delete accounting provenance.
    __table_args__ = (
        UniqueConstraint(
            "deployment_id", "source_system", "source_namespace", "source_id", "attempt_id",
            name="uq_cost_source_bound_attempt",
        ),
        ForeignKeyConstraint(
            ["deployment_id", "attempt_id"],
            ["cost_attempts.deployment_id", "cost_attempts.id"],
            name="fk_cost_source_attempt", ondelete="RESTRICT",
        ),
    )


class CostFactRevision(Base):
    """Append-only additions of previously unknown facts, not copied snapshots."""

    __tablename__ = "cost_fact_revisions"

    deployment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    attempt_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_system: Mapped[str] = mapped_column(Text, nullable=False)
    source_namespace: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_read_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_write_tokens: Mapped[int | None] = mapped_column(BigInteger)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger)
    billed_amount: Mapped[Decimal | None] = mapped_column(Numeric())
    billed_unit: Mapped[str | None] = mapped_column(Text)
    billed_source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["deployment_id", "source_system", "source_namespace", "source_id", "attempt_id"],
            ["cost_source_references.deployment_id", "cost_source_references.source_system",
             "cost_source_references.source_namespace", "cost_source_references.source_id",
             "cost_source_references.attempt_id"],
            name="fk_cost_revision_source_attempt", ondelete="RESTRICT",
        ),
        CheckConstraint("revision >= 1", name="ck_cost_revision_positive"),
        CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) AND "
            "(output_tokens IS NULL OR output_tokens >= 0) AND "
            "(total_tokens IS NULL OR total_tokens >= 0) AND "
            "(cache_read_tokens IS NULL OR cache_read_tokens >= 0) AND "
            "(cache_write_tokens IS NULL OR cache_write_tokens >= 0) AND "
            "(reasoning_tokens IS NULL OR reasoning_tokens >= 0)",
            name="ck_cost_revision_tokens",
        ),
        CheckConstraint(
            "(billed_amount IS NULL AND billed_unit IS NULL AND billed_source IS NULL) OR "
            "(billed_amount IS NOT NULL AND billed_amount >= 0 AND "
            "billed_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric) AND "
            "billed_unit IS NOT NULL AND length(trim(billed_unit)) > 0 AND "
            "billed_source IS NOT NULL AND length(trim(billed_source)) > 0)",
            name="ck_cost_revision_charge",
        ),
        CheckConstraint(
            "num_nonnulls(input_tokens, output_tokens, total_tokens, cache_read_tokens, "
            "cache_write_tokens, reasoning_tokens, billed_amount) > 0",
            name="ck_cost_revision_nonempty",
        ),
    )
