"""Unit tests for supervisor transcript recall tools."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from src.lib.chat_history_repository import ASSISTANT_CHAT_KIND, ChatMessageRecord
from src.lib.openai_agents import supervisor_context_tools as module


def _message(
    *,
    role: str,
    content: str,
    turn_id: str | None,
    message_type: str = "text",
    minute: int = 0,
) -> ChatMessageRecord:
    return ChatMessageRecord(
        message_id=uuid4(),
        session_id="session-recall",
        chat_kind=ASSISTANT_CHAT_KIND,
        turn_id=turn_id,
        role=role,
        message_type=message_type,
        content=content,
        payload_json=None,
        trace_id=None,
        created_at=datetime(2026, 6, 15, 12, minute, tzinfo=timezone.utc),
    )


def _patch_active_chat(monkeypatch):
    monkeypatch.setattr(module, "get_current_session_id", lambda: "session-recall")
    monkeypatch.setattr(module, "get_current_user_id", lambda: "user-recall")


def test_recall_chat_history_fetches_exact_turn_after_projection_row(monkeypatch):
    _patch_active_chat(monkeypatch)
    earlier_user = _message(
        role="user",
        content="Please remember the exact allele note: unc-26(e205).",
        turn_id="turn-old",
        minute=1,
    )
    projection = _message(
        role="assistant",
        content="Compacted projection, not transcript.",
        turn_id=None,
        message_type=module.CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE,
        minute=2,
    )
    earlier_assistant = _message(
        role="assistant",
        content="Exact allele note recorded: unc-26(e205).",
        turn_id="turn-old",
        minute=3,
    )
    monkeypatch.setattr(
        module,
        "_list_session_messages",
        lambda **_kwargs: [earlier_user, projection, earlier_assistant],
    )

    payload = json.loads(
        asyncio.run(module.recall_chat_history(detail="turn", turn_ref="turn-old"))
    )

    assert payload["status"] == "ok"
    assert [message["content"] for message in payload["messages"]] == [
        "Please remember the exact allele note: unc-26(e205).",
        "Exact allele note recorded: unc-26(e205).",
    ]


def test_recall_chat_history_search_returns_exact_current_session_results(monkeypatch):
    _patch_active_chat(monkeypatch)
    matching = _message(
        role="assistant",
        content="The exact phrase is alpha beta gamma.",
        turn_id="turn-search",
        minute=1,
    )

    class _FakeRepo:
        def __init__(self, _db):
            pass

        def search_session_messages_ranked(self, **kwargs):
            assert kwargs["session_id"] == "session-recall"
            assert kwargs["user_auth_sub"] == "user-recall"
            assert kwargs["chat_kind"] == ASSISTANT_CHAT_KIND
            assert kwargs["query"] == "alpha beta"
            return [
                matching,
                _message(
                    role="assistant",
                    content="projection row",
                    turn_id=None,
                    message_type=module.CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE,
                ),
            ]

    db = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "ChatHistoryRepository", _FakeRepo)

    payload = json.loads(
        asyncio.run(module.recall_chat_history(detail="search", query="alpha beta"))
    )

    assert payload["status"] == "ok"
    assert payload["messages"] == [
        {
            "ordinal": 1,
            "message_id": str(matching.message_id),
            "turn_id": "turn-search",
            "role": "assistant",
            "message_type": "text",
            "created_at": matching.created_at.isoformat(),
            "content": "The exact phrase is alpha beta gamma.",
        }
    ]
