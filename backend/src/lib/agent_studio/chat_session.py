"""Durable chat-session helpers for Agent Studio."""

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.agent_studio.models import ChatMessage
from src.lib.chat_history_repository import (
    AGENT_STUDIO_CHAT_KIND,
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
    ChatMessageCursor,
    ChatMessageRecord,
    ChatSessionCursor,
    ChatSessionRecord,
    decode_chat_session_cursor,
    decode_chat_message_cursor,
    encode_chat_session_cursor,
    encode_chat_message_cursor,
)
from src.lib.openai_agents.chat_compaction_session import CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE
from src.models.sql.chat_session import ChatSession as ChatSessionModel

AGENT_STUDIO_SEEDED_SESSION_PREFIX = "agent-studio-seed:"
AGENT_STUDIO_HIDDEN_MESSAGE_TYPES = frozenset({CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE})


@dataclass(frozen=True)
class PreparedAgentStudioTurn:
    """Persisted Agent Studio turn metadata used by the Opus streaming path."""

    session_id: str
    turn_id: str
    user_message: str
    requested_context_session_id: str | None
    user_turn_created: bool = True
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
        "active_document_id": str(record.active_document_id) if record.active_document_id else None,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "last_message_at": record.last_message_at.isoformat() if record.last_message_at else None,
        "recent_activity_at": record.recent_activity_at.isoformat(),
    }


