"""Tool idea request SQL model for Agent Workshop ideation flow."""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, CheckConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .database import Base


class ToolIdeaRequest(Base):
    """Curator-submitted request for a new tool capability."""

    __tablename__ = "tool_idea_requests"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    opus_conversation = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    status = Column(
        String(50),
        nullable=False,
        default="submitted",
        server_default="submitted",
    )
    developer_notes = Column(Text, nullable=True)
    resulting_tool_key = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('submitted', 'reviewed', 'in_progress', 'completed', 'declined')",
            name="ck_tool_idea_requests_status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ToolIdeaRequest id={self.id} user_id={self.user_id} "
            f"status='{self.status}' title='{self.title}'>"
        )
