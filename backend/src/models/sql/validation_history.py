"""Immutable validation facts; heads are disposable, version-checked indexes."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class ValidationArtifact(Base):
    __tablename__ = "validation_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    document_id: Mapped[str | None] = mapped_column(String, nullable=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    producer_id: Mapped[str] = mapped_column(String, nullable=False)
    subject_kind: Mapped[str] = mapped_column(String, nullable=False)
    baseline_sha: Mapped[str] = mapped_column(
        ForeignKey("validation_bodies.sha256"), nullable=False
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "session_id", "producer_id", name="uq_validation_producer"
        ),
    )


class ValidationBody(Base):
    __tablename__ = "validation_bodies"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ValidationFact(Base):
    """One immutable contribution, scoped to an artifact (never a mutable result)."""

    __tablename__ = "validation_facts"
    sequence: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("validation_artifacts.id"), nullable=False
    )
    id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(36))
    parent_id: Mapped[str | None] = mapped_column(String(36))
    body_sha: Mapped[str] = mapped_column(
        ForeignKey("validation_bodies.sha256"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint("artifact_id", "id", name="uq_validation_fact_id"),
        UniqueConstraint(
            "artifact_id", "operation_id", name="uq_validation_fact_operation"
        ),
        ForeignKeyConstraint(
            ["artifact_id", "parent_id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
        ForeignKeyConstraint(
            ["artifact_id", "target_id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
        Index("ix_validation_history_target", "artifact_id", "target_id", "sequence"),
        Index("ix_validation_history_sequence", "artifact_id", "sequence"),
    )


class ValidationHead(Base):
    __tablename__ = "validation_heads"
    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["artifact_id", "target_id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
        ForeignKeyConstraint(
            ["artifact_id", "fact_id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
    )


class ValidationCheckpoint(Base):
    """Exact projection at a revision; no cascade can erase historical content."""

    __tablename__ = "validation_checkpoints"
    envelope_id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, nullable=False)
    source_extraction_result_id: Mapped[str | None] = mapped_column(String)
    body_sha: Mapped[str] = mapped_column(
        ForeignKey("validation_bodies.sha256"), nullable=False
    )
    provenance: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
