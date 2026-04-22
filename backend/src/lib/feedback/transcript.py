"""Helpers for capturing and formatting durable feedback transcripts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.lib.chat_history_repository import ChatHistoryRepository, ChatMessageCursor, ChatMessageRecord
from src.lib.chat_transcript import FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY


TRANSCRIPT_PAGE_SIZE = 200
TRANSCRIPT_EXCERPT_EDGE_TURNS = 3
MAX_INLINE_TRANSCRIPT_TURNS = TRANSCRIPT_EXCERPT_EDGE_TURNS * 2
MAX_TRANSCRIPT_TURN_CHARS = 500


def capture_feedback_conversation_transcript(
    *,
    repository: ChatHistoryRepository,
    session_id: str,
    user_auth_sub: str,
) -> dict[str, Any] | None:
    """Return one durable transcript snapshot for a visible session."""

    detail = repository.get_session_detail(
        session_id=session_id,
        user_auth_sub=user_auth_sub,
        message_limit=TRANSCRIPT_PAGE_SIZE,
    )
    if detail is None:
        return None

    messages = list(detail.messages)
    cursor: ChatMessageCursor | None = detail.next_message_cursor
    while cursor is not None:
        message_page = repository.list_messages(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            limit=TRANSCRIPT_PAGE_SIZE,
            cursor=cursor,
        )
        messages.extend(message_page.items)
        cursor = message_page.next_cursor

    session_payload: dict[str, Any] = {
        "session_id": detail.session.session_id,
        "title": detail.session.title,
        "generated_title": detail.session.generated_title,
        "effective_title": detail.session.effective_title,
        "active_document_id": (
            str(detail.session.active_document_id)
            if detail.session.active_document_id is not None
            else None
        ),
        "created_at": detail.session.created_at.isoformat(),
        "updated_at": detail.session.updated_at.isoformat(),
        "last_message_at": (
            detail.session.last_message_at.isoformat()
            if detail.session.last_message_at is not None
            else None
        ),
    }

    chat_kind = getattr(detail.session, "chat_kind", None)
    if isinstance(chat_kind, str) and chat_kind.strip():
        session_payload["chat_kind"] = chat_kind.strip()

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "message_count": len(messages),
        "session": session_payload,
        "messages": [_serialize_message(message) for message in messages],
    }


def format_feedback_transcript_section(
    *,
    transcript: dict[str, Any] | None,
    feedback_id: str,
) -> str | None:
    """Render a developer-facing transcript body section for email/SNS."""

    if not isinstance(transcript, dict):
        return None

    turns = _visible_transcript_turns(transcript.get("messages"))
    if not turns:
        return (
            "Conversation transcript snapshot:\n"
            f"Stored on feedback report {feedback_id}, but no visible turns were captured."
        )

    if len(turns) <= MAX_INLINE_TRANSCRIPT_TURNS:
        lines = [
            "Conversation transcript snapshot:",
            f"Full durable transcript stored on feedback report {feedback_id}.",
            "",
        ]
        lines.extend(_format_turns(turns, start_index=1))
        return "\n".join(lines)

    leading_turns = turns[:TRANSCRIPT_EXCERPT_EDGE_TURNS]
    trailing_turns = turns[-TRANSCRIPT_EXCERPT_EDGE_TURNS:]
    omitted_turn_count = len(turns) - len(leading_turns) - len(trailing_turns)

    lines = [
        "Conversation transcript excerpt:",
        f"First and last {TRANSCRIPT_EXCERPT_EDGE_TURNS} turns shown.",
        f"Full durable transcript stored on feedback report {feedback_id}.",
        "",
    ]
    lines.extend(_format_turns(leading_turns, start_index=1))
    lines.extend(
        [
            "",
            f"... {omitted_turn_count} middle turns omitted ...",
            "",
        ]
    )
    lines.extend(
        _format_turns(
            trailing_turns,
            start_index=len(turns) - len(trailing_turns) + 1,
        )
    )
    return "\n".join(lines)


def _serialize_message(message: ChatMessageRecord) -> dict[str, Any]:
    return {
        "message_id": str(message.message_id),
        "session_id": message.session_id,
        "turn_id": message.turn_id,
        "role": message.role,
        "message_type": message.message_type,
        "content": message.content,
        "payload_json": message.payload_json,
        "trace_id": message.trace_id,
        "created_at": message.created_at.isoformat(),
    }


def _visible_transcript_turns(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []

    turns: list[dict[str, str]] = []
    for raw_message in messages:
        if not isinstance(raw_message, dict):
            continue

        role = str(raw_message.get("role") or "").strip().lower()
        content = _message_content_for_display(raw_message)
        if not content:
            continue

        turns.append(
            {
                "role": _display_role(role),
                "content": _truncate_for_notification(content),
            }
        )

    return turns


def _message_content_for_display(message: dict[str, Any]) -> str:
    role = str(message.get("role") or "").strip().lower()
    payload_json = message.get("payload_json")
    if role == "flow" and isinstance(payload_json, dict):
        assistant_message = payload_json.get(FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY)
        if isinstance(assistant_message, str) and assistant_message.strip():
            return assistant_message.strip()

    content = message.get("content")
    if not isinstance(content, str):
        return ""
    return content.strip()


def _display_role(role: str) -> str:
    if role == "user":
        return "User"
    if role in {"assistant", "flow"}:
        return "Assistant"
    return role.title() or "Message"


def _truncate_for_notification(content: str) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= MAX_TRANSCRIPT_TURN_CHARS:
        return normalized
    return f"{normalized[: MAX_TRANSCRIPT_TURN_CHARS - 3].rstrip()}..."


def _format_turns(turns: list[dict[str, str]], *, start_index: int) -> list[str]:
    lines: list[str] = []
    for offset, turn in enumerate(turns):
        lines.append(f"{start_index + offset}. {turn['role']}: {turn['content']}")
    return lines


__all__ = [
    "MAX_INLINE_TRANSCRIPT_TURNS",
    "MAX_TRANSCRIPT_TURN_CHARS",
    "TRANSCRIPT_EXCERPT_EDGE_TURNS",
    "TRANSCRIPT_PAGE_SIZE",
    "capture_feedback_conversation_transcript",
    "format_feedback_transcript_section",
]
