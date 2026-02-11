"""Custom agent models for Prompt Workshop."""

import uuid

from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .database import Base


class CustomAgent(Base):
    """User-owned custom agent that replaces a parent system prompt."""

    __tablename__ = "custom_agents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)

    parent_agent_key = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    custom_prompt = Column(Text, nullable=False)
    mod_prompt_overrides = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    icon = Column(String(10), nullable=False, default="\U0001F527")
    include_mod_rules = Column(Boolean, nullable=False, default=True, server_default="true")
    parent_prompt_hash = Column(String(64), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "uq_custom_agents_active",
            "user_id",
            "name",
            unique=True,
            postgresql_where=(is_active == True),
        ),
    )

    def __repr__(self) -> str:
        return f"<CustomAgent id={self.id} user_id={self.user_id} name='{self.name}' active={self.is_active}>"


class CustomAgentVersion(Base):
    """Version snapshots of custom agent prompt content."""

    __tablename__ = "custom_agent_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    custom_agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("custom_agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    custom_prompt = Column(Text, nullable=False)
    mod_prompt_overrides = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("uq_custom_agent_versions_version", "custom_agent_id", "version", unique=True),
    )

    def __repr__(self) -> str:
        return f"<CustomAgentVersion custom_agent_id={self.custom_agent_id} v={self.version}>"
