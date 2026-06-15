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
    recent_user = _message(
        role="user",
        content="Recent visible message, not the requested older turn.",
        turn_id="turn-recent",
        minute=4,
    )
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
    list_session_calls = []
    repo_calls = []

    class _FakeRepo:
        def __init__(self, _db):
            pass

        def list_messages_for_turn(self, **kwargs):
            repo_calls.append(kwargs)
            assert kwargs["session_id"] == "session-recall"
            assert kwargs["user_auth_sub"] == "user-recall"
            assert kwargs["chat_kind"] == ASSISTANT_CHAT_KIND
            assert kwargs["turn_id"] == "turn-old"
            return [earlier_user, projection, earlier_assistant]

    db = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "ChatHistoryRepository", _FakeRepo)
    monkeypatch.setattr(
        module,
        "_list_session_messages",
        lambda **kwargs: list_session_calls.append(kwargs) or [recent_user],
    )

    payload = json.loads(
        asyncio.run(module.recall_chat_history(detail="turn", turn_ref="turn-old"))
    )

    assert list_session_calls == []
    assert len(repo_calls) == 1
    assert payload["status"] == "ok"
    assert [message["content"] for message in payload["messages"]] == [
        "Please remember the exact allele note: unc-26(e205).",
        "Exact allele note recorded: unc-26(e205).",
    ]


def test_recall_chat_history_fetches_message_id_ref_from_durable_store(monkeypatch):
    _patch_active_chat(monkeypatch)
    earlier_user = _message(
        role="user",
        content="Need the exact older question.",
        turn_id="turn-message-id",
        minute=1,
    )
    earlier_assistant = _message(
        role="assistant",
        content="Here is the exact older answer.",
        turn_id="turn-message-id",
        minute=2,
    )
    projection = _message(
        role="assistant",
        content="Compacted projection should stay hidden.",
        turn_id=None,
        message_type=module.CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE,
        minute=3,
    )

    class _FakeRepo:
        def __init__(self, _db):
            pass

        def get_message_by_id(self, **kwargs):
            assert kwargs["session_id"] == "session-recall"
            assert kwargs["user_auth_sub"] == "user-recall"
            assert kwargs["chat_kind"] == ASSISTANT_CHAT_KIND
            assert kwargs["message_id"] == earlier_assistant.message_id
            return earlier_assistant

        def list_messages_for_turn(self, **kwargs):
            assert kwargs["turn_id"] == "turn-message-id"
            return [earlier_user, earlier_assistant, projection]

    db = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "ChatHistoryRepository", _FakeRepo)
    monkeypatch.setattr(
        module,
        "_list_session_messages",
        lambda **_kwargs: [],
    )

    payload = json.loads(
        asyncio.run(
            module.recall_chat_history(
                detail="turn",
                turn_ref=str(earlier_assistant.message_id),
            )
        )
    )

    assert payload["status"] == "ok"
    assert [message["content"] for message in payload["messages"]] == [
        "Need the exact older question.",
        "Here is the exact older answer.",
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
            assert kwargs["excluded_message_types"] == {
                module.CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE
            }
            return [
                matching,
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