def serialize_chat_history_session_summary(record: ChatSessionRecord) -> Dict[str, Any]:
    """Serialize the compact provider-facing projection for a session page."""

    return {
        "session_id": record.session_id,
        "chat_kind": record.chat_kind,
        "title": record.effective_title,
        "active_document_id": str(record.active_document_id) if record.active_document_id else None,
        "recent_activity_at": record.recent_activity_at.isoformat(),
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _serialize_payload_json(value: dict[str, Any] | list[Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _field_metadata(value: str | None, *, serialization: str) -> Dict[str, Any] | None:
    if value is None:
        return None
    return {
        "length": len(value),
        "sha256": _sha256_text(value),
        "serialization": serialization,
    }


def _serialize_chat_message_summary(record: ChatMessageRecord) -> Dict[str, Any]:
    return {
        "message_id": str(record.message_id),
        "turn_id": record.turn_id,
        "role": record.role,
        "message_type": record.message_type,
        "trace_id": record.trace_id,
        "created_at": record.created_at.isoformat(),
        "fields": {
            "content": _field_metadata(record.content, serialization="utf-8 text"),
            "payload_json": _field_metadata(
                _serialize_payload_json(record.payload_json),
                serialization="canonical JSON (UTF-8, sorted keys, compact separators)",
            ),
        },
    }


def _cursor_for_message(message: ChatMessageRecord) -> ChatMessageCursor:
    return ChatMessageCursor(
        created_at=message.created_at,
        message_id=message.message_id,
    )


def _provider_result_chars(value: Dict[str, Any]) -> int:
    return len(json.dumps(value, default=str))


def _bounded_page_result(
    *,
    items: list[ChatMessageRecord],
    repository_next_cursor: ChatMessageCursor | None,
    provider_inline_max_chars: int,
    build_result: Callable[[list[ChatMessageRecord], ChatMessageCursor | None], Dict[str, Any]],
) -> Dict[str, Any]:
    """Fit a metadata page to the actual provider-visible JSON envelope."""

    for item_count in range(len(items), -1, -1):
        page_items = items[:item_count]
        if item_count < len(items) and page_items:
            next_cursor = _cursor_for_message(page_items[-1])
        else:
            next_cursor = repository_next_cursor
        candidate = build_result(page_items, next_cursor)
        if _provider_result_chars(candidate) <= provider_inline_max_chars:
            if item_count == 0 and items:
                break
            return candidate
    return {
        "success": False,
        "error": (
            "The provider inline tool-result limit is too small to return one "
            "chat row with its required stable identity metadata."
        ),
        "provider_inline_max_chars": provider_inline_max_chars,
    }


def require_tool_string(tool_input: dict[str, Any], field_name: str) -> str:
    raw_value = tool_input.get(field_name)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(f"Missing required parameter: {field_name}")
    return raw_value.strip()


def resolve_chat_history_limit(tool_input: dict[str, Any], *, max_limit: int) -> int:
    raw_limit = tool_input.get("limit", min(10, max_limit))
    if (
        isinstance(raw_limit, bool)
        or not isinstance(raw_limit, int)
        or raw_limit < 1
        or raw_limit > max_limit
    ):
        raise ValueError(f"limit must be an integer from 1 to {max_limit}")
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


def get_chat_session_page_payload(
    *,
    repository: ChatHistoryRepository,
    user_auth_sub: str,
    chat_kind: str,
    cursor: str | None,
    limit: int,
    provider_inline_max_chars: int,
    query: str | None = None,
    serialize_session: Callable[[ChatSessionRecord], Dict[str, Any]] = (
        serialize_chat_history_session_summary
    ),
) -> Dict[str, Any]:
    """Return a keyset-paged session list fitted to the provider-visible JSON."""

    session_cursor = decode_chat_session_cursor(cursor)
    if query is None:
        if session_cursor is not None and session_cursor.relevance is not None:
            raise ValueError(
                "ranked search cursor cannot be used for list_recent_chats"
            )
        page = repository.list_sessions(
            user_auth_sub=user_auth_sub,
            chat_kind=chat_kind,
            limit=limit,
            cursor=session_cursor,
        )
        tool_name = "list_recent_chats"
    else:
        page = repository.search_sessions_ranked(
            user_auth_sub=user_auth_sub,
            chat_kind=chat_kind,
            query=query,
            limit=limit,
            cursor=session_cursor,
        )
        tool_name = "search_chat_history"

    count_arguments: Dict[str, Any] = {
        "user_auth_sub": user_auth_sub,
        "chat_kind": chat_kind,
    }
    if query is not None:
        count_arguments["query"] = query
    total_sessions = repository.count_sessions(
        **count_arguments,
    )
    range_start = session_cursor.position if session_cursor is not None else 0

    def cursor_after(item_count: int) -> ChatSessionCursor | None:
        if item_count < len(page.items):
            if page.item_cursors is not None:
                base_cursor = page.item_cursors[item_count - 1]
            else:
                item = page.items[item_count - 1]
                base_cursor = ChatSessionCursor(
                    recent_activity_at=item.recent_activity_at,
                    session_id=item.session_id,
                )
        else:
            base_cursor = page.next_cursor
        if base_cursor is None:
            return None
        return replace(base_cursor, position=range_start + item_count)

    def build_result(
        page_items: list[ChatSessionRecord],
        next_cursor_value: ChatSessionCursor | None,
    ) -> Dict[str, Any]:
        encoded_next_cursor = encode_chat_session_cursor(next_cursor_value)
        complete = encoded_next_cursor is None
        arguments: Dict[str, Any] = {
            "chat_kind": chat_kind,
            "limit": limit,
            "cursor": encoded_next_cursor,
        }
        if query is not None:
            arguments["query"] = query
        return {
            "success": True,
            "contract_version": "chat_session_page.v1",
            "view": "ranked_search_page" if query is not None else "recent_session_page",
            **({"query": query} if query is not None else {}),
            "chat_kind": chat_kind,
            "limit": limit,
            "total_sessions": total_sessions,
            "returned_range": {
                "start": range_start,
                "end": range_start + len(page_items),
            },
            "sessions": [serialize_session(item) for item in page_items],
            "complete": complete,
            "next_call": (
                None
                if complete
                else {
                    "tool": tool_name,
                    "arguments": arguments,
                }
            ),
            "instruction": (
                "Follow next_call until complete=true. Call get_chat_conversation "
                "with a session_id for exact persisted title fields and transcript metadata."
            ),
        }

    for item_count in range(len(page.items), -1, -1):
        if item_count == 0 and page.items:
            break
        candidate = build_result(page.items[:item_count], cursor_after(item_count))
        if _provider_result_chars(candidate) <= provider_inline_max_chars:
            return candidate

    return {
        "success": False,
        "error": (
            "The provider inline tool-result limit is too small to return one "
            "chat session with its stable identity and effective title."
        ),
        "provider_inline_max_chars": provider_inline_max_chars,
    }


def get_chat_conversation_payload(
    *,
    repository: ChatHistoryRepository,
    session_id: str,
    user_auth_sub: str,
    cursor: str | None,
    limit: int,
    provider_inline_max_chars: int,
    serialize_session: Callable[[ChatSessionRecord], Dict[str, Any]] = serialize_chat_history_session,
) -> Dict[str, Any]:
    message_cursor = decode_chat_message_cursor(cursor)
    detail = repository.get_session_detail(
        session_id=session_id,
        user_auth_sub=user_auth_sub,
        message_limit=limit,
        message_cursor=message_cursor,
        excluded_message_types=set(AGENT_STUDIO_HIDDEN_MESSAGE_TYPES),
    )
    if detail is None:
        return {
            "success": False,
            "error": "Chat session not found.",
        }

    chat_kind = detail.session.chat_kind
    if not chat_kind:
        raise ValueError("chat_kind is required to paginate the chat conversation")
    excluded_types = set(AGENT_STUDIO_HIDDEN_MESSAGE_TYPES)
    total_messages = repository.count_messages(
        session_id=session_id,
        user_auth_sub=user_auth_sub,
        chat_kind=chat_kind,
        excluded_message_types=excluded_types,
    )
    range_start = (
        repository.count_messages(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=chat_kind,
            excluded_message_types=excluded_types,
            through_cursor=message_cursor,
        )
        if message_cursor is not None
        else 0
    )

    def build_result(
        page_items: list[ChatMessageRecord],
        next_cursor_value: ChatMessageCursor | None,
    ) -> Dict[str, Any]:
        encoded_next_cursor = encode_chat_message_cursor(next_cursor_value)
        complete = encoded_next_cursor is None
        next_call = None
        if not complete:
            next_call = {
                "tool": "get_chat_conversation",
                "arguments": {
                    "session_id": session_id,
                    "cursor": encoded_next_cursor,
                    "limit": limit,
                },
            }
        return {
            "success": True,
            "contract_version": "chat_conversation_recall.v1",
            "view": "message_page",
            "chat_kind": chat_kind,
            "session": serialize_session(detail.session),
            "total_message_count": total_messages,
            "returned_range": {
                "start": range_start,
                "end": range_start + len(page_items),
            },
            "messages": [_serialize_chat_message_summary(item) for item in page_items],
            "complete": complete,
            "next_call": next_call,
            "instruction": (
                "Follow next_call until complete=true. Select a turn_id and call "
                "get_chat_turn for independently chunked exact row fields."
            ),
        }

    return _bounded_page_result(
        items=list(detail.messages),
        repository_next_cursor=detail.next_message_cursor,
        provider_inline_max_chars=provider_inline_max_chars,
        build_result=build_result,
    )


def get_chat_turn_payload(
    *,
    repository: ChatHistoryRepository,
    session_id: str,
    turn_id: str,
    user_auth_sub: str,
    cursor: str | None,
    limit: int,
    message_id: str | None,
    field: str | None,
    start: int | None,
    max_chars: int | None,
    field_hash: str | None,
    chunk_max_chars: int,
    provider_inline_max_chars: int,
    serialize_session: Callable[[ChatSessionRecord], Dict[str, Any]] = serialize_chat_history_session,
) -> Dict[str, Any]:
    session = repository.get_session(
        session_id=session_id,
        user_auth_sub=user_auth_sub,
    )
    if session is None:
        return {
            "success": False,
            "error": "Chat session not found.",
        }
    if not session.chat_kind:
        raise ValueError("chat_kind is required to load a chat turn")

    detail_requested = any(
        value is not None
        for value in (message_id, field, start, max_chars, field_hash)
    )
    if detail_requested:
        if cursor is not None:
            raise ValueError("cursor cannot be combined with exact field retrieval")
        if (
            not isinstance(message_id, str)
            or not message_id.strip()
            or field not in {"content", "payload_json"}
        ):
            raise ValueError("message_id and field are required for exact field retrieval")
        if not isinstance(field_hash, str) or not field_hash.strip():
            raise ValueError("field_hash is required for exact field retrieval")
        message_id = message_id.strip()
        field_hash = field_hash.strip()
        chunk_start = 0 if start is None else start
        if isinstance(chunk_start, bool) or not isinstance(chunk_start, int) or chunk_start < 0:
            raise ValueError("start must be a non-negative integer")
        requested_max_chars = chunk_max_chars if max_chars is None else max_chars
        if (
            isinstance(requested_max_chars, bool)
            or not isinstance(requested_max_chars, int)
            or requested_max_chars < 1
        ):
            raise ValueError("max_chars must be a positive integer")
        chunk_size = min(requested_max_chars, chunk_max_chars)
        try:
            durable_message_id = UUID(message_id)
        except ValueError as exc:
            raise ValueError("message_id must be a valid UUID") from exc
        message = repository.get_message_by_id(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=session.chat_kind,
            message_id=durable_message_id,
            excluded_message_types=set(AGENT_STUDIO_HIDDEN_MESSAGE_TYPES),
        )
        if message is None or message.turn_id != turn_id:
            return {
                "success": False,
                "error": "Chat turn row not found.",
                "turn_id": turn_id,
                "message_id": message_id,
            }
        serialized_value = (
            message.content
            if field == "content"
            else _serialize_payload_json(message.payload_json)
        )
        if serialized_value is None:
            return {
                "success": False,
                "error": f"Chat turn row has no {field} value.",
                "turn_id": turn_id,
                "message_id": message_id,
            }
        current_hash = _sha256_text(serialized_value)
        if field_hash != current_hash:
            return {
                "success": False,
                "error": "The durable chat field changed; reload turn metadata and restart chunks.",
                "current_hash": current_hash,
                "length": len(serialized_value),
            }
        if chunk_start > len(serialized_value):
            raise ValueError(
                f"start {chunk_start} exceeds field length {len(serialized_value)}"
            )

        def build_chunk_result(end: int) -> Dict[str, Any]:
            complete = end == len(serialized_value)
            next_call = None
            if not complete:
                next_call = {
                    "tool": "get_chat_turn",
                    "arguments": {
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "message_id": message_id,
                        "field": field,
                        "field_hash": current_hash,
                        "start": end,
                        "max_chars": chunk_size,
                    },
                }
            return {
                "success": True,
                "contract_version": "chat_turn_field_recall.v1",
                "view": "field_chunk",
                "session_id": session_id,
                "turn_id": turn_id,
                "message_id": message_id,
                "field": field,
                "serialization": (
                    "utf-8 text"
                    if field == "content"
                    else "canonical JSON (UTF-8, sorted keys, compact separators)"
                ),
                "length": len(serialized_value),
                "sha256": current_hash,
                "returned_range": {"start": chunk_start, "end": end},
                "chunk": serialized_value[chunk_start:end],
                "complete": complete,
                "next_call": next_call,
                "instruction": "Concatenate chunks in returned_range order and verify sha256.",
            }

        requested_end = min(chunk_start + chunk_size, len(serialized_value))
        candidate = build_chunk_result(requested_end)
        if _provider_result_chars(candidate) <= provider_inline_max_chars:
            return candidate
        low = chunk_start + 1
        high = requested_end - 1
        fitting_result = None
        while low <= high:
            candidate_end = (low + high) // 2
            candidate = build_chunk_result(candidate_end)
            if _provider_result_chars(candidate) <= provider_inline_max_chars:
                fitting_result = candidate
                low = candidate_end + 1
            else:
                high = candidate_end - 1
        if fitting_result is not None:
            return fitting_result
        return {
            "success": False,
            "error": (
                "The provider inline tool-result limit is too small to return even one "
                "exact chat field character with its required identity metadata."
            ),
            "provider_inline_max_chars": provider_inline_max_chars,
        }

    message_cursor = decode_chat_message_cursor(cursor)
    excluded_types = set(AGENT_STUDIO_HIDDEN_MESSAGE_TYPES)
    page = repository.list_messages_for_turn_page(
        session_id=session_id,
        user_auth_sub=user_auth_sub,
        chat_kind=session.chat_kind,
        turn_id=turn_id,
        limit=limit,
        cursor=message_cursor,
        excluded_message_types=excluded_types,
    )
    total_messages = repository.count_messages(
        session_id=session_id,
        user_auth_sub=user_auth_sub,
        chat_kind=session.chat_kind,
        turn_id=turn_id,
        excluded_message_types=excluded_types,
    )
    if total_messages == 0:
        return {
            "success": False,
            "error": "Chat turn not found.",
            "session": serialize_session(session),
            "turn_id": turn_id,
        }

    range_start = (
        repository.count_messages(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=session.chat_kind,
            turn_id=turn_id,
            excluded_message_types=excluded_types,
            through_cursor=message_cursor,
        )
        if message_cursor is not None
        else 0
    )

    def build_result(
        page_items: list[ChatMessageRecord],
        next_cursor_value: ChatMessageCursor | None,
    ) -> Dict[str, Any]:
        row_summaries = []
        for message in page_items:
            summary = _serialize_chat_message_summary(message)
            for field_name, metadata in summary["fields"].items():
                if metadata is not None:
                    metadata["complete"] = False
                    metadata["next_call"] = {
                        "tool": "get_chat_turn",
                        "arguments": {
                            "session_id": session_id,
                            "turn_id": turn_id,
                            "message_id": str(message.message_id),
                            "field": field_name,
                            "field_hash": metadata["sha256"],
                            "start": 0,
                            "max_chars": chunk_max_chars,
                        },
                    }
            row_summaries.append(summary)
        encoded_next_cursor = encode_chat_message_cursor(next_cursor_value)
        complete = encoded_next_cursor is None
        next_call = None
        if not complete:
            next_call = {
                "tool": "get_chat_turn",
                "arguments": {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "cursor": encoded_next_cursor,
                    "limit": limit,
                },
            }
        return {
            "success": True,
            "contract_version": "chat_turn_recall.v1",
            "view": "row_page",
            "chat_kind": session.chat_kind,
            "session": serialize_session(session),
            "turn_id": turn_id,
            "total_message_count": total_messages,
            "returned_range": {
                "start": range_start,
                "end": range_start + len(page_items),
            },
            "messages": row_summaries,
            "complete": complete,
            "next_call": next_call,
            "durability": (
                "Completed prior turns are recallable here. An in-flight same-turn raw "
                "tool result exists only in the current provider tool continuation until "
                "the assistant turn completes and is persisted."
            ),
        }

    return _bounded_page_result(
        items=list(page.items),
        repository_next_cursor=page.next_cursor,
        provider_inline_max_chars=provider_inline_max_chars,
        build_result=build_result,
    )


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
        user_turn_created=user_turn.created,
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
    trace_capture: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    payload: Dict[str, Any] = {}
    if tool_calls:
        payload["tool_calls"] = tool_calls
    if trace_capture:
        payload["trace_capture"] = trace_capture
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
        tool_input = tool_call.get("argument_summary")
        result_payload = {
            "status": tool_call.get("result_status"),
            "error": tool_call.get("result_error"),
            "summary": tool_call.get("result_summary"),
            "backend_blocked_tool_scope": tool_call.get("backend_blocked_tool_scope"),
        }
        replay_events.append(
            opus_sse_event(
                session_id=session_id,
                turn_id=turn_id,
                event_type="TOOL_USE",
                tool_name=tool_call.get("tool_name"),
                tool_input=tool_input,
            )
        )
        if any(value is not None for value in result_payload.values()):
            replay_events.append(
                opus_sse_event(
                    session_id=session_id,
                    turn_id=turn_id,
                    event_type="TOOL_RESULT",
                    tool_name=tool_call.get("tool_name"),
                    result=result_payload,
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
