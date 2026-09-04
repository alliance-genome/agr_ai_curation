"""Contract coverage for Agent Studio chat history tool registration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from agents import FunctionTool, ToolSearchTool

from src.api import agent_studio as api_module
from src.lib.chat_history_repository import AGENT_STUDIO_CHAT_KIND, ALL_CHAT_KINDS_SENTINEL


HISTORY_TOOLS_SESSION_ID = "agent-studio-history-tools-session"
HISTORY_TOOLS_TURN_ID = "agent-studio-history-tools-turn-1"


def _consume_sse_events(stream_response) -> list[dict]:
    events: list[dict] = []
    for line in stream_response.iter_lines():
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[6:]))
    return events


def test_agent_studio_chat_endpoint_registers_chat_history_tools_on_the_wire(
    contract_client,
    chat_contract_auth_headers,
    monkeypatch,
):
    captured: dict[str, Any] = {}

    monkeypatch.setattr(api_module, "get_api_key", lambda _provider: "test-key")
    monkeypatch.setattr(api_module, "_build_opus_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr(api_module, "set_workflow_user_context", lambda **_kwargs: None)
    monkeypatch.setattr(api_module, "clear_workflow_user_context", lambda: None)
    monkeypatch.setattr(api_module, "set_current_flow_context", lambda _context: None)
    monkeypatch.setattr(api_module, "clear_current_flow_context", lambda: None)
    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=1),
    )

    def _fake_get_db():
        yield SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(api_module, "get_db", _fake_get_db)
    monkeypatch.setattr(
        api_module,
        "_prepare_agent_studio_turn",
        lambda *, request, **_kwargs: api_module.PreparedAgentStudioTurn(
            session_id=HISTORY_TOOLS_SESSION_ID,
            turn_id=HISTORY_TOOLS_TURN_ID,
            user_message=request.messages[-1].content,
            requested_context_session_id=request.context.session_id if request.context else None,
            user_turn_created=False,
        ),
    )
    monkeypatch.setattr(
        api_module,
        "_persist_completed_agent_studio_turn",
        lambda **kwargs: api_module.ChatMessageRecord(
            message_id=uuid4(),
            session_id=kwargs["session_id"],
            chat_kind=AGENT_STUDIO_CHAT_KIND,
            turn_id=kwargs["turn_id"],
            role="assistant",
            message_type="text",
            content=kwargs["assistant_message"],
            payload_json=kwargs["payload_json"],
            trace_id=kwargs["trace_id"],
            created_at=datetime(2026, 4, 23, 4, 30, tzinfo=timezone.utc),
        ),
    )
    async def _fake_openai_runtime(**kwargs):
        captured["tools"] = kwargs["tools"]
        kwargs["state"].assistant_text_parts.append("History tools ready")
        kwargs["state"].response_id = "resp-history-1"
        yield {"type": "TEXT_DELTA", "delta": "History tools ready"}

    monkeypatch.setattr(api_module, "stream_agent_studio_run", _fake_openai_runtime)

    with contract_client.stream(
        "POST",
        "/api/agent-studio/chat",
        headers=chat_contract_auth_headers,
        json={
            "messages": [{"role": "user", "content": "Show me my recent sessions"}],
            "context": {"active_tab": "agents"},
        },
    ) as response:
        events = _consume_sse_events(response)

    assert response.status_code == 200, response.text
    # PROVIDER_CONTEXT_PREFLIGHT is token-budget observability emitted before the
    # provider call (96ab9632); filter it to assert the meaningful stream.
    assert any(event["type"] == "PROVIDER_CONTEXT_PREFLIGHT" for event in events)
    assert [
        event["type"] for event in events if event["type"] != "PROVIDER_CONTEXT_PREFLIGHT"
    ] == ["TEXT_DELTA", "DONE"]

    assert sum(isinstance(tool, ToolSearchTool) for tool in captured["tools"]) == 1
    tools_by_name = {
        tool.name.rsplit(".", 1)[-1]: tool
        for tool in captured["tools"]
        if isinstance(tool, FunctionTool)
    }
    assert {
        "list_recent_chats",
        "search_chat_history",
        "get_chat_conversation",
        "get_chat_turn",
    } <= set(tools_by_name)
    assert tools_by_name["list_recent_chats"].defer_loading is True
    assert tools_by_name["list_recent_chats"].params_json_schema["required"] == ["chat_kind"]
    assert tools_by_name["list_recent_chats"].params_json_schema["properties"]["chat_kind"]["enum"] == [
        "assistant_chat",
        "agent_studio",
        ALL_CHAT_KINDS_SENTINEL,
    ]
    assert tools_by_name["search_chat_history"].params_json_schema["required"] == [
        "query",
        "chat_kind",
    ]
    assert tools_by_name["get_chat_conversation"].params_json_schema["required"] == ["session_id"]
    assert tools_by_name["get_chat_turn"].params_json_schema["required"] == ["session_id", "turn_id"]
    assert set(tools_by_name["get_chat_conversation"].params_json_schema["properties"]) == {
        "session_id",
        "cursor",
        "limit",
    }
    assert set(tools_by_name["get_chat_turn"].params_json_schema["properties"]) == {
        "session_id",
        "turn_id",
        "cursor",
        "limit",
        "message_id",
        "field",
        "field_hash",
        "start",
        "max_chars",
    }
