"""Unit tests for Agent Studio test-agent endpoint and workshop prompt context."""

import asyncio
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse


class TestAgentTestEndpoint:
    """Tests for POST /api/agent-studio/test-agent/{agent_id}."""

    def test_flatten_runner_event_merges_data_and_audit_fields(self):
        from src.api.agent_studio import _flatten_runner_event

        event = {
            "type": "TEXT_MESSAGE_CONTENT",
            "data": {"delta": "hello", "trace_id": "trace-123"},
            "timestamp": "2026-02-11T00:00:00Z",
            "details": {"message": "ok"},
        }

        flattened = _flatten_runner_event(event, "session-123")

        assert flattened["type"] == "TEXT_MESSAGE_CONTENT"
        assert flattened["delta"] == "hello"
        assert flattened["trace_id"] == "trace-123"
        assert flattened["session_id"] == "session-123"
        assert flattened["sessionId"] == "session-123"
        assert flattened["timestamp"] == "2026-02-11T00:00:00Z"
        assert flattened["details"] == {"message": "ok"}

    def test_endpoint_requires_document_for_document_dependent_agent(self, monkeypatch):
        import src.api.agent_studio as api_module

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "get_agent_metadata",
            lambda _agent_id, **_kwargs: {"requires_document": True},
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.test_agent_endpoint(
                    agent_id="pdf",
                    request=api_module.AgentTestRequest(input="test query"),
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )

        assert exc_info.value.status_code == 400
        assert "requires a document_id" in str(exc_info.value.detail)

    def test_endpoint_streams_runner_events(self, monkeypatch):
        import src.api.agent_studio as api_module

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "set_current_session_id",
            lambda _sid: None,
        )
        monkeypatch.setattr(
            api_module,
            "set_current_user_id",
            lambda _uid: None,
        )
        monkeypatch.setattr(
            api_module,
            "get_agent_metadata",
            lambda _agent_id, **_kwargs: {"requires_document": False},
        )
        monkeypatch.setattr(api_module, "get_agent_by_id", lambda _aid, **_kwargs: object())

        run_kwargs = {}

        async def _fake_run_agent_streamed(**kwargs):
            run_kwargs.update(kwargs)
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-123"}}
            yield {"type": "TEXT_MESSAGE_CONTENT", "data": {"delta": "hello"}}
            yield {
                "type": "RUN_FINISHED",
                "data": {"response": "hello", "trace_id": "trace-123"},
            }

        monkeypatch.setattr(api_module, "run_agent_streamed", _fake_run_agent_streamed)

        response = asyncio.run(
            api_module.test_agent_endpoint(
                agent_id="gene",
                request=api_module.AgentTestRequest(
                    input="test query",
                    mod_id="WB",
                    session_id="session-1",
                ),
                user={"sub": "auth-sub"},
                db=SimpleNamespace(),
            )
        )

        assert isinstance(response, StreamingResponse)

        async def _consume_stream() -> str:
            chunks = []
            async for chunk in response.body_iterator:
                if isinstance(chunk, bytes):
                    chunks.append(chunk.decode("utf-8"))
                else:
                    chunks.append(chunk)
            return "".join(chunks)

        stream_text = asyncio.run(_consume_stream())

        assert '"type": "TEXT_MESSAGE_CONTENT"' in stream_text
        assert '"delta": "hello"' in stream_text
        assert '"type": "DONE"' in stream_text
        assert '"trace_id": "trace-123"' in stream_text
        assert '"session_id": "session-1"' in stream_text
        assert run_kwargs["active_groups"] == ["WB"]
        assert run_kwargs["session_id"] == "session-1"

    def test_endpoint_resolves_custom_agent_ids_with_ownership_check(self, monkeypatch):
        import src.api.agent_studio as api_module

        custom_uuid = uuid.uuid4()
        custom_agent_id = f"ca_{custom_uuid}"
        observed = {}

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "parse_custom_agent_id",
            lambda _agent_id: custom_uuid,
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda _db, _uuid, _uid: SimpleNamespace(id=custom_uuid),
        )
        monkeypatch.setattr(
            api_module,
            "set_current_session_id",
            lambda _sid: None,
        )
        monkeypatch.setattr(
            api_module,
            "set_current_user_id",
            lambda _uid: None,
        )
        def _fake_get_agent_metadata(agent_id: str, **_kwargs):
            observed["metadata_agent_id"] = agent_id
            return {"requires_document": False}

        def _fake_get_agent_by_id(agent_id: str, **_kwargs):
            observed["factory_agent_id"] = agent_id
            return object()

        monkeypatch.setattr(
            api_module,
            "get_agent_metadata",
            _fake_get_agent_metadata,
        )
        monkeypatch.setattr(
            api_module,
            "get_agent_by_id",
            _fake_get_agent_by_id,
        )

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-custom"}}
            yield {"type": "RUN_FINISHED", "data": {"response": "ok", "trace_id": "trace-custom"}}

        monkeypatch.setattr(api_module, "run_agent_streamed", _fake_run_agent_streamed)

        response = asyncio.run(
            api_module.test_agent_endpoint(
                agent_id=custom_agent_id,
                request=api_module.AgentTestRequest(input="test custom query"),
                user={"sub": "auth-sub"},
                db=SimpleNamespace(),
            )
        )

        assert isinstance(response, StreamingResponse)
        assert observed["metadata_agent_id"] == custom_agent_id
        assert observed["factory_agent_id"] == custom_agent_id


