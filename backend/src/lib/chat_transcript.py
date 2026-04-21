"""Durable chat transcript helpers shared across API and agent runtime code."""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy.orm import Session

from src.lib.chat_history_repository import (
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
    ChatMessageCursor,
    ChatMessageRecord,
)
from src.models.sql.database import SessionLocal


FLOW_SUMMARY_MESSAGE_TYPE = "flow_summary"
FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY = "_assistant_message"


def extract_flow_assistant_message(message: ChatMessageRecord) -> str | None:
    """Return the hidden assistant flow-memory message stored on a durable flow row."""

    if message.role != "flow" or not isinstance(message.payload_json, dict):
        return None

    assistant_message = message.payload_json.get(FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY)
    if not isinstance(assistant_message, str):
        return None

    normalized = assistant_message.strip()
    return normalized or None


def collect_durable_text_exchanges(
    messages: Sequence[ChatMessageRecord],
    *,
    pending_user_message: Optional[str] = None,
) -> tuple[list[tuple[str, str]], Optional[str]]:
    """Pair durable transcript rows into completed user/assistant exchanges."""

    exchanges: list[tuple[str, str]] = []

    for message in messages:
        if not message.content.strip():
            continue

        if message.role == "user":
            pending_user_message = message.content
            continue

        if pending_user_message is None:
            continue

        if message.role == "assistant" and message.message_type == "text":
            exchanges.append((pending_user_message, message.content))
            pending_user_message = None
            continue

        if message.role == "flow":
            assistant_message = extract_flow_assistant_message(message)
            if assistant_message is None:
                continue
            exchanges.append((pending_user_message, assistant_message))
            pending_user_message = None

    return exchanges, pending_user_message


def _list_session_messages(
    *,
    repository: ChatHistoryRepository,
    session_id: str,
    user_id: str,
) -> list[ChatMessageRecord]:
    messages: list[ChatMessageRecord] = []
    message_cursor: ChatMessageCursor | None = None

    while True:
        message_page = repository.list_messages(
            session_id=session_id,
            user_auth_sub=user_id,
            limit=200,
            cursor=message_cursor,
        )
        if not message_page.items:
            break

        messages.extend(message_page.items)
        if message_page.next_cursor is None:
            break
        message_cursor = message_page.next_cursor

    return messages


def list_session_text_exchanges(
    *,
    session_id: str,
    user_id: str,
    db: Session | None = None,
    repository: ChatHistoryRepository | None = None,
) -> list[tuple[str, str]]:
    """Return completed durable exchanges for one visible chat session."""

    if repository is not None:
        try:
            messages = _list_session_messages(
                repository=repository,
                session_id=session_id,
                user_id=user_id,
            )
        except ChatHistorySessionNotFoundError:
            return []
        exchanges, _pending_user_message = collect_durable_text_exchanges(messages)
        return exchanges

    owns_session = db is None
    session = db or SessionLocal()
    try:
        durable_repository = ChatHistoryRepository(session)
        try:
            messages = _list_session_messages(
                repository=durable_repository,
                session_id=session_id,
                user_id=user_id,
            )
        except ChatHistorySessionNotFoundError:
            return []
        exchanges, _pending_user_message = collect_durable_text_exchanges(messages)
        return exchanges
    finally:
        if owns_session:
            session.close()


def latest_assistant_message_for_session(
    *,
    session_id: str,
    user_id: str,
    db: Session | None = None,
    repository: ChatHistoryRepository | None = None,
) -> str | None:
    """Return the latest completed assistant message for one visible session."""

    exchanges = list_session_text_exchanges(
        session_id=session_id,
        user_id=user_id,
        db=db,
        repository=repository,
    )
    if not exchanges:
        return None
    return exchanges[-1][1]


def count_session_text_messages(
    *,
    session_id: str,
    user_id: str,
    db: Session | None = None,
    repository: ChatHistoryRepository | None = None,
) -> int:
    """Return the visible completed text-message count for one durable session."""

    return len(
        list_session_text_exchanges(
            session_id=session_id,
            user_id=user_id,
            db=db,
            repository=repository,
        )
    ) * 2


__all__ = [
    "FLOW_SUMMARY_MESSAGE_TYPE",
    "FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY",
    "collect_durable_text_exchanges",
    "count_session_text_messages",
    "extract_flow_assistant_message",
    "latest_assistant_message_for_session",
    "list_session_text_exchanges",
]
