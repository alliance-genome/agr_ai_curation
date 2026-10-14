"""Immutable, owner-scoped agent history; payload bytes deduplicate independently."""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class AgentHistoryPayload(Base):
    __tablename__ = "agent_history_payloads"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class AgentHistoryEvent(Base):
    __tablename__ = "agent_history_events"
    sequence: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    invocation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    parent_invocation_id: Mapped[str | None] = mapped_column(String(36))
    lineage: Mapped[str] = mapped_column(Text, nullable=False)
    agent_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(Text)
    call_id: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(ForeignKey("agent_history_payloads.sha256"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("ix_agent_history_owner_run_sequence", "owner_id", "run_id", "sequence"),
        Index("ix_agent_history_owner_session_sequence", "owner_id", "session_id", "sequence"),
        Index("ix_agent_history_invocation_sequence", "invocation_id", "sequence"),
    )
