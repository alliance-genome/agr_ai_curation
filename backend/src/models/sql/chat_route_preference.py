"""Persisted per-user routing intent for ordinary chat."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class ChatRoutePreference(Base):
    """One mutually exclusive automatic, agent, or flow choice per user."""

    __tablename__ = "chat_route_preferences"

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    agent_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("agents.id"),
        nullable=True,
    )
    flow_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("curation_flows.id"),
        nullable=True,
    )
    target_public_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "(mode = 'automatic' AND agent_id IS NULL AND flow_id IS NULL "
            "AND target_public_id IS NULL AND target_display_name IS NULL) OR "
            "(mode = 'agent' AND agent_id IS NOT NULL AND flow_id IS NULL "
            "AND target_public_id IS NOT NULL AND target_display_name IS NOT NULL) OR "
            "(mode = 'flow' AND agent_id IS NULL AND flow_id IS NOT NULL "
            "AND target_public_id IS NOT NULL AND target_display_name IS NOT NULL)",
            name="ck_chat_route_preference_mode_target",
        ),
    )
