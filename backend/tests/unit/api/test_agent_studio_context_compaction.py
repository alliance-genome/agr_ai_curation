"""Tests for Agent Studio Opus provider-context compaction helpers."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

import src.api.agent_studio as api_module
import src.lib.agent_studio.flow_tools as flow_tools
from src.lib.chat_history_repository import (
    AGENT_STUDIO_CHAT_KIND,
    ChatMessageRecord,
    ChatSessionRecord,
)


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


class _RepeatedToolLoopMessagesApi:
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
                            id="toolu_inventory_1",
                            name="get_trace_payloads",
                            input={
                                "trace_id": "trace-1",
                            },
                        )
                    ],
                    stop_reason="tool_use",
                ),
            )
        if len(api_calls) == 2:
            self._captured["first_continuation_messages"] = kwargs["messages"]
            self._captured["first_provider_result"] = kwargs["messages"][-1]["content"][0][
                "content"
            ]
            return _FakeSuccessfulStream(
                events=[],
                final_message=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="tool_use",
                            id="toolu_payload_2",
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

        self._captured["second_continuation_messages"] = kwargs["messages"]
        self._captured["second_provider_result"] = kwargs["messages"][-1]["content"][0][
            "content"
        ]
        return _FakeSuccessfulStream(
            events=[
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(text="I recalled the exact payload."),
                )
            ],
            final_message=SimpleNamespace(
                content=[SimpleNamespace(type="text", text="I recalled the exact payload.")],
                stop_reason="end_turn",
            ),
        )


class _RepeatedToolLoopAnthropicClient:
    def __init__(self, captured: dict[str, Any]):
        self.beta = SimpleNamespace(messages=_RepeatedToolLoopMessagesApi(captured))


def _agent_studio_message(
    *,
    session_id: str,
    turn_id: str,
    role: str,
    content: str,
) -> ChatMessageRecord:
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
        created_at=datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc),
    )


def _agent_studio_session(*, session_id: str) -> ChatSessionRecord:
    timestamp = datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc)
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
        chat_kind=AGENT_STUDIO_CHAT_KIND,
    )


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
        "purpose": (
            "Reload durable transcript rows already persisted for this turn after "
            "provider context editing; same-turn tool-call summaries become "
            "durable only after the assistant turn completes."
        ),
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


def test_workshop_proposal_provider_projection_excludes_full_prompt(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    proposed_prompt = "Exact curator proposal.\n" * 1800
    prompt_hash = api_module._prompt_hash(proposed_prompt)
    tool_result = {
        "success": True,
        "approval_status": "pending_user_approval",
        "pending_user_approval": True,
        "proposal_id": f"main:{prompt_hash}",
        "target_prompt": "main",
        "target_group_id": None,
        "apply_mode": "replace",
        "proposed_prompt": proposed_prompt,
        "prompt_length": len(proposed_prompt),
        "prompt_hash": prompt_hash,
        "change_summary": "Clarified exact evidence requirements.",
        "message": "Prompt update proposal prepared. Awaiting curator approval in the UI.",
    }

    content = api_module._provider_tool_result_content(
        tool_name="update_workshop_prompt_draft",
        tool_input={"apply_mode": "replace", "updated_prompt": proposed_prompt},
        tool_result=tool_result,
        session_id="agent-studio-session-1",
        turn_id="opus-turn-1",
    )
    provider_result = json.loads(content)

    assert provider_result["contract_version"] == "workshop_prompt_proposal_ack.v1"
    assert provider_result["approval_status"] == "pending_user_approval"
    assert provider_result["proposal_id"] == f"main:{prompt_hash}"
    assert provider_result["prompt_length"] == len(proposed_prompt)
    assert provider_result["prompt_hash"] == prompt_hash
    assert provider_result["change_summary"] == "Clarified exact evidence requirements."
    assert "proposed_prompt" not in provider_result
    assert proposed_prompt not in content
    assert "exact proposed text remains" in provider_result["instruction"]
    assert len(content) < 12000


def test_targeted_edit_provider_projection_describes_retained_edits(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    proposed_prompt = "Server-derived proposal text.\n" * 1200
    prompt_hash = api_module._prompt_hash(proposed_prompt)

    tool_input = {
        "apply_mode": "targeted_edit",
        "edits": [{"old_text": "old", "new_text": "new"}],
    }
    tool_result = {
        "success": True,
        "approval_status": "pending_user_approval",
        "pending_user_approval": True,
        "proposal_id": f"main:{prompt_hash}",
        "target_prompt": "main",
        "target_group_id": None,
        "apply_mode": "targeted_edit",
        "proposed_prompt": proposed_prompt,
        "prompt_length": len(proposed_prompt),
        "prompt_hash": prompt_hash,
        "change_summary": "Applied one targeted edit.",
        "message": "Awaiting approval.",
    }

    inline_content = api_module._provider_tool_result_content(
        tool_name="update_workshop_prompt_draft",
        tool_input=tool_input,
        tool_result=tool_result,
        session_id="agent-studio-session-1",
        turn_id="opus-turn-1",
    )
    inline_result = json.loads(inline_content)

    assert "Only the authored targeted edits remain" in inline_result["instruction"]
    assert "refresh_workshop_prompt chunks" in inline_result["instruction"]
    assert "exact proposed text remains" not in inline_result["instruction"]
    assert proposed_prompt not in inline_content

    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "300")
    content = api_module._provider_tool_result_content(
        tool_name="update_workshop_prompt_draft",
        tool_input=tool_input,
        tool_result=tool_result,
        session_id="agent-studio-session-1",
        turn_id="opus-turn-1",
    )
    compact = json.loads(content)

    assert compact["status"] == "compacted_tool_result"
    recall_purpose = compact["recall"]["retained_proposal_input"]["purpose"]
    assert "Only the authored targeted edits remain" in recall_purpose
    assert "refresh_workshop_prompt after approval" in recall_purpose
    assert "exact authored proposal remains" not in recall_purpose
    assert proposed_prompt not in content


def test_compacted_workshop_ack_never_replays_proposal_input(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "300")
    proposed_prompt = "Do not replay this exact proposal.\n" * 1200
    prompt_hash = api_module._prompt_hash(proposed_prompt)

    content = api_module._provider_tool_result_content(
        tool_name="update_workshop_prompt_draft",
        tool_input={"apply_mode": "replace", "updated_prompt": proposed_prompt},
        tool_result={
            "success": True,
            "approval_status": "pending_user_approval",
            "pending_user_approval": True,
            "proposal_id": f"main:{prompt_hash}",
            "target_prompt": "main",
            "target_group_id": None,
            "apply_mode": "replace",
            "proposed_prompt": proposed_prompt,
            "prompt_length": len(proposed_prompt),
            "prompt_hash": prompt_hash,
            "change_summary": "A summary long enough to force generic compaction.",
            "message": "Awaiting approval.",
        },
        session_id="agent-studio-session-1",
        turn_id="opus-turn-1",
    )
    compact = json.loads(content)

    assert compact["status"] == "compacted_tool_result"
    assert "repeat_or_narrow_tool" not in compact["recall"]
    assert "retained_proposal_input" in compact["recall"]
    assert proposed_prompt not in content
    assert "updated_prompt" not in content


def test_default_exact_trace_chunk_stays_inline_with_metadata_headroom(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    serialized = "realistic exact TraceReview content " * 250
    serialized = serialized[:8000]
    tool_result = {
        "status": "success",
        "data": {
            "source": "local",
            "trace_id": "856df16f1752cb53ee43dcb2f5ecfd16",
            "payload": {
                "payload_id": "observation:observation-123:output",
                "field_id": "payload:observation:observation-123:output",
                "field": "output",
                "sha256": "a" * 64,
                "start": 0,
                "end": len(serialized),
                "returned_char_count": len(serialized),
                "total_char_count": 32000,
                "byte_count": 32000,
                "complete": False,
                "next_start": len(serialized),
                "next_call": {
                    "payload_id": "observation:observation-123:output",
                    "start": len(serialized),
                },
                "serialized": serialized,
            },
        },
        "token_info": {"estimated_tokens": 2200, "within_budget": True},
        "error": None,
    }

    content = api_module._provider_tool_result_content(
        tool_name="get_trace_payload",
        tool_input={
            "trace_id": "856df16f1752cb53ee43dcb2f5ecfd16",
            "payload_id": "observation:observation-123:output",
            "start": 0,
            "max_chars": 8000,
        },
        tool_result=tool_result,
        session_id="agent-studio-session-1",
        turn_id="opus-turn-4-abc123",
    )

    assert len(content) < 12000
    assert json.loads(content) == tool_result


@pytest.mark.parametrize(
    "serialized",
    [
        "😀" * 666,
        "\\\"quoted\\\\value" * 400,
    ],
)
def test_json_escape_aware_exact_trace_chunks_stay_inline(monkeypatch, serialized):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    tool_result = {
        "status": "success",
        "data": {
            "field": "assistant_response",
            "chunk": {
                "field_id": "conversation:assistant_response",
                "field": "assistant_response",
                "sha256": "a" * 64,
                "total_char_count": 20000,
                "byte_count": 80000,
                "start": 0,
                "end": len(serialized),
                "returned_char_count": len(serialized),
                "complete": False,
                "next_start": len(serialized),
                "next_call": {
                    "trace_id": "856df16f1752cb53ee43dcb2f5ecfd16",
                    "field": "assistant_response",
                    "start": len(serialized),
                    "max_chars": 8000,
                },
                "serialized": serialized,
            },
            "domain_envelope": None,
        },
        "token_info": {"estimated_tokens": 2500, "within_budget": True, "warning": None},
        "error": None,
    }

    content = api_module._provider_tool_result_content(
        tool_name="get_trace_conversation",
        tool_input={
            "trace_id": "856df16f1752cb53ee43dcb2f5ecfd16",
            "field": "assistant_response",
        },
        tool_result=tool_result,
        session_id="agent-studio-session-1",
        turn_id="opus-turn-escape-aware",
    )

    assert len(content) < 12000
    assert json.loads(content) == tool_result


def test_current_flow_manifest_and_bounded_details_stay_under_provider_cap(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    monkeypatch.setenv("AGENT_STUDIO_FLOW_INSPECTION_CHUNK_MAX_CHARS", "1000")
    flow_tools.set_current_flow_context(
        {
            "flow_name": "Disease Extraction and Export",
            "version": "1.1",
            "entry_node_id": "task",
            "nodes": [
                {
                    "id": "task",
                    "type": "task_input",
                    "data": {
                        "agent_id": "task_input",
                        "agent_display_name": "Task",
                        "task_instructions": "Extract disease mentions. " * 100,
                        "output_key": "task_input",
                    },
                },
                {
                    "id": "disease",
                    "type": "agent",
                    "data": {
                        "agent_id": "disease_extractor",
                        "agent_display_name": "Disease Extractor",
                        "step_goal": "Extract normalized disease identifiers.",
                        "custom_instructions": "Preserve evidence. " * 100,
                        "prompt_version": 4,
                        "output_key": "diseases",
                        "validation_attachments": [
                            {
                                "attachment_id": "disease-ontology",
                                "validator_id": "disease-validator",
                                "validator_binding_id": "disease-binding",
                                "state": "active",
                                "enabled": True,
                                "default_enabled": True,
                            }
                        ],
                        "validation_groups": [],
                    },
                },
                {
                    "id": "csv",
                    "type": "output",
                    "data": {
                        "agent_id": "csv_formatter",
                        "agent_display_name": "CSV",
                        "output_key": "csv",
                        "projection_plan": {
                            "columns": [
                                {"field": f"disease.field_{index}", "label": f"Field {index}"}
                                for index in range(50)
                            ],
                            "format": "csv",
                        },
                    },
                },
            ],
            "edges": [
                {"id": "c1", "source": "task", "target": "disease"},
                {
                    "id": "o1",
                    "source": "disease",
                    "target": "csv",
                    "role": "output_attachment",
                },
            ],
        }
    )

    results = [
        ("get_current_flow", {}, flow_tools._get_current_flow_handler()()),
        (
            "get_current_flow_topology",
            {"section": "output_bindings"},
            flow_tools._get_current_flow_topology_handler()(section="output_bindings"),
        ),
        (
            "get_current_flow_node",
            {"node_id": "disease"},
            flow_tools._get_current_flow_node_handler()(node_id="disease"),
        ),
        (
            "get_current_flow_instructions",
            {"node_id": "task", "field": "task_instructions"},
            flow_tools._get_current_flow_instructions_handler()(
                node_id="task", field="task_instructions"
            ),
        ),
        (
            "get_current_flow_projection_plan",
            {"node_id": "csv", "field": "columns"},
            flow_tools._get_current_flow_projection_plan_handler()(
                node_id="csv", field="columns"
            ),
        ),
        (
            "get_current_flow_validation_warnings",
            {},
            flow_tools._get_current_flow_validation_warnings_handler()(),
        ),
        (
            "get_current_flow_validation_schedule",
            {"node_id": "disease", "section": "selections"},
            flow_tools._get_current_flow_validation_schedule_handler()(
                node_id="disease", section="selections"
            ),
        ),
    ]

    for tool_name, tool_input, result in results:
        content = api_module._provider_tool_result_content(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_result=result,
            session_id="agent-studio-session-flow",
            turn_id="flow-turn",
        )
        assert len(content) < 12000
        assert json.loads(content).get("status") != "compacted_tool_result"


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


def test_repeated_tool_loop_continuations_stay_compact_and_keep_exact_results(
    monkeypatch,
):
    captured: dict[str, Any] = {}
    inventory_value = "payload inventory entry " * 400
    exact_payload_value = "exact TraceReview payload " * 500

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
            turn_id="opus-turn-repeat-abc123",
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

    async def _fake_handle_tool_call(*, tool_name, **_kwargs):
        if tool_name == "get_trace_payloads":
            return {
                "status": "success",
                "data": {
                    "payloads": [
                        {
                            "payload_id": "observation:abc:output",
                            "preview": inventory_value,
                            "model_live": False,
                        }
                    ],
                    "observability_payloads": {
                        "exact_payload_requires_explicit_lookup": True,
                    },
                },
            }
        if tool_name == "get_trace_payload":
            return {
                "status": "success",
                "trace_id": "trace-1",
                "data": {
                    "payload_id": "observation:abc:output",
                    "value": exact_payload_value,
                    "next_start": None,
                },
            }
        raise AssertionError(f"unexpected tool: {tool_name}")

    monkeypatch.setattr(api_module, "_handle_tool_call", _fake_handle_tool_call)
    monkeypatch.setattr(
        api_module.anthropic,
        "AsyncAnthropic",
        lambda api_key: _RepeatedToolLoopAnthropicClient(captured),
    )

    request = api_module.ChatRequest(
        messages=[
            api_module.ChatMessage(
                role="user",
                content="Inspect the trace inventory, then fetch the exact payload.",
            )
        ],
        context=api_module.ChatContext(active_tab="agents", trace_id="trace-1"),
    )

    response = asyncio.run(
        api_module.chat_with_opus(
            request,
            user={"sub": "auth-sub-1", "email": "dev@example.org"},
        )
    )
    events = asyncio.run(_consume_stream(response))

    preflight_operations = [
        event["operation"]
        for event in events
        if event["type"] == "PROVIDER_CONTEXT_PREFLIGHT"
    ]
    assert preflight_operations == [
        "initial_anthropic_call",
        "tool_loop_continuation",
        "tool_loop_continuation",
    ]

    tool_result_events = [event for event in events if event["type"] == "TOOL_RESULT"]
    assert tool_result_events[0]["result"]["data"]["payloads"][0]["preview"] == inventory_value
    assert tool_result_events[1]["result"]["data"]["value"] == exact_payload_value

    first_provider_result = captured["first_provider_result"]
    second_provider_result = captured["second_provider_result"]
    first_compact = json.loads(first_provider_result)
    second_compact = json.loads(second_provider_result)

    assert first_compact["status"] == "compacted_tool_result"
    assert "payloads" in first_compact["summary"]["fields"]["data"]["keys"]
    assert first_compact["recall"]["trace_payloads"]["payload_ids"] == [
        "observation:abc:output"
    ]
    assert second_compact["status"] == "compacted_tool_result"
    assert second_compact["recall"]["chat_turn"]["turn_id"] == "opus-turn-repeat-abc123"
    assert second_compact["recall"]["trace_payloads"]["payload_ids"] == [
        "observation:abc:output"
    ]
    assert inventory_value not in first_provider_result
    assert exact_payload_value not in second_provider_result


def test_compact_tool_result_recall_hints_fetch_exact_turn_and_trace_payload(
    monkeypatch,
):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "500")

    exact_turn_phrase = "Early Agent Studio note: preserve record-alpha-6789 exactly."
    exact_payload_value = "TraceReview exact payload body " * 300
    compact = json.loads(
        api_module._provider_tool_result_content(
            tool_name="get_trace_payload",
            tool_input={
                "trace_id": "trace-1",
                "payload_id": "observation:abc:output",
                "max_chars": 0,
            },
            tool_result={
                "status": "success",
                "trace_id": "trace-1",
                "data": {
                    "payload_id": "observation:abc:output",
                    "value": exact_payload_value,
                },
            },
            session_id="agent-studio-session-1",
            turn_id="opus-turn-early-abc123",
        )
    )

    class _FakeRepository:
        def __init__(self, _db):
            pass

        def get_session(self, **kwargs):
            assert kwargs == {
                "session_id": "agent-studio-session-1",
                "user_auth_sub": "auth-sub-1",
            }
            return _agent_studio_session(session_id=kwargs["session_id"])

        def list_messages_for_turn(self, **kwargs):
            assert kwargs == {
                "session_id": "agent-studio-session-1",
                "user_auth_sub": "auth-sub-1",
                "chat_kind": AGENT_STUDIO_CHAT_KIND,
                "turn_id": "opus-turn-early-abc123",
                "excluded_message_types": {"context_compaction"},
            }
            return [
                _agent_studio_message(
                    session_id=kwargs["session_id"],
                    turn_id=kwargs["turn_id"],
                    role="user",
                    content=exact_turn_phrase,
                ),
                _agent_studio_message(
                    session_id=kwargs["session_id"],
                    turn_id=kwargs["turn_id"],
                    role="assistant",
                    content="I recorded that exact note.",
                ),
            ]

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(api_module, "ChatHistoryRepository", _FakeRepository)

    turn_result = asyncio.run(
        api_module._handle_tool_call(
            tool_name=compact["recall"]["chat_turn"]["tool"],
            tool_input=compact["recall"]["chat_turn"],
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-1",
            messages=[],
        )
    )

    from src.lib.agent_studio import tools as tools_module

    captured_payload_lookup: dict[str, Any] = {}

    async def _fake_get_trace_payload(**kwargs):
        captured_payload_lookup.update(kwargs)
        return {
            "status": "success",
            "data": {
                "payload_id": kwargs["payload_id"],
                "value": exact_payload_value,
            },
        }

    monkeypatch.setattr(tools_module, "get_trace_payload", _fake_get_trace_payload)
    payload_result = asyncio.run(
        api_module._handle_tool_call(
            tool_name=compact["recall"]["trace_payloads"]["tool"],
            tool_input={
                "trace_id": "trace-1",
                "payload_id": compact["recall"]["trace_payloads"]["payload_ids"][0],
                "max_chars": 0,
            },
            context=None,
            user_email="dev@example.org",
            user_auth_sub="auth-sub-1",
            messages=[],
        )
    )

    assert exact_payload_value not in json.dumps(compact, sort_keys=True)
    assert turn_result["success"] is True
    assert turn_result["messages"][0]["content"] == exact_turn_phrase
    assert captured_payload_lookup == {
        "trace_id": "trace-1",
        "payload_id": "observation:abc:output",
        "scope": None,
        "observation_id": None,
        "field": None,
        "start": 0,
        "max_chars": 0,
    }
    assert payload_result["data"]["value"] == exact_payload_value