class TestAgentWorkshopSystemPrompt:
    """Tests for agent workshop context injection into Opus system prompt."""

    def test_build_opus_system_prompt_includes_workshop_context_and_truncates_draft(self):
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import ChatContext, AgentWorkshopContext

        draft = "A" * 12050
        context = ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                template_source="gene",
                template_name="Gene Validation",
                custom_agent_id="ca_123",
                custom_agent_name="Gene Custom v3",
                include_mod_rules=True,
                selected_mod_id="WB",
                prompt_draft=draft,
                selected_mod_prompt_draft="WB MOD DRAFT CONTENT",
                mod_prompt_override_count=2,
                has_mod_prompt_overrides=True,
                template_prompt_stale=True,
                template_exists=True,
                draft_tool_ids=["search_document", "read_section", "read_subsection", "agr_curation_query"],
            ),
        )

        system_prompt = api_module._build_opus_system_prompt(context)

        assert "<agent_workshop_context>" in system_prompt
        assert "Current Context: Agent Workshop" in system_prompt
        assert "Template source: Gene Validation" in system_prompt
        assert "Custom agent: Gene Custom v3" in system_prompt
        assert "Selected MOD: WB" in system_prompt
        assert "Has MOD prompt overrides: Yes" in system_prompt
        assert "MOD override count: 2" in system_prompt
        assert "Draft attached tools: search_document, read_section, read_subsection, agr_curation_query" in system_prompt
        assert "proactively identify concrete prompt improvements during normal conversation" in system_prompt
        assert "ask for permission in plain language" in system_prompt
        assert "<workshop_prompt_draft>" in system_prompt
        assert "<workshop_selected_mod_prompt mod=\"WB\">" in system_prompt
        assert "WB MOD DRAFT CONTENT" in system_prompt
        assert "Truncated to first 12000 chars for context." in system_prompt
        assert "Prompt injection note:" in system_prompt

    def test_get_all_opus_tools_includes_workshop_prompt_update_tool(self):
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import ChatContext, AgentWorkshopContext

        tools = api_module._get_all_opus_tools(
            ChatContext(
                active_tab="agent_workshop",
                agent_workshop=AgentWorkshopContext(template_source="gene"),
            )
        )
        tool_names = {tool.get("name") for tool in tools}

        assert "update_workshop_prompt_draft" in tool_names

    def test_get_all_opus_tools_excludes_flow_tools_outside_flows_tab(self):
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import ChatContext

        tools = api_module._get_all_opus_tools(ChatContext(active_tab="agent_workshop"))
        tool_names = {tool.get("name") for tool in tools}

        assert "get_current_flow" not in tool_names
        assert "get_available_agents" not in tool_names

    def test_get_all_opus_tools_includes_flow_tools_on_flows_tab(self):
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import ChatContext

        tools = api_module._get_all_opus_tools(ChatContext(active_tab="flows"))
        tool_names = {tool.get("name") for tool in tools}

        assert "get_current_flow" in tool_names
        assert "get_available_agents" in tool_names

    def test_handle_update_workshop_prompt_tool_returns_proposal_with_approval_gate(self):
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import ChatContext, AgentWorkshopContext

        context = ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(template_source="gene"),
        )

        result = asyncio.run(
            api_module._handle_tool_call(
                tool_name="update_workshop_prompt_draft",
                tool_input={
                    "updated_prompt": "You are a strict gene expression extraction assistant.",
                    "change_summary": "Tightened extraction and citation requirements.",
                    "apply_mode": "replace",
                },
                context=context,
                user_email="dev@example.org",
                messages=[],
            )
        )

        assert result["success"] is True
        assert result["pending_user_approval"] is True
        assert result["apply_mode"] == "replace"
        assert result["proposed_prompt"] == "You are a strict gene expression extraction assistant."
        assert result["change_summary"] == "Tightened extraction and citation requirements."

    def test_handle_tool_call_blocks_flow_tools_outside_flows_tab(self):
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import ChatContext

        result = asyncio.run(
            api_module._handle_tool_call(
                tool_name="get_current_flow",
                tool_input={},
                context=ChatContext(active_tab="agent_workshop"),
                user_email="dev@example.org",
                messages=[],
            )
        )

        assert result["success"] is False
        assert "not available on the agent_workshop tab" in result["error"]

    def test_handle_update_workshop_prompt_tool_rejects_non_workshop_context(self):
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import ChatContext

        result = asyncio.run(
            api_module._handle_tool_call(
                tool_name="update_workshop_prompt_draft",
                tool_input={"updated_prompt": "Prompt text"},
                context=ChatContext(active_tab="agents"),
                user_email="dev@example.org",
                messages=[],
            )
        )

        assert result["success"] is False
        assert "only available while the curator is on the Agent Workshop tab" in result["error"]

    def test_handle_update_workshop_prompt_tool_supports_targeted_edit_text_replacement(self):
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import ChatContext, AgentWorkshopContext

        context = ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                template_source="gene",
                prompt_draft="You are a careful curator.\nAlways cite evidence.\n",
            ),
        )

        result = asyncio.run(
            api_module._handle_tool_call(
                tool_name="update_workshop_prompt_draft",
                tool_input={
                    "apply_mode": "targeted_edit",
                    "edits": [
                        {
                            "operation": "replace_text",
                            "find_text": "careful",
                            "replacement_text": "rigorous",
                            "occurrence": "first",
                        }
                    ],
                },
                context=context,
                user_email="dev@example.org",
                messages=[],
            )
        )

        assert result["success"] is True
        assert result["pending_user_approval"] is True
        assert result["apply_mode"] == "targeted_edit"
        assert "You are a rigorous curator." in result["proposed_prompt"]
        assert result["applied_edits"] == ["replace_text first occurrence"]

    def test_handle_update_workshop_prompt_tool_supports_targeted_edit_section_replacement(self):
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import ChatContext, AgentWorkshopContext

        context = ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                template_source="gene",
                prompt_draft=(
                    "## Scope\n"
                    "Extract expression claims.\n\n"
                    "## Output\n"
                    "Return concise bullet points.\n"
                ),
            ),
        )

        result = asyncio.run(
            api_module._handle_tool_call(
                tool_name="update_workshop_prompt_draft",
                tool_input={
                    "apply_mode": "targeted_edit",
                    "edits": [
                        {
                            "operation": "replace_section",
                            "section_heading": "Output",
                            "replacement_text": "Return JSON with evidence and citations.",
                        }
                    ],
                },
                context=context,
                user_email="dev@example.org",
                messages=[],
            )
        )

        assert result["success"] is True
        assert result["apply_mode"] == "targeted_edit"
        assert "## Output" in result["proposed_prompt"]
        assert "Return JSON with evidence and citations." in result["proposed_prompt"]
        assert "Return concise bullet points." not in result["proposed_prompt"]

    def test_handle_update_workshop_prompt_tool_rejects_targeted_edit_without_edits(self):
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import ChatContext, AgentWorkshopContext

        context = ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                template_source="gene",
                prompt_draft="## Scope\nExtract claims.\n",
            ),
        )

        result = asyncio.run(
            api_module._handle_tool_call(
                tool_name="update_workshop_prompt_draft",
                tool_input={"apply_mode": "targeted_edit", "edits": []},
                context=context,
                user_email="dev@example.org",
                messages=[],
            )
        )

        assert result["success"] is False
        assert "edits must be a non-empty array" in result["error"]
