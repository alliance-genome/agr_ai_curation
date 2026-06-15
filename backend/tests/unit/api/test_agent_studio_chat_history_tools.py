"""Unit tests for Agent Studio chat history tool registration and dispatch."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import src.api.agent_studio as api_module
from src.lib.agent_studio.models import ChatContext
from src.lib.chat_history_repository import (
    ALL_CHAT_KINDS_SENTINEL,
    AGENT_STUDIO_CHAT_KIND,
    ASSISTANT_CHAT_KIND,
    ChatMessageRecord,
    ChatSessionPage,
    ChatSessionRecord,
)


def _session_record(*, session_id: str, chat_kind: str) -> ChatSessionRecord:
    timestamp = datetime(2026, 4, 23, 3, 15, tzinfo=timezone.utc)
    return ChatSessionRecord(
        session_id=session_id,
        user_auth_sub="auth-sub-1",
        title=f"title-{session_id}",
        generated_title=None,
        active_document_id=None,
        created_at=timestamp,
        updated_at=timestamp,
        last_message_at=timestamp,
        deleted_at=None,
        chat_kind=chat_kind,
    )


def _message_record(*, session_id: str, turn_id: str, role: str, content: str) -> ChatMessageRecord:
    timestamp = datetime(2026, 4, 23, 3, 16, tzinfo=timezone.utc)
    return ChatMessageRecord(
        message_id=uuid4(),
        session_id=session_id,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        turn_id=turn_id,
        role=role,
        message_type="text",
        content=content,
        payload_json=None,
        trace_id=None,
        created_at=timestamp,
    )


def test_chat_history_tools_are_registered_for_opus():
    tools = api_module._get_all_opus_tools(ChatContext(active_tab="agents"))
    tools_by_name = {tool["name"]: tool for tool in tools}

    assert {"list_recent_chats", "search_chat_history", "get_chat_conversation", "get_chat_turn"} <= set(
        api_module._COMMON_TOOLS
    )
    assert {"list_recent_chats", "search_chat_history", "get_chat_conversation", "get_chat_turn"} <= set(
        tools_by_name
    )

    list_schema = tools_by_name["list_recent_chats"]["input_schema"]
    assert list_schema["required"] == ["chat_kind"]
    assert list_schema["properties"]["chat_kind"]["enum"] == [
        ASSISTANT_CHAT_KIND,
        AGENT_STUDIO_CHAT_KIND,
        ALL_CHAT_KINDS_SENTINEL,
    ]

    search_schema = tools_by_name["search_chat_history"]["input_schema"]
    assert search_schema["required"] == ["query", "chat_kind"]
    assert search_schema["properties"]["chat_kind"]["enum"] == [
        ASSISTANT_CHAT_KIND,
        AGENT_STUDIO_CHAT_KIND,
        ALL_CHAT_KINDS_SENTINEL,
    ]

    conversation_schema = tools_by_name["get_chat_conversation"]["input_schema"]
    assert conversation_schema["required"] == ["session_id"]

    turn_schema = tools_by_name["get_chat_turn"]["input_schema"]
    assert turn_schema["required"] == ["session_id", "turn_id"]


def test_handle_tool_call_list_recent_chats_forwards_user_auth_sub(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeRepository:
        def __init__(self, _db):
            pass

        def count_sessions(self, **kwargs):
            captured["count_kwargs"] = kwargs
            return 1

        def list_sessions(self, **kwargs):
            captured["list_kwargs"] = kwargs
            return ChatSessionPage(
                items=[_session_record(session_id="session-1", chat_kind=ASSISTANT_CHAT_KIND)],
                next_cursor=None,
            )

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(api_module, "ChatHistoryRepository", _FakeRepository)

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="list_recent_chats",
            tool_input={"chat_kind": "all", "limit": 3},
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-123",
            messages=[],
        )
    )

    assert result["success"] is True
    assert result["total_sessions"] == 1
    assert result["sessions"][0]["session_id"] == "session-1"
    assert captured["count_kwargs"] == {
        "user_auth_sub": "auth-sub-123",
        "chat_kind": "all",
    }
    assert captured["list_kwargs"] == {
        "user_auth_sub": "auth-sub-123",
        "chat_kind": "all",
        "limit": 3,
    }


def test_handle_tool_call_search_chat_history_uses_ranked_repository_search(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeRepository:
        def __init__(self, _db):
            pass

        def count_sessions(self, **kwargs):
            captured["count_kwargs"] = kwargs
            return 2

        def search_sessions_ranked(self, **kwargs):
            captured["search_kwargs"] = kwargs
            return ChatSessionPage(
                items=[
                    _session_record(session_id="session-2", chat_kind=AGENT_STUDIO_CHAT_KIND),
                    _session_record(session_id="session-1", chat_kind=ASSISTANT_CHAT_KIND),
                ],
                next_cursor=None,
            )

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(api_module, "ChatHistoryRepository", _FakeRepository)

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="search_chat_history",
            tool_input={"query": "tp53 OR dna", "chat_kind": "all", "limit": 2},
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-456",
            messages=[],
        )
    )

    assert result["success"] is True
    assert [session["session_id"] for session in result["sessions"]] == ["session-2", "session-1"]
    assert captured["count_kwargs"] == {
        "user_auth_sub": "auth-sub-456",
        "chat_kind": "all",
        "query": "tp53 OR dna",
    }
    assert captured["search_kwargs"] == {
        "user_auth_sub": "auth-sub-456",
        "chat_kind": "all",
        "query": "tp53 OR dna",
        "limit": 2,
    }


def test_handle_tool_call_search_chat_history_requires_query():
    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="search_chat_history",
            tool_input={"query": "   ", "chat_kind": "all"},
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-1",
            messages=[],
        )
    )

    assert result["success"] is False
    assert result["error"] == "Missing required parameter: query"


def test_handle_tool_call_get_chat_turn_loads_current_session_turn(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeRepository:
        def __init__(self, _db):
            pass

        def get_session(self, **kwargs):
            captured["get_session_kwargs"] = kwargs
            return _session_record(
                session_id=kwargs["session_id"],
                chat_kind=AGENT_STUDIO_CHAT_KIND,
            )

        def list_messages_for_turn(self, **kwargs):
            captured["turn_kwargs"] = kwargs
            return [
                _message_record(
                    session_id=kwargs["session_id"],
                    turn_id=kwargs["turn_id"],
                    role="user",
                    content="Earlier compacted question",
                ),
                _message_record(
                    session_id=kwargs["session_id"],
                    turn_id=kwargs["turn_id"],
                    role="assistant",
                    content="Earlier answer with tool-call summary",
                ),
            ]

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(api_module, "ChatHistoryRepository", _FakeRepository)

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="get_chat_turn",
            tool_input={"session_id": "agent-studio-session-1", "turn_id": "opus-turn-2"},
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-turn",
            messages=[],
        )
    )

    assert result["success"] is True
    assert result["turn_id"] == "opus-turn-2"
    assert [message["role"] for message in result["messages"]] == ["user", "assistant"]
    assert captured["get_session_kwargs"] == {
        "session_id": "agent-studio-session-1",
        "user_auth_sub": "auth-sub-turn",
    }
    assert captured["turn_kwargs"] == {
        "session_id": "agent-studio-session-1",
        "user_auth_sub": "auth-sub-turn",
        "chat_kind": AGENT_STUDIO_CHAT_KIND,
        "turn_id": "opus-turn-2",
    }
