"""Unit tests for Agent Studio chat history tool registration and dispatch."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

import src.api.agent_studio as api_module
from src.lib.agent_studio.models import ChatContext
from src.lib.chat_history_repository import (
    ALL_CHAT_KINDS_SENTINEL,
    AGENT_STUDIO_CHAT_KIND,
    ASSISTANT_CHAT_KIND,
    ChatMessageCursor,
    ChatMessagePage,
    ChatMessageRecord,
    ChatSessionCursor,
    ChatSessionDetail,
    ChatSessionPage,
    ChatSessionRecord,
    encode_chat_session_cursor,
)
from src.lib.openai_agents.chat_compaction_session import CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE


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


def _message_record(
    *,
    session_id: str,
    turn_id: str,
    role: str,
    content: str,
    chat_kind: str = AGENT_STUDIO_CHAT_KIND,
    message_type: str = "text",
) -> ChatMessageRecord:
    timestamp = datetime(2026, 4, 23, 3, 16, tzinfo=timezone.utc)
    return ChatMessageRecord(
        message_id=uuid4(),
        session_id=session_id,
        chat_kind=chat_kind,
        turn_id=turn_id,
        role=role,
        message_type=message_type,
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
    assert "cursor" in list_schema["properties"]

    search_schema = tools_by_name["search_chat_history"]["input_schema"]
    assert search_schema["required"] == ["query", "chat_kind"]
    assert search_schema["properties"]["chat_kind"]["enum"] == [
        ASSISTANT_CHAT_KIND,
        AGENT_STUDIO_CHAT_KIND,
        ALL_CHAT_KINDS_SENTINEL,
    ]
    assert "cursor" in search_schema["properties"]

    conversation_schema = tools_by_name["get_chat_conversation"]["input_schema"]
    assert conversation_schema["required"] == ["session_id"]
    assert {"session_id", "cursor", "limit"} == set(conversation_schema["properties"])

    turn_schema = tools_by_name["get_chat_turn"]["input_schema"]
    assert turn_schema["required"] == ["session_id", "turn_id"]
    assert {
        "session_id",
        "turn_id",
        "cursor",
        "limit",
        "message_id",
        "field",
        "field_hash",
        "start",
        "max_chars",
    } == set(turn_schema["properties"])


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
    assert result["sessions"][0]["title"] == "title-session-1"
    assert "generated_title" not in result["sessions"][0]
    assert "effective_title" not in result["sessions"][0]
    assert result["complete"] is True
    assert captured["count_kwargs"] == {
        "user_auth_sub": "auth-sub-123",
        "chat_kind": "all",
    }
    assert captured["list_kwargs"] == {
        "user_auth_sub": "auth-sub-123",
        "chat_kind": "all",
        "limit": 3,
        "cursor": None,
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
        "cursor": None,
    }


@pytest.mark.parametrize(
    ("tool_name", "query"),
    [
        ("list_recent_chats", None),
        ("search_chat_history", "maximum title"),
    ],
)
def test_chat_session_tools_bound_maximum_titles_and_replay_all_pages(
    monkeypatch,
    tool_name,
    query,
):
    base_timestamp = datetime(2026, 4, 23, 3, 15, tzinfo=timezone.utc)
    sessions = [
        ChatSessionRecord(
            session_id=f"session-{index:02d}",
            user_auth_sub="auth-sub-max-title",
            title='"' * 255,
            generated_title="g" * 255,
            active_document_id=None,
            created_at=base_timestamp - timedelta(minutes=index),
            updated_at=base_timestamp - timedelta(minutes=index),
            last_message_at=base_timestamp - timedelta(minutes=index),
            deleted_at=None,
            chat_kind=AGENT_STUDIO_CHAT_KIND,
        )
        for index in range(25)
    ]

    class _FakeRepository:
        def __init__(self, _db):
            pass

        def count_sessions(self, **kwargs):
            assert kwargs["user_auth_sub"] == "auth-sub-max-title"
            assert kwargs["chat_kind"] == AGENT_STUDIO_CHAT_KIND
            assert kwargs.get("query") == query
            return len(sessions)

        @staticmethod
        def _page(*, limit, cursor, ranked):
            start = 0
            if cursor is not None:
                start = next(
                    index + 1
                    for index, session in enumerate(sessions)
                    if session.session_id == cursor.session_id
                )
            items = sessions[start : start + limit]
            item_cursors = [
                ChatSessionCursor(
                    recent_activity_at=item.recent_activity_at,
                    session_id=item.session_id,
                    relevance=1.0 if ranked else None,
                )
                for item in items
            ]
            has_more = start + len(items) < len(sessions)
            return ChatSessionPage(
                items=items,
                next_cursor=item_cursors[-1] if has_more and item_cursors else None,
                item_cursors=item_cursors if ranked else None,
            )

        def list_sessions(self, **kwargs):
            assert kwargs["chat_kind"] == AGENT_STUDIO_CHAT_KIND
            return self._page(
                limit=kwargs["limit"],
                cursor=kwargs["cursor"],
                ranked=False,
            )

        def search_sessions_ranked(self, **kwargs):
            assert kwargs["chat_kind"] == AGENT_STUDIO_CHAT_KIND
            assert kwargs["query"] == query
            return self._page(
                limit=kwargs["limit"],
                cursor=kwargs["cursor"],
                ranked=True,
            )

        def get_session_detail(self, **kwargs):
            session = next(
                item for item in sessions if item.session_id == kwargs["session_id"]
            )
            return ChatSessionDetail(
                session=session,
                messages=[],
                next_message_cursor=None,
            )

        def count_messages(self, **kwargs):
            return 0

    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(api_module, "ChatHistoryRepository", _FakeRepository)

    tool_input = {
        "chat_kind": AGENT_STUDIO_CHAT_KIND,
        "limit": 25,
    }
    if query is not None:
        tool_input["query"] = query

    returned_ids = []
    returned_ranges = []
    result = None
    next_call = {"tool": tool_name, "arguments": tool_input}
    while next_call is not None:
        current_call = next_call
        result = asyncio.run(
            api_module._handle_tool_call(
                tool_name=current_call["tool"],
                tool_input=current_call["arguments"],
                context=None,
                user_email="dev@example.org",
                user_auth_sub="auth-sub-max-title",
                messages=[],
            )
        )
        assert result["success"] is True
        assert len(api_module._serialize_provider_tool_result(result)) <= 12000
        provider_content = api_module._provider_tool_result_content(
            tool_name=current_call["tool"],
            tool_input=current_call["arguments"],
            tool_result=result,
            session_id="agent-studio-session",
            turn_id="turn-max-title",
        )
        assert api_module.json.loads(provider_content) == result
        assert "compacted_tool_result" not in provider_content
        assert result["total_sessions"] == 25
        assert all("generated_title" not in item for item in result["sessions"])
        assert all("effective_title" not in item for item in result["sessions"])
        assert all(item["title"] == '"' * 255 for item in result["sessions"])
        returned_ids.extend(item["session_id"] for item in result["sessions"])
        returned_ranges.append(result["returned_range"])
        next_call = result["next_call"]
        if next_call is not None:
            assert next_call["arguments"]["chat_kind"] == AGENT_STUDIO_CHAT_KIND
            assert next_call["arguments"].get("query") == query
            assert next_call["arguments"]["limit"] == 25

    assert returned_ids == [session.session_id for session in sessions]
    assert returned_ranges[0]["start"] == 0
    assert returned_ranges[-1]["end"] == 25
    assert all(
        current["end"] == following["start"]
        for current, following in zip(returned_ranges, returned_ranges[1:])
    )
    assert len(returned_ranges) > 1
    assert result is not None
    assert result["complete"] is True

    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "1800")
    narrow_ids = []
    narrow_page_sizes = []
    next_call = {"tool": tool_name, "arguments": tool_input}
    while next_call is not None:
        narrow_result = asyncio.run(
            api_module._handle_tool_call(
                tool_name=next_call["tool"],
                tool_input=next_call["arguments"],
                context=None,
                user_email="dev@example.org",
                user_auth_sub="auth-sub-max-title",
                messages=[],
            )
        )
        assert narrow_result["success"] is True
        assert len(api_module._serialize_provider_tool_result(narrow_result)) <= 1800
        assert narrow_result["sessions"]
        narrow_page_sizes.append(len(narrow_result["sessions"]))
        narrow_ids.extend(item["session_id"] for item in narrow_result["sessions"])
        next_call = narrow_result["next_call"]
    assert narrow_ids == [session.session_id for session in sessions]
    assert 1 in narrow_page_sizes

    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    detail = asyncio.run(
        api_module._handle_tool_call(
            tool_name="get_chat_conversation",
            tool_input={"session_id": sessions[0].session_id},
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-max-title",
            messages=[],
        )
    )
    assert detail["session"]["title"] == '"' * 255
    assert detail["session"]["generated_title"] == "g" * 255
    assert "effective_title" not in detail["session"]


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


def test_handle_tool_call_list_recent_chats_rejects_ranked_search_cursor(monkeypatch):
    class _FakeRepository:
        def __init__(self, _db):
            pass

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(api_module, "ChatHistoryRepository", _FakeRepository)
    cursor = encode_chat_session_cursor(
        ChatSessionCursor(
            recent_activity_at=datetime(2026, 4, 23, 3, 15, tzinfo=timezone.utc),
            session_id="session-ranked",
            relevance=0.75,
        )
    )

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="list_recent_chats",
            tool_input={"chat_kind": "all", "cursor": cursor},
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-1",
            messages=[],
        )
    )

    assert result == {
        "success": False,
        "error": "ranked search cursor cannot be used for list_recent_chats",
    }


def test_chat_history_limit_uses_environment_bounded_default(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_CHAT_HISTORY_PAGE_SIZE", "5")

    assert api_module._resolve_chat_history_limit({}) == 5
    with pytest.raises(ValueError, match="from 1 to 5"):
        api_module._resolve_chat_history_limit({"limit": 6})


def test_handle_tool_call_get_chat_conversation_rejects_malformed_cursor():
    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="get_chat_conversation",
            tool_input={"session_id": "session-1", "cursor": "not-a-valid-cursor"},
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-1",
            messages=[],
        )
    )

    assert result == {"success": False, "error": "Invalid message cursor"}


def test_handle_tool_call_get_chat_conversation_pages_summaries_and_hides_compaction_rows(
    monkeypatch,
):
    captured: list[dict[str, object]] = []
    hidden_types = {CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE}
    visible_user = _message_record(
        session_id="assistant-session-1",
        turn_id="turn-1",
        role="user",
        content="Visible question",
        chat_kind=ASSISTANT_CHAT_KIND,
    )
    visible_assistant = _message_record(
        session_id="assistant-session-1",
        turn_id="turn-1",
        role="assistant",
        content="Visible answer",
        chat_kind=ASSISTANT_CHAT_KIND,
    )

    class _FakeRepository:
        def __init__(self, _db):
            pass

        def get_session_detail(self, **kwargs):
            captured.append(kwargs)
            assert kwargs["excluded_message_types"] == hidden_types
            if kwargs["message_cursor"] is not None:
                return ChatSessionDetail(
                    session=_session_record(
                        session_id=kwargs["session_id"],
                        chat_kind=ASSISTANT_CHAT_KIND,
                    ),
                    messages=[visible_assistant],
                    next_message_cursor=None,
                )
            return ChatSessionDetail(
                session=_session_record(
                    session_id=kwargs["session_id"],
                    chat_kind=ASSISTANT_CHAT_KIND,
                ),
                messages=[visible_user],
                next_message_cursor=ChatMessageCursor(
                    created_at=visible_user.created_at,
                    message_id=visible_user.message_id,
                ),
            )

        def count_messages(self, **kwargs):
            assert kwargs["excluded_message_types"] == hidden_types
            return 1 if kwargs.get("through_cursor") is not None else 2

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(api_module, "ChatHistoryRepository", _FakeRepository)

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="get_chat_conversation",
            tool_input={"session_id": "assistant-session-1"},
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-conversation",
            messages=[],
        )
    )

    assert result["success"] is True
    assert result["total_message_count"] == 2
    assert result["returned_range"] == {"start": 0, "end": 1}
    assert result["messages"][0]["fields"]["content"]["length"] == len("Visible question")
    assert "content" not in result["messages"][0]
    assert result["complete"] is False

    next_call = result["next_call"]
    replay = asyncio.run(
        api_module._handle_tool_call(
            tool_name=next_call["tool"],
            tool_input=next_call["arguments"],
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-conversation",
            messages=[],
        )
    )
    assert replay["returned_range"] == {"start": 1, "end": 2}
    assert replay["complete"] is True
    assert replay["next_call"] is None
    assert len(captured) == 2


def test_handle_tool_call_get_chat_turn_chunks_large_exact_fields_with_replayable_calls(
    monkeypatch,
):
    hidden_types = {CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE}
    content = "quoted \\\"value\\\" and slash \\\\ " * 600
    message = _message_record(
        session_id="agent-studio-session-1",
        turn_id="opus-turn-2",
        role="assistant",
        content=content,
    )
    message = ChatMessageRecord(
        **{**message.__dict__, "payload_json": {"z": content, "a": [1, 2, 3]}}
    )

    class _FakeRepository:
        def __init__(self, _db):
            pass

        def get_session(self, **kwargs):
            return _session_record(
                session_id=kwargs["session_id"],
                chat_kind=AGENT_STUDIO_CHAT_KIND,
            )

        def list_messages_for_turn_page(self, **kwargs):
            assert kwargs["excluded_message_types"] == hidden_types
            return ChatMessagePage(items=[message], next_cursor=None)

        def count_messages(self, **kwargs):
            assert kwargs["excluded_message_types"] == hidden_types
            return 1

        def get_message_by_id(self, **kwargs):
            assert kwargs["excluded_message_types"] == hidden_types
            return message if kwargs["message_id"] == message.message_id else None

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
    assert result["total_message_count"] == 1
    assert "content" not in result["messages"][0]
    assert result["messages"][0]["fields"]["content"]["length"] == len(content)
    assert result["messages"][0]["fields"]["content"]["complete"] is False

    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "1200")
    stale_hash_call = {
        **result["messages"][0]["fields"]["content"]["next_call"],
        "arguments": {
            **result["messages"][0]["fields"]["content"]["next_call"]["arguments"],
            "field_hash": "0" * 64,
        },
    }
    stale_hash_result = asyncio.run(
        api_module._handle_tool_call(
            tool_name=stale_hash_call["tool"],
            tool_input=stale_hash_call["arguments"],
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-turn",
            messages=[],
        )
    )
    assert stale_hash_result["success"] is False
    assert "changed" in stale_hash_result["error"]
    assert stale_hash_result["current_hash"] == result["messages"][0]["fields"]["content"]["sha256"]

    for field_name in ("content", "payload_json"):
        next_call = result["messages"][0]["fields"][field_name]["next_call"]
        chunks = []
        while next_call is not None:
            chunk_result = asyncio.run(
                api_module._handle_tool_call(
                    tool_name=next_call["tool"],
                    tool_input=next_call["arguments"],
                    context=None,
                    user_email="dev@example.org",
                    user_auth_sub="auth-sub-turn",
                    messages=[],
                )
            )
            assert len(api_module._serialize_provider_tool_result(chunk_result)) <= 1200
            assert chunk_result["returned_range"]["end"] > chunk_result["returned_range"]["start"]
            chunks.append(chunk_result["chunk"])
            next_call = chunk_result["next_call"]
        reconstructed = "".join(chunks)
        expected = content if field_name == "content" else api_module.json.dumps(
            message.payload_json,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert reconstructed == expected


def test_handle_tool_call_get_chat_turn_same_turn_returns_only_persisted_rows(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "500")
    content = api_module._provider_tool_result_content(
        tool_name="get_trace_payload",
        tool_input={"trace_id": "trace-1", "payload_id": "payload-1"},
        tool_result={
            "status": "success",
            "data": {"value": "large value" * 1000},
        },
        session_id="agent-studio-session-1",
        turn_id="opus-turn-current",
    )
    compact = json.loads(content)
    turn_recall = compact["recall"]["turn"]
    assert len(content) <= 500
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")

    class _FakeRepository:
        def __init__(self, _db):
            pass

        def get_session(self, **kwargs):
            return _session_record(
                session_id=kwargs["session_id"],
                chat_kind=AGENT_STUDIO_CHAT_KIND,
            )

        def list_messages_for_turn_page(self, **kwargs):
            assert kwargs["excluded_message_types"] == {CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE}
            return ChatMessagePage(
                items=[_message_record(
                    session_id=kwargs["session_id"],
                    turn_id=kwargs["turn_id"],
                    role="user",
                    content="Current same-turn request already persisted.",
                )],
                next_cursor=None,
            )

        def count_messages(self, **kwargs):
            return 1

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(api_module, "ChatHistoryRepository", _FakeRepository)

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name=turn_recall["tool"],
            tool_input=turn_recall,
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-turn",
            messages=[],
        )
    )

    assert result["success"] is True
    assert result["total_message_count"] == 1
    assert "in-flight same-turn raw tool result exists only" in result["durability"]
    assert result["messages"][0]["fields"]["content"]["length"] == len(
        "Current same-turn request already persisted."
    )
