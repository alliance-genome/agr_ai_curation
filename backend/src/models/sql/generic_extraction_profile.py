"""Reusable profile heads and immutable closed-contract revisions."""

import uuid

from sqlalchemy import (
    Boolean,
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .database import Base


class GenericExtractionProfile(Base):
    __tablename__ = "generic_extraction_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(
        Integer, ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=True,
    )
    visibility = Column(String(20), nullable=False, default="private")
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default="")
    semantic_class = Column(Text, nullable=False)
    head_revision = Column(Integer, nullable=False, default=1)
    archived = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "(visibility = 'private' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_generic_profile_visibility",
        ),
        CheckConstraint("head_revision > 0", name="ck_generic_profile_head_positive"),
        ForeignKeyConstraint(
            ["id", "head_revision"],
            [
                "generic_extraction_profile_revisions.profile_id",
                "generic_extraction_profile_revisions.revision",
            ],
            name="fk_generic_profile_head",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
            ondelete="NO ACTION",
        ),
        Index("ix_generic_profiles_owner_archive", "owner_id", "archived", "id"),
        Index("ix_generic_profiles_project_archive", "project_id", "archived", "id"),
    )


class GenericExtractionProfileRevision(Base):
    __tablename__ = "generic_extraction_profile_revisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("generic_extraction_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision = Column(Integer, nullable=False)
    fingerprint = Column(String(71), nullable=False)
    contract = Column(JSONB, nullable=False)
    creator_id = Column(
        Integer, ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("profile_id", "revision", name="uq_generic_profile_revision"),
        UniqueConstraint(
            "id", "fingerprint", name="uq_generic_profile_revision_identity"
        ),
        CheckConstraint("revision > 0", name="ck_generic_profile_revision_positive"),
        CheckConstraint(
            "fingerprint ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_generic_profile_fingerprint",
        ),
        CheckConstraint(
            "jsonb_typeof(contract) = 'object'",
            name="ck_generic_profile_contract_object",
        ),
        Index("ix_generic_profile_revision_fingerprint", "profile_id", "fingerprint"),
    )
