"""Read-only size report for durable chat replay context."""

from __future__ import annotations

from typing import Any

from src.lib.chat_history_repository import (
    ASSISTANT_CHAT_KIND,
    AGENT_STUDIO_CHAT_KIND,
    ChatHistoryRepository,
    ChatMessageCursor,
    ChatMessageRecord,
)
from src.lib.chat_transcript import extract_flow_assistant_message
from src.lib.runtime_payload_budget import classify_threshold, estimate_tokens_from_chars, json_size


VALID_REPORT_CHAT_KINDS = {ASSISTANT_CHAT_KIND, AGENT_STUDIO_CHAT_KIND}


def build_chat_context_report(
    *,
    repository: ChatHistoryRepository,
    session_id: str,
    user_auth_sub: str,
    chat_kind: str = ASSISTANT_CHAT_KIND,
) -> dict[str, Any] | None:
    if chat_kind not in VALID_REPORT_CHAT_KINDS:
        raise ValueError("chat_kind must be 'assistant_chat' or 'agent_studio'")

    messages = _list_all_messages(
        repository=repository,
        session_id=session_id,
        user_auth_sub=user_auth_sub,
        chat_kind=chat_kind,
    )
    if messages is None:
        return None

    rows = [_message_report(message) for message in messages]
    visible_content_chars = sum(row["content_chars"] for row in rows)
    payload_json_chars = sum(row["payload_json_chars"] for row in rows)
    replay_content_chars = sum(row["replay_content_chars"] for row in rows)
    hidden_flow_memory_chars = sum(
        row["replay_content_chars"]
        for row in rows
        if row["role"] == "flow" and row["model_live_source"] == "_assistant_message"
    )
    flow_memory_message_count = sum(
        1
        for row in rows
        if row["role"] == "flow" and row["model_live_source"] == "_assistant_message"
    )
    estimated_replay_tokens = estimate_tokens_from_chars(replay_content_chars)
    return {
        "session_id": session_id,
        "chat_kind": chat_kind,
        "message_count": len(rows),
        "visible_content_chars": visible_content_chars,
        "payload_json_chars": payload_json_chars,
        "hidden_flow_memory_chars": hidden_flow_memory_chars,
        "flow_memory_message_count": flow_memory_message_count,
        "trace_ids": sorted({row["trace_id"] for row in rows if row["trace_id"]}),
        "messages": rows,
        "estimated_replay_tokens": estimated_replay_tokens,
        "threshold": classify_threshold(estimated_replay_tokens),
    }


def _list_all_messages(
    *,
    repository: ChatHistoryRepository,
    session_id: str,
    user_auth_sub: str,
    chat_kind: str,
) -> list[ChatMessageRecord] | None:
    messages: list[ChatMessageRecord] = []
    cursor: ChatMessageCursor | None = None

    while True:
        page = repository.list_messages(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=chat_kind,
            limit=200,
            cursor=cursor,
        )
        if not page.items:
            break
        messages.extend(page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    return messages


def _message_report(message: ChatMessageRecord) -> dict[str, Any]:
    content_chars = len(message.content or "")
    payload_json_chars = (
        json_size(message.payload_json).json_chars
        if message.payload_json is not None
        else 0
    )
    flow_replay_text = extract_flow_assistant_message(message)
    if message.role == "flow" and flow_replay_text is not None:
        model_live = True
        model_live_source = "_assistant_message"
        replay_content_chars = len(flow_replay_text)
    else:
        model_live = message.role in {"user", "assistant"}
        model_live_source = "content" if model_live else None
        replay_content_chars = content_chars if model_live else 0

    return {
        "message_id": str(message.message_id),
        "role": message.role,
        "message_type": message.message_type,
        "content_chars": content_chars,
        "payload_json_chars": payload_json_chars,
        "trace_id": message.trace_id,
        "model_live": model_live,
        "model_live_source": model_live_source,
        "payload_json_model_live": False,
        "replay_content_chars": replay_content_chars,
    }

