"""Custom-agent compatibility models.

`CustomAgent` now aliases the unified `agents` table model to keep
legacy imports working during migration cleanup.
"""

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import synonym

from .agent import Agent
from .database import Base

CustomAgent = Agent

__all__ = ["CustomAgent", "CustomAgentVersion"]


class CustomAgentVersion(Base):
    """Version snapshots of custom agent prompt content."""

    __tablename__ = "custom_agent_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    custom_agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    custom_prompt = Column(Text, nullable=False)
    group_prompt_overrides = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    mod_prompt_overrides = synonym("group_prompt_overrides")
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "uq_custom_agent_versions_version",
            "custom_agent_id",
            "version",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<CustomAgentVersion custom_agent_id={self.custom_agent_id} "
            f"v={self.version}>"
        )
