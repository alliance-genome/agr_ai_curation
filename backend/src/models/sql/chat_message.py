"""SQLAlchemy model for durable chat transcript rows."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.sql.database import Base


class ChatMessage(Base):
    """Durable transcript row stored for chat history and resume flows."""

    __tablename__ = "chat_messages"

    message_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_kind: Mapped[str] = mapped_column(String, nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    message_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="text",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        nullable=False,
        server_default=text("to_tsvector('english', '')"),
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'flow')",
            name="ck_chat_messages_role",
        ),
        CheckConstraint(
            "btrim(session_id) <> ''",
            name="ck_chat_messages_session_id_not_empty",
        ),
        CheckConstraint(
            "chat_kind IN ('assistant_chat', 'agent_studio')",
            name="ck_chat_messages_chat_kind",
        ),
        CheckConstraint(
            "turn_id IS NULL OR btrim(turn_id) <> ''",
            name="ck_chat_messages_turn_id_not_empty",
        ),
        CheckConstraint(
            "btrim(message_type) <> ''",
            name="ck_chat_messages_message_type_not_empty",
        ),
        CheckConstraint(
            "btrim(content) <> ''",
            name="ck_chat_messages_content_not_empty",
        ),
        Index(
            "ix_chat_messages_session_timeline",
            "session_id",
            "chat_kind",
            "created_at",
            "message_id",
        ),
        Index(
            "ix_chat_messages_turn_lookup",
            "session_id",
            "chat_kind",
            "turn_id",
            postgresql_where=text("turn_id IS NOT NULL"),
        ),
        Index(
            "uq_chat_messages_user_turn",
            "session_id",
            "chat_kind",
            "turn_id",
            unique=True,
            postgresql_where=text("turn_id IS NOT NULL AND role = 'user'"),
        ),
        Index(
            "uq_chat_messages_assistant_turn",
            "session_id",
            "chat_kind",
            "turn_id",
            unique=True,
            postgresql_where=text("turn_id IS NOT NULL AND role = 'assistant'"),
        ),
        Index(
            "ix_chat_messages_search_vector_assistant_chat",
            "search_vector",
            postgresql_using="gin",
            postgresql_where=text("chat_kind = 'assistant_chat'"),
        ),
        Index(
            "ix_chat_messages_search_vector_agent_studio",
            "search_vector",
            postgresql_using="gin",
            postgresql_where=text("chat_kind = 'agent_studio'"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<ChatMessage(message_id={self.message_id}, "
            f"session_id='{self.session_id}', chat_kind='{self.chat_kind}', "
            f"role='{self.role}', "
            f"message_type='{self.message_type}')>"
        )
