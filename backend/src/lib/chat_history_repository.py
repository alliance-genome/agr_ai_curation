"""Repository helpers for durable chat session and message persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.sql.chat_message import ChatMessage as ChatMessageModel
from src.models.sql.chat_session import ChatSession as ChatSessionModel


MAX_SESSION_PAGE_SIZE = 100
MAX_MESSAGE_PAGE_SIZE = 200
TURN_ID_UNIQUE_CONSTRAINTS = (
    "uq_chat_messages_user_turn",
    "uq_chat_messages_assistant_turn",
)
IDEMPOTENT_TURN_ROLES = {"user", "assistant"}
VALID_CHAT_ROLES = {"user", "assistant", "flow"}


@dataclass(frozen=True)
class ChatSessionCursor:
    """Keyset cursor for descending recent-activity session pagination."""

    recent_activity_at: datetime
    session_id: str


@dataclass(frozen=True)
class ChatMessageCursor:
    """Keyset cursor for ascending chat-message pagination."""

    created_at: datetime
    message_id: UUID


@dataclass(frozen=True)
class ChatSessionRecord:
    """Immutable session payload returned by the repository."""

    session_id: str
    user_auth_sub: str
    title: str | None
    active_document_id: UUID | None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None
    deleted_at: datetime | None

    @property
    def recent_activity_at(self) -> datetime:
        return self.last_message_at or self.created_at


@dataclass(frozen=True)
class ChatMessageRecord:
    """Immutable message payload returned by the repository."""

    message_id: UUID
    session_id: str
    turn_id: str | None
    role: str
    message_type: str
    content: str
    payload_json: dict[str, Any] | list[Any] | None
    trace_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class ChatSessionPage:
    """Paginated session result set."""

    items: list[ChatSessionRecord]
    next_cursor: ChatSessionCursor | None


@dataclass(frozen=True)
class ChatMessagePage:
    """Paginated message result set."""

    items: list[ChatMessageRecord]
    next_cursor: ChatMessageCursor | None


@dataclass(frozen=True)
class ChatSessionDetail:
    """Session detail with the first or next transcript page."""

    session: ChatSessionRecord
    messages: list[ChatMessageRecord]
    next_message_cursor: ChatMessageCursor | None


@dataclass(frozen=True)
class AppendMessageResult:
    """Outcome of a durable transcript append."""

    message: ChatMessageRecord
    created: bool


class ChatHistorySessionNotFoundError(LookupError):
    """Raised when a session is missing, deleted, or not visible to the caller."""


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalize_optional_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    return normalized


def _validate_page_size(limit: int, *, field_name: str, max_value: int) -> int:
    if limit < 1:
        raise ValueError(f"{field_name} must be greater than zero")
    if limit > max_value:
        raise ValueError(f"{field_name} must be less than or equal to {max_value}")
    return limit


def _session_record(session: ChatSessionModel) -> ChatSessionRecord:
    return ChatSessionRecord(
        session_id=session.session_id,
        user_auth_sub=session.user_auth_sub,
        title=session.title,
        active_document_id=session.active_document_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_message_at=session.last_message_at,
        deleted_at=session.deleted_at,
    )


def _message_record(message: ChatMessageModel) -> ChatMessageRecord:
    return ChatMessageRecord(
        message_id=message.message_id,
        session_id=message.session_id,
        turn_id=message.turn_id,
        role=message.role,
        message_type=message.message_type,
        content=message.content,
        payload_json=message.payload_json,
        trace_id=message.trace_id,
        created_at=message.created_at,
    )


def _is_duplicate_turn_integrity_error(error: IntegrityError) -> bool:
    error_text = f"{error}\n{error.orig}"
    return any(constraint_name in error_text for constraint_name in TURN_ID_UNIQUE_CONSTRAINTS)


class ChatHistoryRepository:
    """Single SQL read/write surface for durable chat history tables."""

    def __init__(self, db: Session):
        self._db = db

    def create_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        title: str | None = None,
        active_document_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ChatSessionRecord:
        """Insert one chat session and flush it without committing."""

        session = ChatSessionModel(
            session_id=_normalize_required_text(session_id, field_name="session_id"),
            user_auth_sub=_normalize_required_text(
                user_auth_sub,
                field_name="user_auth_sub",
            ),
            title=_normalize_optional_text(title, field_name="title"),
            active_document_id=active_document_id,
        )
        if created_at is not None:
            session.created_at = created_at
            session.updated_at = created_at

        self._db.add(session)
        self._db.flush()
        self._db.refresh(session)
        return _session_record(session)

    def get_or_create_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        title: str | None = None,
        active_document_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ChatSessionRecord:
        """Return the visible session or insert a new one when absent."""

        session = self._get_active_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
        )
        if session is not None:
            return _session_record(session)

        return self.create_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            title=title,
            active_document_id=active_document_id,
            created_at=created_at,
        )

    def get_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
    ) -> ChatSessionRecord | None:
        """Fetch one active session scoped to the authenticated user."""

        session = self._get_active_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
        )
        if session is None:
            return None

        return _session_record(session)

    def get_session_detail(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        message_limit: int = 100,
        message_cursor: ChatMessageCursor | None = None,
    ) -> ChatSessionDetail | None:
        """Fetch one active session and one chronological page of transcript rows."""

        session = self._get_active_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
        )
        if session is None:
            return None

        message_page = self._list_messages_for_session(
            session_id=session.session_id,
            limit=message_limit,
            cursor=message_cursor,
        )
        return ChatSessionDetail(
            session=_session_record(session),
            messages=message_page.items,
            next_message_cursor=message_page.next_cursor,
        )

    def list_sessions(
        self,
        *,
        user_auth_sub: str,
        limit: int = 20,
        cursor: ChatSessionCursor | None = None,
        active_document_id: UUID | None = None,
    ) -> ChatSessionPage:
        """List active sessions ordered by null-safe recent activity descending."""

        return self._list_sessions(
            user_auth_sub=user_auth_sub,
            limit=limit,
            cursor=cursor,
            search_query=None,
            active_document_id=active_document_id,
        )

    def search_sessions(
        self,
        *,
        user_auth_sub: str,
        query: str,
        limit: int = 20,
        cursor: ChatSessionCursor | None = None,
        active_document_id: UUID | None = None,
    ) -> ChatSessionPage:
        """Search active sessions with Postgres full-text search and recent ordering."""

        normalized_query = _normalize_required_text(query, field_name="query")
        return self._list_sessions(
            user_auth_sub=user_auth_sub,
            limit=limit,
            cursor=cursor,
            search_query=normalized_query,
            active_document_id=active_document_id,
        )

    def count_sessions(
        self,
        *,
        user_auth_sub: str,
        query: str | None = None,
        active_document_id: UUID | None = None,
    ) -> int:
        """Count visible sessions for the authenticated user and optional filters."""

        normalized_user_auth_sub = _normalize_required_text(
            user_auth_sub,
            field_name="user_auth_sub",
        )
        stmt = select(func.count()).select_from(ChatSessionModel).where(
            ChatSessionModel.user_auth_sub == normalized_user_auth_sub,
            ChatSessionModel.deleted_at.is_(None),
        )

        if query is not None:
            normalized_query = _normalize_required_text(query, field_name="query")
            stmt = stmt.where(
                ChatSessionModel.search_vector.op("@@")(
                    func.websearch_to_tsquery("english", normalized_query)
                )
            )

        if active_document_id is not None:
            stmt = stmt.where(
                ChatSessionModel.active_document_id == active_document_id,
            )

        return int(self._db.scalar(stmt) or 0)

    def rename_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        title: str,
    ) -> ChatSessionRecord | None:
        """Rename one visible session and refresh trigger-managed columns."""

        session = self._get_active_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
        )
        if session is None:
            return None

        session.title = _normalize_required_text(title, field_name="title")
        self._db.flush()
        self._db.refresh(session)
        return _session_record(session)

    def soft_delete_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        deleted_at: datetime | None = None,
    ) -> bool:
        """Soft-delete one visible session without deleting transcript rows."""

        session = self._get_active_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
        )
        if session is None:
            return False

        deleted_timestamp = deleted_at or datetime.now(timezone.utc)
        session.deleted_at = deleted_timestamp
        session.updated_at = deleted_timestamp
        self._db.flush()
        return True

    def list_messages(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        limit: int = 100,
        cursor: ChatMessageCursor | None = None,
    ) -> ChatMessagePage:
        """List transcript rows for one visible session in chronological order."""

        self._require_active_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
        )
        return self._list_messages_for_session(
            session_id=_normalize_required_text(session_id, field_name="session_id"),
            limit=limit,
            cursor=cursor,
        )

    def append_message(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        role: str,
        content: str,
        message_type: str = "text",
        turn_id: str | None = None,
        payload_json: dict[str, Any] | list[Any] | None = None,
        trace_id: str | None = None,
        created_at: datetime | None = None,
    ) -> AppendMessageResult:
        """Append one transcript row and reuse an existing row on duplicate turn replay."""

        normalized_role = _normalize_required_text(role, field_name="role")
        if normalized_role not in VALID_CHAT_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_CHAT_ROLES)}")

        session = self._require_active_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
        )
        message = ChatMessageModel(
            session_id=session.session_id,
            turn_id=_normalize_optional_text(turn_id, field_name="turn_id"),
            role=normalized_role,
            message_type=_normalize_required_text(
                message_type,
                field_name="message_type",
            ),
            content=content,
            payload_json=payload_json,
            trace_id=_normalize_optional_text(trace_id, field_name="trace_id"),
        )
        if not content.strip():
            raise ValueError("content is required")
        if created_at is not None:
            message.created_at = created_at

        use_savepoint = (
            message.turn_id is not None and message.role in IDEMPOTENT_TURN_ROLES
        )
        if use_savepoint:
            try:
                with self._db.begin_nested():
                    self._db.add(message)
                    self._db.flush()
            except IntegrityError as error:
                if message in self._db:
                    self._db.expunge(message)
                if not _is_duplicate_turn_integrity_error(error):
                    raise
                existing = self._db.scalar(
                    select(ChatMessageModel).where(
                        ChatMessageModel.session_id == session.session_id,
                        ChatMessageModel.turn_id == message.turn_id,
                        ChatMessageModel.role == message.role,
                    )
                )
                if existing is None:
                    raise
                return AppendMessageResult(
                    message=_message_record(existing),
                    created=False,
                )
        else:
            self._db.add(message)
            self._db.flush()

        self._db.refresh(message)
        self._db.refresh(session)
        return AppendMessageResult(
            message=_message_record(message),
            created=True,
        )

    def _get_active_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
    ) -> ChatSessionModel | None:
        normalized_session_id = _normalize_required_text(
            session_id,
            field_name="session_id",
        )
        normalized_user_auth_sub = _normalize_required_text(
            user_auth_sub,
            field_name="user_auth_sub",
        )
        return self._db.scalar(
            select(ChatSessionModel).where(
                ChatSessionModel.session_id == normalized_session_id,
                ChatSessionModel.user_auth_sub == normalized_user_auth_sub,
                ChatSessionModel.deleted_at.is_(None),
            )
        )

    def _require_active_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
    ) -> ChatSessionModel:
        session = self._get_active_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
        )
        if session is None:
            raise ChatHistorySessionNotFoundError("Chat session not found")
        return session

    def _list_sessions(
        self,
        *,
        user_auth_sub: str,
        limit: int,
        cursor: ChatSessionCursor | None,
        search_query: str | None,
        active_document_id: UUID | None,
    ) -> ChatSessionPage:
        page_size = _validate_page_size(
            limit,
            field_name="limit",
            max_value=MAX_SESSION_PAGE_SIZE,
        )
        normalized_user_auth_sub = _normalize_required_text(
            user_auth_sub,
            field_name="user_auth_sub",
        )
        recent_activity = func.coalesce(
            ChatSessionModel.last_message_at,
            ChatSessionModel.created_at,
        )

        stmt = select(ChatSessionModel).where(
            ChatSessionModel.user_auth_sub == normalized_user_auth_sub,
            ChatSessionModel.deleted_at.is_(None),
        )

        if active_document_id is not None:
            stmt = stmt.where(
                ChatSessionModel.active_document_id == active_document_id,
            )

        if search_query is not None:
            stmt = stmt.where(
                ChatSessionModel.search_vector.op("@@")(
                    func.websearch_to_tsquery("english", search_query)
                )
            )

        if cursor is not None:
            stmt = stmt.where(
                or_(
                    recent_activity < cursor.recent_activity_at,
                    and_(
                        recent_activity == cursor.recent_activity_at,
                        ChatSessionModel.session_id < cursor.session_id,
                    ),
                )
            )

        sessions = self._db.scalars(
            stmt.order_by(
                recent_activity.desc(),
                ChatSessionModel.session_id.desc(),
            ).limit(page_size + 1)
        ).all()

        has_more = len(sessions) > page_size
        items = sessions[:page_size]
        next_cursor = None
        if has_more and items:
            last_item = _session_record(items[-1])
            next_cursor = ChatSessionCursor(
                recent_activity_at=last_item.recent_activity_at,
                session_id=last_item.session_id,
            )

        return ChatSessionPage(
            items=[_session_record(session) for session in items],
            next_cursor=next_cursor,
        )

    def _list_messages_for_session(
        self,
        *,
        session_id: str,
        limit: int,
        cursor: ChatMessageCursor | None,
    ) -> ChatMessagePage:
        page_size = _validate_page_size(
            limit,
            field_name="limit",
            max_value=MAX_MESSAGE_PAGE_SIZE,
        )
        stmt = select(ChatMessageModel).where(
            ChatMessageModel.session_id == session_id,
        )

        if cursor is not None:
            stmt = stmt.where(
                or_(
                    ChatMessageModel.created_at > cursor.created_at,
                    and_(
                        ChatMessageModel.created_at == cursor.created_at,
                        ChatMessageModel.message_id > cursor.message_id,
                    ),
                )
            )

        messages = self._db.scalars(
            stmt.order_by(
                ChatMessageModel.created_at.asc(),
                ChatMessageModel.message_id.asc(),
            ).limit(page_size + 1)
        ).all()

        has_more = len(messages) > page_size
        items = messages[:page_size]
        next_cursor = None
        if has_more and items:
            last_item = items[-1]
            next_cursor = ChatMessageCursor(
                created_at=last_item.created_at,
                message_id=last_item.message_id,
            )

        return ChatMessagePage(
            items=[_message_record(message) for message in items],
            next_cursor=next_cursor,
        )


__all__ = [
    "AppendMessageResult",
    "ChatHistoryRepository",
    "ChatHistorySessionNotFoundError",
    "ChatMessageCursor",
    "ChatMessagePage",
    "ChatMessageRecord",
    "ChatSessionCursor",
    "ChatSessionDetail",
    "ChatSessionPage",
    "ChatSessionRecord",
]
