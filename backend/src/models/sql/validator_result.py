"""Durable standalone validator outputs; no artificial extraction envelope."""
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class ValidatorResultRecord(Base):
    __tablename__ = "validator_result_records"

    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    origin_session_id: Mapped[str] = mapped_column(String, nullable=False)
    invocation_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_key: Mapped[str] = mapped_column(String, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "origin_session_id", "invocation_id", name="uq_validator_result_invocation"),
        Index("ix_validator_result_owner_session", "user_id", "origin_session_id"),
    )
