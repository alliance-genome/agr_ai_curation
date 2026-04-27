"""Durable chat-session helpers for Agent Studio."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.agent_studio.models import ChatMessage
from src.lib.chat_history_repository import (
    AGENT_STUDIO_CHAT_KIND,
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
    ChatMessageRecord,
    ChatSessionRecord,
    MAX_MESSAGE_PAGE_SIZE,
)
from src.models.sql.chat_session import ChatSession as ChatSessionModel

AGENT_STUDIO_SEEDED_SESSION_PREFIX = "agent-studio-seed:"


@dataclass(frozen=True)
class PreparedAgentStudioTurn:
    """Persisted Agent Studio turn metadata used by the Opus streaming path."""

    session_id: str
    turn_id: str
    user_message: str
    requested_context_session_id: str | None
    replay_assistant_turn: ChatMessageRecord | None = None


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def serialize_chat_history_session(record: ChatSessionRecord) -> Dict[str, Any]:
    return {
        "session_id": record.session_id,
        "chat_kind": record.chat_kind,
        "title": record.title,
        "generated_title": record.generated_title,
        "effective_title": record.effective_title,
        "active_document_id": str(record.active_document_id) if record.active_document_id else None,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "last_message_at": record.last_message_at.isoformat() if record.last_message_at else None,
        "recent_activity_at": record.recent_activity_at.isoformat(),
    }


def serialize_chat_history_message(record: ChatMessageRecord) -> Dict[str, Any]:
    return {
        "message_id": str(record.message_id),
        "session_id": record.session_id,
        "chat_kind": record.chat_kind,
        "turn_id": record.turn_id,
        "role": record.role,
        "message_type": record.message_type,
        "content": record.content,
        "payload_json": record.payload_json,
        "trace_id": record.trace_id,
        "created_at": record.created_at.isoformat(),
    }


def require_tool_string(tool_input: dict[str, Any], field_name: str) -> str:
    raw_value = tool_input.get(field_name)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(f"Missing required parameter: {field_name}")
    return raw_value.strip()


def resolve_chat_history_limit(tool_input: dict[str, Any]) -> int:
    raw_limit = tool_input.get("limit", 10)
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        raise ValueError("limit must be an integer")
    return raw_limit


def with_chat_history_repository(
    callback: Callable[[ChatHistoryRepository], Dict[str, Any]],
    *,
    session_factory: Callable[[], Any],
    repository_cls: type[ChatHistoryRepository],
) -> Dict[str, Any]:
    chat_history_db = session_factory()
    try:
        repository = repository_cls(chat_history_db)
        return callback(repository)
    finally:
        chat_history_db.close()


def get_chat_conversation_payload(
    *,
    repository: ChatHistoryRepository,
    session_id: str,
    user_auth_sub: str,
    serialize_session: Callable[[ChatSessionRecord], Dict[str, Any]] = serialize_chat_history_session,
    serialize_message: Callable[[ChatMessageRecord], Dict[str, Any]] = serialize_chat_history_message,
) -> Dict[str, Any]:
    detail = repository.get_session_detail(
        session_id=session_id,
        user_auth_sub=user_auth_sub,
        message_limit=MAX_MESSAGE_PAGE_SIZE,
    )
    if detail is None:
        return {
            "success": False,
            "error": "Chat session not found.",
        }

    session_chat_kind = detail.session.chat_kind
    messages = list(detail.messages)
    cursor = detail.next_message_cursor
    while cursor is not None:
        if not session_chat_kind:
            raise ValueError("chat_kind is required to paginate the chat conversation")
        page = repository.list_messages(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=session_chat_kind,
            limit=MAX_MESSAGE_PAGE_SIZE,
            cursor=cursor,
        )
        messages.extend(page.items)
        cursor = page.next_cursor

    return {
        "success": True,
        "chat_kind": session_chat_kind,
        "session": serialize_session(detail.session),
        "message_count": len(messages),
        "messages": [serialize_message(message) for message in messages],
    }


def extract_latest_user_message(messages: List[ChatMessage]) -> str:
    if not messages:
        raise ValueError("messages must include at least one user turn")
    latest_message = messages[-1]
    if str(latest_message.role).strip() != "user":
        raise ValueError("messages must end with a user turn")
    if not latest_message.content.strip():
        raise ValueError("messages[-1].content is required")
    return latest_message.content


def build_agent_studio_turn_id(messages: List[ChatMessage]) -> str:
    user_turn_count = sum(1 for message in messages if str(message.role).strip() == "user")
    if user_turn_count < 1:
        raise ValueError("messages must include at least one user turn")

    digest_source = [
        {
            "role": str(message.role),
            "content": message.content,
        }
        for message in messages
    ]
    digest = hashlib.sha256(
        json.dumps(digest_source, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"opus-turn-{user_turn_count}-{digest}"


def derive_seeded_agent_studio_session_id(requested_session_id: str) -> str:
    derived_session_id = f"{AGENT_STUDIO_SEEDED_SESSION_PREFIX}{requested_session_id}"
    if len(derived_session_id) <= 255:
        return derived_session_id

    hashed_seed = hashlib.sha256(requested_session_id.encode("utf-8")).hexdigest()
    return f"{AGENT_STUDIO_SEEDED_SESSION_PREFIX}{hashed_seed}"


def get_active_chat_session_row(
    db: Session,
    session_id: str,
    *,
    chat_session_model: type[ChatSessionModel],
) -> ChatSessionModel | None:
    normalized_session_id = normalize_optional_text(session_id)
    if normalized_session_id is None:
        return None

    return db.scalar(
        select(chat_session_model).where(
            chat_session_model.session_id == normalized_session_id,
            chat_session_model.deleted_at.is_(None),
        )
    )


def resolve_agent_studio_session_id(
    *,
    db: Session,
    user_id: str,
    requested_session_id: str | None,
    chat_session_model: type[ChatSessionModel],
) -> str:
    normalized_requested_session_id = normalize_optional_text(requested_session_id)
    if normalized_requested_session_id is None:
        return str(uuid.uuid4())

    existing_session = get_active_chat_session_row(
        db,
        normalized_requested_session_id,
        chat_session_model=chat_session_model,
    )
    if existing_session is None:
        return normalized_requested_session_id
    if existing_session.user_auth_sub != user_id:
        raise ChatHistorySessionNotFoundError("Chat session not found")
    if existing_session.chat_kind == AGENT_STUDIO_CHAT_KIND:
        return normalized_requested_session_id

    derived_session_id = derive_seeded_agent_studio_session_id(normalized_requested_session_id)
    derived_session = get_active_chat_session_row(
        db,
        derived_session_id,
        chat_session_model=chat_session_model,
    )
    if derived_session is None:
        return derived_session_id
    if derived_session.user_auth_sub != user_id or derived_session.chat_kind != AGENT_STUDIO_CHAT_KIND:
        raise ChatHistorySessionNotFoundError("Chat session not found")
    return derived_session_id


def prepare_agent_studio_turn(
    *,
    db: Session,
    user_id: str,
    request: Any,
    chat_session_model: type[ChatSessionModel],
    repository_cls: type[ChatHistoryRepository] = ChatHistoryRepository,
) -> PreparedAgentStudioTurn:
    repository = repository_cls(db)
    requested_context_session_id = (
        normalize_optional_text(request.context.session_id) if request.context else None
    )
    session_id = resolve_agent_studio_session_id(
        db=db,
        user_id=user_id,
        requested_session_id=requested_context_session_id,
        chat_session_model=chat_session_model,
    )
    turn_id = build_agent_studio_turn_id(request.messages)
    user_message = extract_latest_user_message(request.messages)

    repository.get_or_create_session(
        session_id=session_id,
        user_auth_sub=user_id,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
    )
    user_turn = repository.append_message(
        session_id=session_id,
        user_auth_sub=user_id,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        role="user",
        content=user_message,
        turn_id=turn_id,
    )
    db.commit()

    replay_assistant_turn = None
    if not user_turn.created:
        replay_assistant_turn = repository.get_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=turn_id,
            role="assistant",
        )

    return PreparedAgentStudioTurn(
        session_id=session_id,
        turn_id=turn_id,
        user_message=user_turn.message.content,
        requested_context_session_id=requested_context_session_id,
        replay_assistant_turn=replay_assistant_turn,
    )


def assistant_tool_calls_from_payload(payload_json: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload_json, dict):
        return []

    raw_tool_calls = payload_json.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return []

    tool_calls: List[Dict[str, Any]] = []
    for tool_call in raw_tool_calls:
        if not isinstance(tool_call, dict):
            continue
        tool_name = normalize_optional_text(tool_call.get("tool_name"))
        if tool_name is None:
            continue
        tool_calls.append(dict(tool_call))
    return tool_calls


def extract_opus_text_content(content_blocks: List[Any]) -> str:
    text_parts: List[str] = []
    for block in content_blocks:
        if getattr(block, "type", None) != "text":
            continue
        text_value = getattr(block, "text", None)
        if isinstance(text_value, str):
            text_parts.append(text_value)
    return "".join(text_parts)


def build_agent_studio_assistant_payload(
    *,
    tool_calls: List[Dict[str, Any]],
    requested_context_session_id: str | None,
    session_id: str,
) -> Dict[str, Any] | None:
    payload: Dict[str, Any] = {}
    if tool_calls:
        payload["tool_calls"] = tool_calls
    if (
        requested_context_session_id is not None
        and requested_context_session_id != session_id
    ):
        payload["seed_session_id"] = requested_context_session_id
    return payload or None


def persist_completed_agent_studio_turn(
    *,
    session_id: str,
    user_id: str,
    turn_id: str,
    assistant_message: str,
    trace_id: str | None,
    payload_json: Dict[str, Any] | None,
    session_factory: Callable[[], Any],
    repository_cls: type[ChatHistoryRepository] = ChatHistoryRepository,
) -> ChatMessageRecord:
    completion_db = session_factory()
    try:
        repository = repository_cls(completion_db)
        session = repository.get_session(
            session_id=session_id,
            user_auth_sub=user_id,
        )
        if session is None or session.chat_kind != AGENT_STUDIO_CHAT_KIND:
            raise ChatHistorySessionNotFoundError("Chat session not found")

        existing_assistant_turn = repository.get_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=turn_id,
            role="assistant",
        )
        if existing_assistant_turn is not None:
            return existing_assistant_turn

        assistant_turn = repository.append_message(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=AGENT_STUDIO_CHAT_KIND,
            role="assistant",
            content=assistant_message,
            turn_id=turn_id,
            trace_id=trace_id,
            payload_json=payload_json,
        )
        completion_db.commit()
        return assistant_turn.message
    except Exception:
        completion_db.rollback()
        raise
    finally:
        completion_db.close()


def opus_sse_event(
    *,
    session_id: str,
    turn_id: str,
    event_type: str,
    **payload: Any,
) -> str:
    event_payload: Dict[str, Any] = {
        "type": event_type,
        "session_id": session_id,
        "turn_id": turn_id,
    }
    event_payload.update(payload)
    return f"data: {json.dumps(event_payload, default=str)}\n\n"


def build_agent_studio_replay_events(
    *,
    session_id: str,
    turn_id: str,
    assistant_turn: ChatMessageRecord,
) -> List[str]:
    replay_events: List[str] = []
    for tool_call in assistant_tool_calls_from_payload(assistant_turn.payload_json):
        replay_events.append(
            opus_sse_event(
                session_id=session_id,
                turn_id=turn_id,
                event_type="TOOL_USE",
                tool_name=tool_call.get("tool_name"),
                tool_input=tool_call.get("tool_input"),
            )
        )
        if "result" in tool_call:
            replay_events.append(
                opus_sse_event(
                    session_id=session_id,
                    turn_id=turn_id,
                    event_type="TOOL_RESULT",
                    tool_name=tool_call.get("tool_name"),
                    result=tool_call.get("result"),
                )
            )

    if assistant_turn.content:
        replay_events.append(
            opus_sse_event(
                session_id=session_id,
                turn_id=turn_id,
                event_type="TEXT_DELTA",
                delta=assistant_turn.content,
                trace_id=assistant_turn.trace_id,
            )
        )
    replay_events.append(
        opus_sse_event(
            session_id=session_id,
            turn_id=turn_id,
            event_type="DONE",
            trace_id=assistant_turn.trace_id,
        )
    )
    return replay_events
