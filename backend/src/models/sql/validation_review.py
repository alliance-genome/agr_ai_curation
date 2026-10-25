"""Durable admission and recoverable jobs; immutable audit lives in validation_facts."""

from datetime import datetime
from typing import Any
from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class ValidationReviewScope(Base):
    __tablename__ = "validation_review_scopes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    operation_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("validation_review_scopes.id")
    )
    contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    charged: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    __table_args__ = (
        UniqueConstraint("owner_id", "operation_id", name="uq_review_scope_operation"),
    )


class ValidationReviewEpisode(Base):
    __tablename__ = "validation_review_episodes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scope_id: Mapped[str] = mapped_column(
        ForeignKey("validation_review_scopes.id"), nullable=False
    )
    predecessor_id: Mapped[str | None] = mapped_column(
        ForeignKey("validation_review_episodes.id")
    )
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    allowances: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    charged: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["artifact_id", "id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
    )


class ValidationReviewReservation(Base):
    __tablename__ = "validation_review_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("validation_review_episodes.id"), nullable=False
    )
    operation_id: Mapped[str] = mapped_column(String, nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    charges: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    actual: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint(
            "episode_id", "operation_id", name="uq_review_reservation_operation"
        ),
    )


class ValidationReviewWork(Base):
    __tablename__ = "validation_review_work"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("validation_review_episodes.id"), nullable=False
    )
    operation_id: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="validator")
    state: Mapped[str] = mapped_column(String, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint("episode_id", "operation_id", name="uq_review_work_operation"),
    )


class ValidationReviewCoverage(Base):
    """Append-only, server-derived coverage credits; never replenishes retries."""

    __tablename__ = "validation_review_coverage"
    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    scope_id: Mapped[str] = mapped_column(
        ForeignKey("validation_review_scopes.id"), nullable=False, index=True
    )
    targets: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    allowances: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["artifact_id", "episode_id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
    )


class ChatReviewDispatch(Base):
    """Durable extraction replay fence; started work is never silently replayed."""
    __tablename__ = "chat_review_dispatches"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    document_id: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    turn_id: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


class FlowReviewStep(Base):
    """One durable extraction submission and review boundary per configured step."""
    __tablename__ = "flow_review_steps"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    document_id: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    contract: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("validation_artifacts.id"))
    episode_id: Mapped[str | None] = mapped_column(ForeignKey("validation_review_episodes.id"))
    outcome: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


class WorkspaceReviewOperation(Base):
    """An explicit curator command; duplicate delivery never grants another round."""
    __tablename__ = "workspace_review_operations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request: Mapped[dict] = mapped_column(JSONB, nullable=False)
    artifacts: Mapped[list] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("owner_id", "request_id", name="uq_workspace_review_request"),)
