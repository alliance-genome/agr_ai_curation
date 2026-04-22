"""SQLAlchemy model for durable chat session metadata."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.sql.database import Base


class ChatSession(Base):
    """Durable chat session state keyed by the external session identifier."""

    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_auth_sub: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_kind: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    generated_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active_document_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("pdf_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        nullable=False,
        server_default=text("to_tsvector('english', '')"),
    )

    __table_args__ = (
        CheckConstraint("btrim(session_id) <> ''", name="ck_chat_sessions_session_id_not_empty"),
        CheckConstraint("btrim(user_auth_sub) <> ''", name="ck_chat_sessions_user_auth_sub_not_empty"),
        CheckConstraint(
            "chat_kind IN ('assistant_chat', 'agent_studio')",
            name="ck_chat_sessions_chat_kind",
        ),
        CheckConstraint(
            "title IS NULL OR btrim(title) <> ''",
            name="ck_chat_sessions_title_not_empty",
        ),
        CheckConstraint(
            "generated_title IS NULL OR btrim(generated_title) <> ''",
            name="ck_chat_sessions_generated_title_not_empty",
        ),
        Index(
            "ix_chat_sessions_user_auth_sub",
            "user_auth_sub",
            "chat_kind",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_chat_sessions_recent_activity",
            "user_auth_sub",
            "chat_kind",
            text("(COALESCE(last_message_at, created_at)) DESC"),
            text("session_id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_chat_sessions_active_document_id",
            "active_document_id",
            postgresql_where=text("active_document_id IS NOT NULL"),
        ),
        Index(
            "ix_chat_sessions_search_vector_assistant_chat",
            "search_vector",
            postgresql_using="gin",
            postgresql_where=text(
                "deleted_at IS NULL AND chat_kind = 'assistant_chat'"
            ),
        ),
        Index(
            "ix_chat_sessions_search_vector_agent_studio",
            "search_vector",
            postgresql_using="gin",
            postgresql_where=text(
                "deleted_at IS NULL AND chat_kind = 'agent_studio'"
            ),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<ChatSession(session_id='{self.session_id}', "
            f"user_auth_sub='{self.user_auth_sub}', "
            f"chat_kind='{self.chat_kind}', "
            f"deleted_at={self.deleted_at})>"
        )
