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


def test_chat_history_tools_are_registered_for_opus():
    tools = api_module._get_all_opus_tools(ChatContext(active_tab="agents"))
    tools_by_name = {tool["name"]: tool for tool in tools}

    assert {"list_recent_chats", "search_chat_history", "get_chat_conversation"} <= set(
        api_module._COMMON_TOOLS
    )
    assert {"list_recent_chats", "search_chat_history", "get_chat_conversation"} <= set(
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
