"""Immutable executable revisions reference canonical agents, never a second identity."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .database import Base


class AgentExecutionRevision(Base):
    __tablename__ = "agent_execution_revisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    revision = Column(Integer, nullable=False)
    creator_id = Column(
        Integer, ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    fingerprint = Column(String(71), nullable=False)
    snapshot = Column(JSONB, nullable=False)
    notes = Column(Text, nullable=True)
    output_state = Column(String(30), nullable=False)
    output_mode = Column(String(30), nullable=True)
    output_schema_key = Column(String(100), nullable=True)
    profile_revision_id = Column(UUID(as_uuid=True), nullable=True)
    profile_fingerprint = Column(String(71), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("agent_id", "revision", name="uq_agent_execution_revision"),
        UniqueConstraint("agent_id", "id", name="uq_agent_execution_revision_owner"),
        CheckConstraint("revision > 0", name="ck_agent_execution_revision_positive"),
        CheckConstraint(
            "fingerprint ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_agent_execution_fingerprint",
        ),
        CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'",
            name="ck_agent_execution_snapshot_object",
        ),
        CheckConstraint(
            "(output_state = 'none' AND output_mode IS NULL AND output_schema_key IS NULL AND profile_revision_id IS NULL AND profile_fingerprint IS NULL) OR "
            "(output_state = 'structured_extraction' AND output_mode IS NOT NULL AND ("
            "(output_mode = 'domain' AND output_schema_key IS NOT NULL AND length(trim(output_schema_key)) > 0 AND profile_revision_id IS NULL AND profile_fingerprint IS NULL) OR "
            "(output_mode = 'profile_bound_generic' AND output_schema_key IS NULL AND profile_revision_id IS NOT NULL AND profile_fingerprint IS NOT NULL) OR "
            "(output_mode = 'unprofiled_generic' AND output_schema_key IS NULL AND profile_revision_id IS NULL AND profile_fingerprint IS NULL)))",
            name="ck_agent_execution_output_contract",
        ),
        CheckConstraint(
            "(snapshot #>> '{output_contract,output_state}') IS NOT DISTINCT FROM output_state AND "
            "(snapshot #>> '{output_contract,output_mode}') IS NOT DISTINCT FROM output_mode AND "
            "(snapshot #>> '{output_contract,output_schema_key}') IS NOT DISTINCT FROM output_schema_key AND "
            "(snapshot #>> '{output_contract,generic_profile_ref,profile_revision_id}') IS NOT DISTINCT FROM profile_revision_id::text AND "
            "(snapshot #>> '{output_contract,generic_profile_ref,fingerprint}') IS NOT DISTINCT FROM profile_fingerprint",
            name="ck_agent_execution_snapshot_output_identity",
        ),
        ForeignKeyConstraint(
            ["profile_revision_id", "profile_fingerprint"],
            [
                "generic_extraction_profile_revisions.id",
                "generic_extraction_profile_revisions.fingerprint",
            ],
            name="fk_agent_execution_profile_identity",
            ondelete="RESTRICT",
            match="FULL",
        ),
        Index("ix_agent_execution_profile_revision", "profile_revision_id"),
    )
