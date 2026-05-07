"""Unit coverage for Agent Studio chat debug metadata snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from src.api import agent_studio as api_module
from src.lib.agent_studio.models import AgentWorkshopContext, ChatContext, ChatMessage


def test_user_debug_payload_summarizes_workshop_prompts_without_raw_text(monkeypatch):
    custom_agent_uuid = UUID("11111111-2222-3333-4444-555555555555")
    saved_updated_at = datetime(2026, 5, 7, 10, 30, tzinfo=timezone.utc)
    saved_agent = SimpleNamespace(
        id=custom_agent_uuid,
        custom_prompt="Saved main prompt",
        parent_agent_key="gene",
        group_prompt_overrides={"WB": "Saved WB prompt"},
        version=3,
        updated_at=saved_updated_at,
    )

    monkeypatch.setattr(
        api_module,
        "get_custom_agent_visible_to_user",
        lambda _db, _uuid, _user_db_id: saved_agent,
    )
    monkeypatch.setattr(
        api_module,
        "get_custom_agent_group_prompt",
        lambda **_kwargs: "Saved WB prompt",
    )

    prepared_turn = api_module.PreparedAgentStudioTurn(
        session_id="agent-studio-session-1",
        turn_id="opus-turn-1",
        user_message="Review my draft",
        requested_context_session_id="agent-studio-session-1",
    )
    request = api_module.ChatRequest(
        messages=[ChatMessage(role="user", content="Review my draft")],
        context=ChatContext(
            active_tab="agent_workshop",
            selected_agent_id=f"ca_{custom_agent_uuid}",
            selected_group_id="WB",
            session_id="agent-studio-session-1",
            trace_id="trace-123",
            agent_workshop=AgentWorkshopContext(
                template_source="gene",
                template_name="Gene Specialist",
                custom_agent_id=f"ca_{custom_agent_uuid}",
                custom_agent_name="My Gene Agent",
                selected_group_id="WB",
                prompt_draft="Saved main prompt",
                selected_group_prompt_draft="Changed WB draft",
                draft_is_dirty=True,
                custom_agent_updated_at=saved_updated_at.isoformat(),
                draft_tool_ids=["agr_curation_query", "search_codebase"],
            ),
        ),
    )

    payload = api_module._build_agent_studio_user_debug_payload(
        db=SimpleNamespace(),
        request=request,
        prepared_turn=prepared_turn,
        user_db_id=7,
    )

    assert payload["debug_context"]["active_tab"] == "agent_workshop"
    assert payload["debug_context"]["agent_workshop"]["saved_custom_agent"] == {
        "custom_agent_id": str(custom_agent_uuid),
        "runtime_agent_id": f"ca_{custom_agent_uuid}",
        "version": 3,
        "updated_at": saved_updated_at.isoformat(),
        "lookup_error": None,
    }
    prompt_context = payload["agent_workshop_prompt_context"]
    assert prompt_context["context_source"] == "frontend_draft"
    assert prompt_context["frontend_draft_matches_saved_db"] is False
    assert prompt_context["frontend_draft"]["main_prompt"]["length"] == len(
        "Saved main prompt"
    )
    assert prompt_context["saved_db_prompt"]["version"] == 3
    assert "Saved main prompt" not in str(payload)
    assert "Changed WB draft" not in str(payload)
    assert "Saved WB prompt" not in str(payload)


def test_tool_call_audit_entry_summarizes_arguments_and_scope_errors():
    context = ChatContext(active_tab="agents")
    tool_result = {
        "success": False,
        "error": "Tool 'create_flow' is not available on the agents tab. Use the matching screen for that tool type.",
    }

    audit = api_module._tool_call_audit_entry(
        tool_name="create_flow",
        tool_use_id="toolu-123",
        tool_input={
            "trace_id": "trace-123",
            "updated_prompt": "Raw prompt text should not be stored",
        },
        tool_result=tool_result,
        context=context,
    )

    assert audit["tool_name"] == "create_flow"
    assert audit["tool_use_id"] == "toolu-123"
    assert audit["result_status"] == "error"
    assert audit["result_type"] == "dict"
    assert audit["backend_blocked_tool_scope"] is True
    assert audit["argument_summary"]["fields"]["trace_id"] == {
        "type": "string",
        "value": "trace-123",
        "length": 9,
    }
    prompt_summary = audit["argument_summary"]["fields"]["updated_prompt"]
    assert prompt_summary["type"] == "string"
    assert prompt_summary["length"] == len("Raw prompt text should not be stored")
    assert "Raw prompt text should not be stored" not in str(audit)


def test_audit_summary_preserves_only_allowlisted_short_debug_values():
    custom_agent_uuid = "11111111-2222-3333-4444-555555555555"
    summary = api_module._summarize_audit_value(
        {
            "trace_id": "trace-123",
            "target_prompt": "group",
            "custom_agent_id": custom_agent_uuid,
            "runtime_agent_id": f"ca_{custom_agent_uuid}",
            "source": "saved_custom_agent",
            "query": "Raw prompt text should not be stored",
            "updated_prompt": "Raw prompt text should not be stored",
            "api_key": "secret-key-material",
        }
    )

    fields = summary["fields"]
    assert fields["trace_id"]["value"] == "trace-123"
    assert fields["target_prompt"]["value"] == "group"
    assert fields["custom_agent_id"]["value"] == custom_agent_uuid
    assert fields["runtime_agent_id"]["value"] == f"ca_{custom_agent_uuid}"
    assert fields["source"]["value"] == "saved_custom_agent"
    assert "value" not in fields["query"]
    assert fields["query"]["length"] == len("Raw prompt text should not be stored")
    assert "value" not in fields["updated_prompt"]
    assert fields["updated_prompt"]["length"] == len(
        "Raw prompt text should not be stored"
    )
    assert "value" not in fields["api_key"]
    assert "Raw prompt text should not be stored" not in str(summary)
    assert "secret-key-material" not in str(summary)


def test_tool_call_audit_entry_records_non_dict_result_type():
    audit = api_module._tool_call_audit_entry(
        tool_name="summarize_trace",
        tool_use_id="toolu-456",
        tool_input={"trace_id": "trace-123"},
        tool_result="plain string result",
        context=ChatContext(active_tab="agents"),
    )

    assert audit["result_status"] == "success"
    assert audit["result_type"] == "str"
    assert audit["result_summary"]["type"] == "string"
    assert "plain string result" not in str(audit)
