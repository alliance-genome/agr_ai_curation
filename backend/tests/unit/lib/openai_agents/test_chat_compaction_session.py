"""Unit tests for standard-chat compaction session behavior."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.chat_history_repository import ASSISTANT_CHAT_KIND, ChatMessageRecord
from src.lib.chat_transcript import FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY
from src.lib.openai_agents import chat_compaction_session as module


def _message(
    *,
    role: str,
    content: str,
    turn_id: str | None,
    message_type: str = "text",
    payload_json=None,
    minute: int = 0,
) -> ChatMessageRecord:
    return ChatMessageRecord(
        message_id=uuid4(),
        session_id="session-1",
        chat_kind=ASSISTANT_CHAT_KIND,
        turn_id=turn_id,
        role=role,
        message_type=message_type,
        content=content,
        payload_json=payload_json,
        trace_id=None,
        created_at=datetime(2026, 6, 15, 12, minute, tzinfo=timezone.utc),
    )


class _FakeDb:
    def __init__(self, projection=None):
        self.projection = projection
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.deleted = []

    def scalar(self, _stmt):
        return self.projection

    def execute(self, stmt):
        self.deleted.append(stmt)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class _FakeRepository:
    def __init__(self, _db, messages, appended):
        self._messages = messages
        self._appended = appended
        self.list_calls = []

    def get_session(self, **_kwargs):
        return SimpleNamespace(session_id="session-1")

    def list_messages(self, **kwargs):
        self.list_calls.append(kwargs)
        assert kwargs["limit"] == module.MAX_MESSAGE_PAGE_SIZE
        messages = self._messages
        after_created_at = kwargs.get("after_created_at")
        if after_created_at is not None:
            messages = [message for message in messages if message.created_at > after_created_at]
        return SimpleNamespace(items=messages, next_cursor=None)

    def append_message(self, **kwargs):
        self._appended.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(message_id=uuid4()))


def test_durable_session_get_items_excludes_current_turn_and_keeps_flow_refs(monkeypatch):
    messages = [
        _message(role="user", content="old question", turn_id="turn-1", minute=1),
        _message(role="assistant", content="old answer", turn_id="turn-1", minute=2),
        _message(role="user", content="run flow", turn_id="turn-flow", minute=3),
        _message(
            role="flow",
            content="visible flow transcript row with bulky details",
            turn_id="turn-flow",
            message_type="flow_summary",
            payload_json={
                FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY: (
                    "Flow refs only: flow_run_id=flow-1 extraction-result:abc"
                ),
                "raw_payload": {"large": "should not be replayed"},
            },
            minute=4,
        ),
        _message(role="user", content="current prompt already persisted", turn_id="turn-2", minute=5),
    ]
    db = _FakeDb()
    appended = []
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        module,
        "ChatHistoryRepository",
        lambda fake_db: _FakeRepository(fake_db, messages, appended),
    )

    session = module.DurableChatHistorySession(
        session_id="session-1",
        user_id="user-1",
        current_turn_id="turn-2",
    )

    assert asyncio.run(session.get_items()) == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "run flow"},
        {
            "role": "assistant",
            "content": "Flow refs only: flow_run_id=flow-1 extraction-result:abc",
        },
    ]


def test_durable_session_get_items_fetches_only_rows_after_projection(monkeypatch):
    projection_created_at = datetime(2026, 6, 15, 12, 3, tzinfo=timezone.utc)
    projection = SimpleNamespace(
        created_at=projection_created_at,
        payload_json={
            "schema": module._PROJECTION_SCHEMA,
            "items": [
                {"role": "user", "content": "projected old question"},
                {"role": "assistant", "content": "projected old answer"},
            ],
            "covered_turn_ids": ["turn-1"],
        },
    )
    messages = [
        _message(role="user", content="old question", turn_id="turn-1", minute=1),
        _message(role="assistant", content="old answer", turn_id="turn-1", minute=2),
        _message(role="user", content="new question", turn_id="turn-2", minute=4),
        _message(role="assistant", content="new answer", turn_id="turn-2", minute=5),
        _message(role="user", content="current prompt", turn_id="turn-3", minute=6),
    ]
    db = _FakeDb(projection=projection)
    appended = []
    repository_holder = {}

    def _repository(fake_db):
        repository = _FakeRepository(fake_db, messages, appended)
        repository_holder["repository"] = repository
        return repository

    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "ChatHistoryRepository", _repository)

    session = module.DurableChatHistorySession(
        session_id="session-1",
        user_id="user-1",
        current_turn_id="turn-3",
    )

    assert asyncio.run(session.get_items()) == [
        {"role": "user", "content": "projected old question"},
        {"role": "assistant", "content": "projected old answer"},
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "new answer"},
    ]
    assert repository_holder["repository"].list_calls == [
        {
            "session_id": "session-1",
            "user_auth_sub": "user-1",
            "chat_kind": module.ASSISTANT_CHAT_KIND,
            "limit": module.MAX_MESSAGE_PAGE_SIZE,
            "cursor": None,
            "excluded_message_types": {module.CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE},
            "after_created_at": projection_created_at,
        }
    ]


def test_durable_session_add_items_writes_projection_without_double_writing_current_user(monkeypatch):
    messages = [
        _message(role="user", content="old question", turn_id="turn-1", minute=1),
        _message(role="assistant", content="old answer", turn_id="turn-1", minute=2),
        _message(role="user", content="current prompt", turn_id="turn-2", minute=3),
    ]
    db = _FakeDb()
    appended = []
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        module,
        "ChatHistoryRepository",
        lambda fake_db: _FakeRepository(fake_db, messages, appended),
    )

    session = module.DurableChatHistorySession(
        session_id="session-1",
        user_id="user-1",
        current_turn_id="turn-2",
    )
    asyncio.run(
        session.add_items(
            [
                {"role": "user", "content": "current prompt"},
                {
                    "type": "function_call_output",
                    "call_id": "call-raw",
                    "output": {"large_raw_payload": "must not be replayed"},
                },
                {"role": "assistant", "content": "current answer"},
            ]
        )
    )

    assert db.commits == 1
    assert len(appended) == 1
    payload = appended[0]["payload_json"]
    assert appended[0]["message_type"] == module.CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE
    assert payload["covered_turn_ids"] == ["turn-2"]
    assert payload["items"] == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "current prompt"},
        {"role": "assistant", "content": "current answer"},
    ]


def test_standard_chat_compaction_trigger_uses_env_threshold(monkeypatch):
    monkeypatch.setenv("STANDARD_CHAT_CONTEXT_TOKEN_BUDGET", "100")
    monkeypatch.setenv("STANDARD_CHAT_COMPACTION_THRESHOLD_PERCENT", "50")

    assert not module.should_compact_standard_chat_context(
        {"session_items": [{"role": "user", "content": "tiny"}]}
    )
    assert module.should_compact_standard_chat_context(
        {"session_items": [{"role": "user", "content": "x" * 300}]}
    )


def test_standard_chat_compaction_trigger_rejects_malformed_sdk_context():
    with pytest.raises(TypeError, match="session_items"):
        module.should_compact_standard_chat_context({"session_items": {"role": "user"}})
