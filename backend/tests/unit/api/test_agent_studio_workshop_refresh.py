"""Tests for Agent Workshop prompt refresh behavior in Agent Studio chat."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

import src.api.agent_studio as api_module
from src.lib.agent_studio.authoring_context import workshop_draft_fingerprint
import src.lib.agent_studio.tool_search_authorization as tool_search_authorization
from src.lib.agent_studio.models import AgentWorkshopContext, ChatContext
from src.lib.chat_history_repository import AGENT_STUDIO_CHAT_KIND


@pytest.fixture
def contract_client(monkeypatch):
    """Create a test client with deterministic API-key auth for the chat endpoint."""

    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("TESTING_API_KEY", "contract-test-key")
    from fastapi.testclient import TestClient
    from main import app

    app.dependency_overrides.clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def chat_contract_auth_headers():
    return {"X-API-Key": "contract-test-key"}


def _consume_sse_events(stream_response) -> list[dict]:
    events: list[dict] = []
    for line in stream_response.iter_lines():
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[6:]))
    return events


def test_workshop_refresh_tool_is_agent_workshop_scoped():
    workshop_context = ChatContext(
        active_tab="agent_workshop",
        agent_workshop=AgentWorkshopContext(prompt_draft="Review me"),
    )
    tools_by_name = {
        tool["name"]: tool
        for tool in api_module._get_all_opus_tools(workshop_context)
    }

    assert "refresh_workshop_prompt" in api_module._WORKSHOP_TOOLS
    assert "refresh_workshop_prompt" in tools_by_name
    assert tools_by_name["refresh_workshop_prompt"]["input_schema"]["required"] == []
    refresh_properties = tools_by_name["refresh_workshop_prompt"]["input_schema"][
        "properties"
    ]
    assert refresh_properties["start"]["minimum"] == 0
    assert refresh_properties["max_chars"]["minimum"] == 1
    assert "prompt_hash" in refresh_properties
    assert refresh_properties["target_prompt"]["enum"] == ["main", "group", "metadata"]

    agents_tools = {
        tool["name"]
        for tool in api_module._get_all_opus_tools(ChatContext(active_tab="agents"))
    }
    assert "refresh_workshop_prompt" not in agents_tools


def test_workshop_prompt_refresh_is_forced_only_for_prompt_sensitive_turns():
    context = ChatContext(
        active_tab="agent_workshop",
        agent_workshop=AgentWorkshopContext(prompt_draft="Current draft"),
    )

    assert api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="Review main prompt",
    )
    assert api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="what do you think now?",
    )
    assert api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="Does it still mention minerite?",
    )
    assert not api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="How should I think about model tradeoffs?",
    )
    assert not api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="What should I do now about the flow?",
    )
    assert not api_module._should_force_workshop_prompt_refresh(
        context=context,
        latest_user_message="Can you explain minerite?",
    )
    assert not api_module._should_force_workshop_prompt_refresh(
        context=ChatContext(active_tab="agents"),
        latest_user_message="Review main prompt",
    )


@pytest.mark.asyncio
async def test_refresh_workshop_prompt_rejects_invalid_target_prompt():
    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "mod"},
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(prompt_draft="Current draft"),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
    )

    assert result == {
        "success": False,
        "error": "Invalid target_prompt: 'mod'. Must be 'main', 'group', or 'metadata'.",
    }


@pytest.mark.asyncio
async def test_refresh_workshop_metadata_chunks_exact_oversized_values(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_WORKSHOP_PROMPT_CHUNK_MAX_CHARS", "31")
    description = "Exact oversized description 🧬 " * 30
    context = ChatContext(
        active_tab="agent_workshop",
        agent_workshop=AgentWorkshopContext(
            getting_started_mode="clone",
            draft_name="Exact name",
            draft_description=description,
            draft_allowed_group_ids=["WB", "FB"],
            group_prompt_overrides={"WB": "rules", "FB": "other rules"},
            draft_tool_ids=[f"tool-{index}" for index in range(40)],
            draft_output_schema_key="gene",
            draft_is_dirty=True,
        ),
    )

    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "metadata"},
        context=context,
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
    )
    assert result["source"] == "current_workshop_metadata"
    chunks: list[str] = []
    while result["next_call"] is not None:
        result = await api_module._handle_tool_call(
            tool_name="refresh_workshop_prompt",
            tool_input=result["next_call"]["arguments"],
            context=context,
            user_email="curator@example.org",
            user_auth_sub="auth-sub-1",
        )
        chunks.append(result["content"])

    metadata = json.loads("".join(chunks))
    assert metadata["draft_description"] == description
    assert metadata["draft_tool_ids"] == [f"tool-{index}" for index in range(40)]
    assert metadata["group_prompt_override_ids"] == ["FB", "WB"]


@pytest.mark.asyncio
async def test_refresh_workshop_prompt_rejects_invalid_context_timestamp():
    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "main"},
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                prompt_draft="Current draft",
                custom_agent_updated_at="not-a-date",
            ),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
    )

    assert result == {
        "success": False,
        "error": "Invalid custom_agent_updated_at value. Expected an ISO 8601 timestamp.",
    }


@pytest.mark.asyncio
async def test_refresh_workshop_prompt_reads_any_captured_group_override():
    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "group", "target_group_id": "group-b"},
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                selected_group_id="group-a",
                selected_group_prompt_draft="Current group A draft",
                group_prompt_overrides={"GROUP-B": "Exact non-selected B override"},
                draft_is_dirty=True,
            ),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
    )

    assert result["success"] is True
    assert result["target_group_id"] == "GROUP-B"
    assert result["source"] == "current_workshop_draft"
    assert result["length"] == len("Exact non-selected B override")

    chunk = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input=result["next_call"]["arguments"],
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                selected_group_id="GROUP-A",
                selected_group_prompt_draft="Current group A draft",
                group_prompt_overrides={"GROUP-B": "Exact non-selected B override"},
                draft_is_dirty=True,
            ),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
    )
    assert chunk["content"] == "Exact non-selected B override"
    assert chunk["complete"] is True


@pytest.mark.asyncio
async def test_refresh_workshop_prompt_rejects_unknown_group_identity():
    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "group", "target_group_id": "group-c"},
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                selected_group_id="GROUP-A",
                selected_group_prompt_draft="Current group A draft",
                group_prompt_overrides={"GROUP-B": "Group B override"},
            ),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
    )

    assert result == {
        "success": False,
        "error": "Agent Workshop has no editable group prompt for GROUP-C.",
    }


@pytest.mark.asyncio
async def test_refresh_workshop_prompt_returns_error_when_saved_agent_is_inaccessible(monkeypatch):
    custom_agent_uuid = uuid4()

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    def _raise_access_error(*_args):
        raise api_module.CustomAgentAccessError("permission denied")

    monkeypatch.setattr(api_module, "get_custom_agent_visible_to_user", _raise_access_error)

    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "main"},
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                custom_agent_id=f"ca_{custom_agent_uuid}",
                prompt_draft="Potentially stale draft.",
            ),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
        user_db_id=7,
    )

    assert result == {
        "success": False,
        "error": f"Could not access custom agent {custom_agent_uuid}.",
    }


@pytest.mark.asyncio
async def test_refresh_workshop_prompt_prefers_newer_saved_custom_agent(monkeypatch):
    custom_agent_uuid = uuid4()
    saved_updated_at = datetime(2026, 5, 6, 14, 15, 20, tzinfo=timezone.utc)
    saved_agent = SimpleNamespace(
        id=custom_agent_uuid,
        template_source="demo_agent",
        instructions="Current prompt with the typo removed.",
        version=3,
        updated_at=saved_updated_at,
    )

    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(api_module, "get_custom_agent_visible_to_user", lambda *_args: saved_agent)

    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "main"},
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                custom_agent_id=f"ca_{custom_agent_uuid}",
                prompt_draft="Older prompt that still says minerite.",
                draft_is_dirty=True,
                custom_agent_updated_at="2026-05-06T14:10:00+00:00",
            ),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
        messages=[{"role": "assistant", "content": "Earlier review mentioned minerite."}],
        user_db_id=7,
    )

    assert result["success"] is True
    assert result["source"] == "saved_custom_agent"
    assert result["custom_agent_id"] == str(custom_agent_uuid)
    assert result["version"] == 3
    assert result["updated_at"] == saved_updated_at.isoformat()
    assert result["length"] == len("Current prompt with the typo removed.")
    assert result["hash"] == api_module._prompt_hash("Current prompt with the typo removed.")
    assert result["view"] == "summary"
    assert "content" not in result
    assert "current_prompt" not in result
    assert result["freshness"] == {
        "draft_is_dirty": True,
        "has_unsaved_context": False,
        "saved_is_newer": True,
        "context_updated_at": "2026-05-06T14:10:00+00:00",
        "saved_updated_at": saved_updated_at.isoformat(),
    }
    assert result["next_call"]["arguments"]["prompt_hash"] == result["hash"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "alpha beta\n" * 2500,
        "\n" * 20_000,
    ],
    ids=["ordinary-text", "json-escape-heavy-text"],
)
async def test_refresh_workshop_prompt_chunks_reconstruct_long_dirty_draft(
    monkeypatch,
    prompt,
):
    monkeypatch.setenv("AGENT_STUDIO_WORKSHOP_PROMPT_CHUNK_MAX_CHARS", "8000")
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    context = ChatContext(
        active_tab="agent_workshop",
        agent_workshop=AgentWorkshopContext(
            prompt_draft=prompt,
            draft_is_dirty=True,
            custom_agent_updated_at="2026-05-06T14:10:00+00:00",
        ),
    )

    summary = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={"target_prompt": "main"},
        context=context,
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
    )

    assert summary["contract_version"] == "workshop_prompt_refresh.v1"
    assert summary["source"] == "current_workshop_draft"
    assert summary["length"] == len(prompt)
    assert summary["hash"] == api_module._prompt_hash(prompt)

    chunks: list[str] = []
    next_call = summary["next_call"]
    last_result: dict[str, Any] | None = None
    while next_call is not None:
        result = await api_module._handle_tool_call(
            tool_name=next_call["tool"],
            tool_input=next_call["arguments"],
            context=context,
            user_email="curator@example.org",
            user_auth_sub="auth-sub-1",
        )
        assert result["view"] == "chunk"
        assert result["hash"] == summary["hash"]
        assert result["returned_range"]["end"] - result["returned_range"]["start"] == len(
            result["content"]
        )
        provider_content = api_module._provider_tool_result_content(
            tool_name="refresh_workshop_prompt",
            tool_input=next_call["arguments"],
            tool_result=result,
            session_id="agent-studio-session-1",
            turn_id="opus-turn-1",
        )
        provider_chunk = json.loads(provider_content)
        assert provider_chunk["view"] == "chunk"
        assert provider_chunk.get("tool_result_compacted") is not True
        chunks.append(provider_chunk["content"])
        last_result = result
        next_call = result["next_call"]

    assert last_result is not None
    assert last_result["complete"] is True
    assert "".join(chunks) == prompt


@pytest.mark.asyncio
async def test_refresh_workshop_prompt_rejects_chunk_from_stale_hash():
    result = await api_module._handle_tool_call(
        tool_name="refresh_workshop_prompt",
        tool_input={
            "target_prompt": "main",
            "prompt_hash": "stale-hash",
            "start": 0,
        },
        context=ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(prompt_draft="Current draft"),
        ),
        user_email="curator@example.org",
        user_auth_sub="auth-sub-1",
    )

    assert result["success"] is False
    assert "changed after the summary" in result["error"]
    assert result["current_hash"] == api_module._prompt_hash("Current draft")


def test_prompt_sensitive_agent_workshop_chat_forces_refresh_before_review(
    contract_client,
    chat_contract_auth_headers,
    monkeypatch,
):
    custom_agent_uuid = uuid4()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(api_module, "get_api_key", lambda _provider: "test-key")
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    monkeypatch.setattr(api_module, "_build_opus_system_prompt", lambda **_kwargs: "system prompt")
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
    monkeypatch.setattr(api_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(
        tool_search_authorization,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(refresh=lambda _db: []),
    )
    monkeypatch.setattr(
        api_module,
        "get_custom_agent_visible_to_user",
        lambda *_args: SimpleNamespace(
            id=custom_agent_uuid,
            template_source="demo_agent",
            instructions="Current saved prompt with no typo.",
            allowed_group_ids=[],
            version=4,
            updated_at=datetime(2026, 5, 6, 14, 15, 20, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(
        api_module,
        "_prepare_agent_studio_turn",
        lambda *, request, **_kwargs: api_module.PreparedAgentStudioTurn(
            session_id="agent-studio-session-1",
            turn_id="opus-turn-1",
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
            created_at=datetime(2026, 5, 6, 14, 16, tzinfo=timezone.utc),
        ),
    )
    async def _fake_openai_runtime(**kwargs):
        captured["runtime"] = kwargs
        state = kwargs["state"]
        tool = next(
            item
            for item in kwargs["tools"]
            if getattr(item, "name", None) == "refresh_workshop_prompt"
        )
        tool_input = {"target_prompt": "main", "view": "summary"}
        captured["forced_tool"] = tool
        tool_result = {
            "contract_version": "workshop_prompt_refresh.v1",
            "success": True,
            "source": "saved_custom_agent",
            "view": "summary",
            "length": len("Current saved prompt with no typo."),
        }
        captured["provider_output"] = api_module._provider_tool_result_content(
            tool_name="refresh_workshop_prompt",
            tool_input=tool_input,
            tool_result=tool_result,
            session_id="agent-studio-session-1",
            turn_id="opus-turn-1",
        )
        yield {
            "type": "TOOL_USE",
            "tool_name": "refresh_workshop_prompt",
            "tool_input": tool_input,
            "call_id": "call-1",
        }
        yield {
            "type": "TOOL_RESULT",
            "tool_name": "refresh_workshop_prompt",
            "result": tool_result,
            "call_id": "call-1",
        }
        state.assistant_text_parts.append("The current saved prompt no longer contains the typo.")
        state.response_id = "resp-workshop-1"
        yield {
            "type": "TEXT_DELTA",
            "delta": "The current saved prompt no longer contains the typo.",
        }

    monkeypatch.setattr(api_module, "stream_agent_studio_run", _fake_openai_runtime)

    workshop_payload = {
        "custom_agent_id": f"ca_{custom_agent_uuid}",
        "custom_agent_name": "Debbie test agent",
        "prompt_draft": "Older context still says minerite.",
        "draft_is_dirty": True,
        "custom_agent_updated_at": "2026-05-06T14:10:00+00:00",
    }
    workshop_model = AgentWorkshopContext.model_validate(workshop_payload)
    workshop_payload["draft_fingerprint"] = workshop_draft_fingerprint(workshop_model)

    with contract_client.stream(
        "POST",
        "/api/agent-studio/chat",
        headers=chat_contract_auth_headers,
        json={
            "messages": [
                {"role": "assistant", "content": "I saw minerite in the earlier draft."},
                {"role": "user", "content": "Did I fix it? Please review the prompt now."},
            ],
            "context": {
                "active_tab": "agent_workshop",
                "agent_workshop": workshop_payload,
            },
        },
    ) as response:
        events = _consume_sse_events(response)

    assert response.status_code == 200, response.text
    preflight_events = [
        event for event in events if event["type"] == "PROVIDER_CONTEXT_PREFLIGHT"
    ]
    assert [event["operation"] for event in preflight_events] == ["agents_sdk_run"]
    output_events = [
        event for event in events if event["type"] != "PROVIDER_CONTEXT_PREFLIGHT"
    ]

    assert [event["type"] for event in output_events] == [
        "TOOL_USE",
        "TOOL_RESULT",
        "TEXT_DELTA",
        "DONE",
    ]
    runtime_call = captured["runtime"]
    assert runtime_call["max_turns"] == api_module.get_agent_studio_openai_max_turns()
    assert runtime_call["model_settings"].reasoning.effort == "medium"
    assert runtime_call["model_settings"].tool_choice == "refresh_workshop_prompt"
    assert runtime_call["model_settings"].parallel_tool_calls is False

    tool_result = output_events[1]["result"]
    assert tool_result["source"] == "saved_custom_agent"
    assert tool_result["view"] == "summary"
    assert tool_result["length"] == len("Current saved prompt with no typo.")
    assert "content" not in tool_result
    assert "current_prompt" not in tool_result

    assert "Current saved prompt with no typo." not in captured["provider_output"]
    assert "workshop_prompt_refresh.v1" in captured["provider_output"]
    assert "minerite" not in captured["provider_output"]
