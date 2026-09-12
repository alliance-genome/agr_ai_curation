"""Immutable audit receipts for package capabilities referenced by profiles."""

from sqlalchemy import Column, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .database import Base


class ProfileValidatorCapability(Base):
    __tablename__ = "profile_validator_capabilities"
    fingerprint = Column(String(71), primary_key=True)
    package_id = Column(Text, nullable=False)
    package_version = Column(Text, nullable=False)
    domain_pack_id = Column(Text, nullable=False)
    domain_pack_version = Column(Text, nullable=False)
    binding_id = Column(Text, nullable=False)
    snapshot = Column(JSONB, nullable=False)
    __table_args__ = (UniqueConstraint("package_id", "package_version", "domain_pack_id",
                                     "domain_pack_version", "binding_id", name="uq_profile_capability_version"),)


class ProfileValidatorCapabilityReference(Base):
    __tablename__ = "profile_validator_capability_references"
    profile_revision_id = Column(UUID(as_uuid=True), ForeignKey(
        "generic_extraction_profile_revisions.id", ondelete="RESTRICT"), primary_key=True)
    mapping_id = Column(Text, primary_key=True)
    capability_fingerprint = Column(String(71), ForeignKey(
        "profile_validator_capabilities.fingerprint", ondelete="RESTRICT"), nullable=False)
