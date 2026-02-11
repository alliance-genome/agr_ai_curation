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
            lambda _agent_id: {"requires_document": True},
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
            lambda _agent_id: {"requires_document": False},
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
        def _fake_get_agent_metadata(agent_id: str):
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


class TestPromptWorkshopSystemPrompt:
    """Tests for prompt workshop context injection into Opus system prompt."""

    def test_build_opus_system_prompt_includes_workshop_context_and_truncates_draft(self):
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import ChatContext, PromptWorkshopContext

        draft = "A" * 12050
        context = ChatContext(
            active_tab="prompt_workshop",
            prompt_workshop=PromptWorkshopContext(
                parent_agent_id="gene",
                parent_agent_name="Gene Validation",
                custom_agent_id="ca_123",
                custom_agent_name="Gene Custom v3",
                include_mod_rules=True,
                selected_mod_id="WB",
                prompt_draft=draft,
                parent_prompt_stale=True,
                parent_exists=True,
            ),
        )

        system_prompt = api_module._build_opus_system_prompt(context)

        assert "<prompt_workshop_context>" in system_prompt
        assert "Current Context: Prompt Workshop" in system_prompt
        assert "Parent agent: Gene Validation" in system_prompt
        assert "Custom agent: Gene Custom v3" in system_prompt
        assert "Selected MOD: WB" in system_prompt
        assert "<workshop_prompt_draft>" in system_prompt
        assert "Truncated to first 12000 chars for context." in system_prompt
        assert "Prompt injection note:" in system_prompt
