"""Tool policy SQL model for Agent Workshop tool library."""

from sqlalchemy import Boolean, Column, DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB

from .database import Base


class ToolPolicy(Base):
    """Runtime policy controls for curator-visible tools."""

    __tablename__ = "tool_policies"

    tool_key = Column(String(100), primary_key=True, nullable=False)
    display_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="", server_default="")
    category = Column(String(100), nullable=False, default="General", server_default="General")
    curator_visible = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    allow_attach = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    allow_execute = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    config = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<ToolPolicy tool_key='{self.tool_key}' visible={self.curator_visible}>"

