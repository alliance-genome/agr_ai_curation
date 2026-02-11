"""Tests for custom-agent API endpoints."""

import asyncio
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse


class TestCustomAgentTestEndpoint:
    """Unit tests for POST /api/agent-studio/custom-agents/{id}/test."""

    def test_flatten_runner_event_merges_data_and_audit_fields(self):
        from src.api.agent_studio_custom import _flatten_runner_event

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

    def test_test_endpoint_requires_document_for_document_dependent_agent(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        custom_agent_id = uuid.uuid4()

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda _db, _uuid, _uid: SimpleNamespace(id=custom_agent_id),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_runtime_info",
            lambda _aid, db=None: SimpleNamespace(
                parent_exists=True,
                requires_document=True,
                parent_agent_key="pdf",
            ),
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.test_custom_agent_endpoint(
                    custom_agent_id=custom_agent_id,
                    request=api_module.TestCustomAgentRequest(input="test query"),
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )

        assert exc_info.value.status_code == 400
        assert "requires a document_id" in str(exc_info.value.detail)

    def test_test_endpoint_streams_runner_events(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        custom_agent_id = uuid.uuid4()

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda _db, _uuid, _uid: SimpleNamespace(id=custom_agent_id),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_runtime_info",
            lambda _aid, db=None: SimpleNamespace(
                parent_exists=True,
                requires_document=False,
                parent_agent_key="gene",
            ),
        )
        monkeypatch.setattr(api_module, "get_agent_by_id", lambda _aid, **_kwargs: object())

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-123"}}
            yield {"type": "TEXT_MESSAGE_CONTENT", "data": {"delta": "hello"}}
            yield {
                "type": "RUN_FINISHED",
                "data": {"response": "hello", "trace_id": "trace-123"},
            }

        monkeypatch.setattr(api_module, "run_agent_streamed", _fake_run_agent_streamed)

        response = asyncio.run(
            api_module.test_custom_agent_endpoint(
                custom_agent_id=custom_agent_id,
                request=api_module.TestCustomAgentRequest(input="test query"),
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
