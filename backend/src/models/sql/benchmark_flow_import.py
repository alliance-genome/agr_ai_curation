"""Which AI Curation flow version each benchmark flow copy came from (append-only).

Written only in the benchmark resolver, one row per import. Main AI Curation never
writes it. Past runs keep the flow version they ran; this record says which AI
Curation version that was and which immutable revisions it pinned.
"""

import uuid

from sqlalchemy import (CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .database import Base


class BenchmarkFlowImport(Base):
    __tablename__ = "benchmark_flow_imports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    export_issuer = Column(Text, nullable=False)
    source_flow_id = Column(UUID(as_uuid=True), nullable=False)
    source_version = Column(String(71), nullable=False)
    flow_id = Column(UUID(as_uuid=True), ForeignKey("curation_flows.id", ondelete="RESTRICT"),
                     nullable=False)
    version = Column(Integer, nullable=False)
    flow_revision = Column(String(71), nullable=False)
    pins = Column(JSONB, nullable=False)
    bundle_sha256 = Column(String(71), nullable=False)
    source_app_version = Column(Text, nullable=False)
    imported_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "export_issuer", "source_flow_id", "version",
                         name="uq_benchmark_flow_import_version"),
        CheckConstraint("version > 0", name="ck_benchmark_flow_import_version_positive"),
        CheckConstraint(
            "source_version ~ '^sha256:[a-f0-9]{64}$' AND flow_revision ~ '^sha256:[a-f0-9]{64}$' "
            "AND bundle_sha256 ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_benchmark_flow_import_digests",
        ),
        CheckConstraint("jsonb_typeof(pins) = 'array'", name="ck_benchmark_flow_import_pins_array"),
        Index("ix_benchmark_flow_imports_flow", "flow_id"),
    )
