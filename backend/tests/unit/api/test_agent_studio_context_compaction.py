"""Tests for Agent Studio Opus provider-context compaction helpers."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import src.api.agent_studio as api_module
from src.lib.chat_history_repository import AGENT_STUDIO_CHAT_KIND


async def _consume_stream(response) -> list[dict[str, Any]]:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)

    events: list[dict[str, Any]] = []
    for line in "".join(chunks).splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


class _FakeSuccessfulStream:
    def __init__(self, events: list[object], final_message: object):
        self._events = list(events)
        self._final_message = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def get_final_message(self):
        return self._final_message


class _FakeMessagesApi:
    def __init__(self, captured: dict[str, Any]):
        self._captured = captured

    def stream(self, **kwargs):
        api_calls = self._captured.setdefault("api_calls", [])
        api_calls.append(kwargs)
        if len(api_calls) == 1:
            return _FakeSuccessfulStream(
                events=[],
                final_message=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="tool_use",
                            id="toolu_big_1",
                            name="get_trace_payload",
                            input={
                                "trace_id": "trace-1",
                                "payload_id": "observation:abc:output",
                                "max_chars": 0,
                            },
                        )
                    ],
                    stop_reason="tool_use",
                ),
            )

        self._captured["second_call_messages"] = kwargs["messages"]
        return _FakeSuccessfulStream(
            events=[
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(text="I fetched the compacted result."),
                )
            ],
            final_message=SimpleNamespace(
                content=[SimpleNamespace(type="text", text="I fetched the compacted result.")],
                stop_reason="end_turn",
            ),
        )


class _FakeAnthropicClient:
    def __init__(self, captured: dict[str, Any]):
        self.beta = SimpleNamespace(messages=_FakeMessagesApi(captured))


def test_large_tool_result_is_compacted_for_provider_continuation(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "500")

    large_value = "payload chunk " * 500
    content = api_module._provider_tool_result_content(
        tool_name="get_trace_payload",
        tool_input={
            "trace_id": "trace-1",
            "payload_id": "observation:abc:output",
            "start": 0,
            "max_chars": 0,
        },
        tool_result={
            "status": "success",
            "trace_id": "trace-1",
            "data": {
                "payload_id": "observation:abc:output",
                "value": large_value,
                "next_start": None,
            },
        },
        session_id="agent-studio-session-1",
        turn_id="opus-turn-4-abc123",
    )

    compact = json.loads(content)

    assert compact["status"] == "compacted_tool_result"
    assert compact["tool_result_compacted"] is True
    assert compact["raw_result_json_chars"] > 500
    assert len(content) < len(large_value)
    assert large_value not in content
    assert compact["recall"]["chat_turn"] == {
        "tool": "get_chat_turn",
        "session_id": "agent-studio-session-1",
        "turn_id": "opus-turn-4-abc123",
        "purpose": "Reload durable current-session turn text and tool-call summaries after provider context editing.",
    }
    assert compact["recall"]["trace_payloads"]["payload_ids"] == [
        "observation:abc:output"
    ]
    assert compact["recall"]["repeat_or_narrow_tool"]["input"]["payload_id"] == (
        "observation:abc:output"
    )


def test_small_tool_result_stays_inline_for_provider_continuation(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "500")

    tool_result = {"success": True, "current_prompt": "Small refreshed prompt."}

    content = api_module._provider_tool_result_content(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "main"},
        tool_result=tool_result,
        session_id="agent-studio-session-1",
        turn_id="opus-turn-1",
    )

    assert json.loads(content) == tool_result


def test_streaming_tool_loop_sends_compact_large_result_to_provider(monkeypatch):
    captured: dict[str, Any] = {}
    large_value = "payload chunk " * 500

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "500")
    monkeypatch.setattr(
        api_module,
        "_resolve_prompt_explorer_model",
        lambda: ("claude-sonnet-test", "Claude Sonnet Test"),
    )
    monkeypatch.setattr(api_module, "_build_opus_system_prompt", lambda **_kwargs: "system prompt")
    monkeypatch.setattr(api_module, "_get_all_opus_tools", lambda _context=None: [])
    monkeypatch.setattr(api_module, "set_workflow_user_context", lambda **_kwargs: None)
    monkeypatch.setattr(api_module, "clear_workflow_user_context", lambda: None)
    monkeypatch.setattr(api_module, "set_current_flow_context", lambda _context: None)
    monkeypatch.setattr(api_module, "clear_current_flow_context", lambda: None)
    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(api_module, "get_db", lambda: iter([SimpleNamespace(close=lambda: None)]))
    monkeypatch.setattr(
        api_module,
        "_prepare_agent_studio_turn",
        lambda *, request, **_kwargs: api_module.PreparedAgentStudioTurn(
            session_id="agent-studio-session-1",
            turn_id="opus-turn-4-abc123",
            user_message=request.messages[-1].content,
            requested_context_session_id=None,
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
            created_at=datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc),
        ),
    )

    async def _fake_handle_tool_call(**_kwargs):
        return {
            "status": "success",
            "trace_id": "trace-1",
            "data": {
                "payload_id": "observation:abc:output",
                "value": large_value,
            },
        }

    monkeypatch.setattr(api_module, "_handle_tool_call", _fake_handle_tool_call)
    monkeypatch.setattr(
        api_module.anthropic,
        "AsyncAnthropic",
        lambda api_key: _FakeAnthropicClient(captured),
    )

    request = api_module.ChatRequest(
        messages=[api_module.ChatMessage(role="user", content="Fetch the large payload")],
        context=api_module.ChatContext(active_tab="agents"),
    )

    response = asyncio.run(
        api_module.chat_with_opus(
            request,
            user={"sub": "auth-sub-1", "email": "dev@example.org"},
        )
    )
    events = asyncio.run(_consume_stream(response))

    tool_result_events = [event for event in events if event["type"] == "TOOL_RESULT"]
    assert tool_result_events[0]["result"]["data"]["value"] == large_value

    second_messages = captured["second_call_messages"]
    tool_result_content = second_messages[-1]["content"][0]["content"]
    compact = json.loads(tool_result_content)

    assert compact["status"] == "compacted_tool_result"
    assert compact["recall"]["chat_turn"]["turn_id"] == "opus-turn-4-abc123"
    assert compact["recall"]["trace_payloads"]["payload_ids"] == [
        "observation:abc:output"
    ]
    assert large_value not in tool_result_content
