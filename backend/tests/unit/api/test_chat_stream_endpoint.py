"""Unit tests for /api/chat/stream endpoint lifecycle handling."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import ANY

from fastapi.responses import StreamingResponse
import pytest

from src.api import chat


async def _consume_stream(response: StreamingResponse) -> list[dict]:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)

    payloads = []
    for line in "".join(chunks).splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def test_chat_stream_endpoint_has_idempotent_cleanup_background_task(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    calls = {"register": [], "unregister": [], "clear": []}

    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "get_supervisor_tool_agent_map", lambda: {})
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])
    monkeypatch.setattr(chat, "conversation_manager", SimpleNamespace(add_exchange=lambda *_args, **_kwargs: None))

    async def _register_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        calls["register"].append((session_id, user_id, stream_token))
        return True

    async def _unregister_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        calls["unregister"].append((session_id, user_id, stream_token))

    async def _clear_cancel_signal(session_id: str):
        calls["clear"].append(session_id)

    async def _check_cancel_signal(_session_id: str) -> bool:
        return False

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-123"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    monkeypatch.setattr(chat, "register_active_stream", _register_active_stream)
    monkeypatch.setattr(chat, "unregister_active_stream", _unregister_active_stream)
    monkeypatch.setattr(chat, "clear_cancel_signal", _clear_cancel_signal)
    monkeypatch.setattr(chat, "check_cancel_signal", _check_cancel_signal)
    monkeypatch.setattr(chat, "run_agent_streamed", _run_agent_streamed)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="hello", session_id="session-chat-stream"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    assert isinstance(response, StreamingResponse)
    assert response.background is not None

    events = asyncio.run(_consume_stream(response))
    assert [event["type"] for event in events] == ["RUN_STARTED", "RUN_FINISHED"]

    # Explicitly invoke response background task to verify cleanup remains idempotent.
    asyncio.run(response.background())

    assert calls["register"] == [("session-chat-stream", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-chat-stream", "auth-sub", ANY)]
    assert calls["clear"] == ["session-chat-stream"]
    assert "session-chat-stream" not in chat._LOCAL_CANCEL_EVENTS
    assert "session-chat-stream" not in chat._LOCAL_SESSION_OWNERS


def test_chat_stream_endpoint_rejects_same_user_when_session_already_active(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()
    existing_event = asyncio.Event()
    chat._LOCAL_SESSION_OWNERS["session-active-same-user"] = "auth-sub"
    chat._LOCAL_CANCEL_EVENTS["session-active-same-user"] = existing_event

    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "get_supervisor_tool_agent_map", lambda: {})
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.chat_stream_endpoint(
                chat_message=chat.ChatMessage(message="hello", session_id="session-active-same-user"),
                user={"sub": "auth-sub", "cognito:groups": []},
            )
        )

    assert exc.value.status_code == 409
    assert chat._LOCAL_SESSION_OWNERS["session-active-same-user"] == "auth-sub"
    assert chat._LOCAL_CANCEL_EVENTS["session-active-same-user"] is existing_event


def test_chat_stream_endpoint_persists_extraction_envelopes_after_success(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    persisted_requests = []

    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(
        chat,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: {"id": "doc-1", "filename": "paper.pdf"}),
    )
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])
    monkeypatch.setattr(chat, "conversation_manager", SimpleNamespace(add_exchange=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(
        chat,
        "get_supervisor_tool_agent_map",
        lambda: {"ask_gene_expression_specialist": "gene-expression"},
    )

    async def _register_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        return True

    async def _unregister_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        return None

    async def _clear_cancel_signal(_session_id: str):
        return None

    async def _check_cancel_signal(_session_id: str) -> bool:
        return False

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-123"}}
        yield {
            "type": "TOOL_COMPLETE",
            "details": {"toolName": "ask_gene_expression_specialist"},
            "internal": {
                "tool_output": json.dumps(
                    {
                        "actor": "gene_expression_specialist",
                        "destination": "gene_expression",
                        "confidence": 0.9,
                        "reasoning": "done",
                        "items": [{"label": "notch"}],
                        "raw_mentions": [],
                        "exclusions": [],
                        "ambiguities": [],
                        "run_summary": {
                            "candidate_count": 1,
                            "kept_count": 1,
                            "excluded_count": 0,
                            "ambiguous_count": 0,
                            "warnings": [],
                        },
                    }
                )
            },
        }
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    monkeypatch.setattr(chat, "register_active_stream", _register_active_stream)
    monkeypatch.setattr(chat, "unregister_active_stream", _unregister_active_stream)
    monkeypatch.setattr(chat, "clear_cancel_signal", _clear_cancel_signal)
    monkeypatch.setattr(chat, "check_cancel_signal", _check_cancel_signal)
    monkeypatch.setattr(chat, "run_agent_streamed", _run_agent_streamed)
    monkeypatch.setattr(chat, "persist_extraction_results", lambda requests: persisted_requests.extend(requests))

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="Extract findings", session_id="session-chat-persist"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == ["RUN_STARTED", "TOOL_COMPLETE", "RUN_FINISHED"]
    assert len(persisted_requests) == 1
    persisted_request = persisted_requests[0]
    assert persisted_request.document_id == "doc-1"
    assert persisted_request.agent_key == "gene-expression"
    assert persisted_request.source_kind is chat.CurationExtractionSourceKind.CHAT
    assert persisted_request.origin_session_id == "session-chat-persist"
    assert persisted_request.trace_id == "trace-123"
    assert persisted_request.user_id == "auth-sub"
    assert persisted_request.candidate_count == 1
    assert persisted_request.adapter_key == "gene_expression"
    assert persisted_request.domain_key == "gene_expression"
    assert persisted_request.metadata["tool_name"] == "ask_gene_expression_specialist"


def test_chat_stream_endpoint_emits_run_error_when_extraction_persistence_fails(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    add_calls = []

    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(
        chat,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: {"id": "doc-1", "filename": "paper.pdf"}),
    )
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])
    monkeypatch.setattr(
        chat,
        "conversation_manager",
        SimpleNamespace(add_exchange=lambda *args, **kwargs: add_calls.append((args, kwargs))),
    )
    monkeypatch.setattr(
        chat,
        "get_supervisor_tool_agent_map",
        lambda: {"ask_gene_expression_specialist": "gene-expression"},
    )

    async def _register_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        return True

    async def _unregister_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        return None

    async def _clear_cancel_signal(_session_id: str):
        return None

    async def _check_cancel_signal(_session_id: str) -> bool:
        return False

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-123"}}
        yield {
            "type": "TOOL_COMPLETE",
            "details": {"toolName": "ask_gene_expression_specialist"},
            "internal": {
                "tool_output": json.dumps(
                    {
                        "actor": "gene_expression_specialist",
                        "destination": "gene_expression",
                        "confidence": 0.9,
                        "reasoning": "done",
                        "items": [{"label": "notch"}],
                        "raw_mentions": [],
                        "exclusions": [],
                        "ambiguities": [],
                        "run_summary": {
                            "candidate_count": 1,
                            "kept_count": 1,
                            "excluded_count": 0,
                            "ambiguous_count": 0,
                            "warnings": [],
                        },
                    }
                )
            },
        }
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    monkeypatch.setattr(chat, "register_active_stream", _register_active_stream)
    monkeypatch.setattr(chat, "unregister_active_stream", _unregister_active_stream)
    monkeypatch.setattr(chat, "clear_cancel_signal", _clear_cancel_signal)
    monkeypatch.setattr(chat, "check_cancel_signal", _check_cancel_signal)
    monkeypatch.setattr(chat, "run_agent_streamed", _run_agent_streamed)

    def _raise_persistence(_requests):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(chat, "persist_extraction_results", _raise_persistence)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="Extract findings", session_id="session-chat-persist-fail"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    event_types = [event["type"] for event in events]
    assert event_types == ["RUN_STARTED", "TOOL_COMPLETE", "SUPERVISOR_ERROR", "RUN_ERROR"]
    assert "RUN_FINISHED" not in event_types
    assert events[-1]["error_type"] == "RuntimeError"
    assert add_calls == []


def test_chat_stream_endpoint_raises_when_tool_map_resolution_fails(monkeypatch):
    """Regression: ALL-137 — tool-map resolution failure must fail closed, not silently disable extraction."""
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(
        chat,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: {"id": "doc-1", "filename": "paper.pdf"}),
    )
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])
    monkeypatch.setattr(chat, "conversation_manager", SimpleNamespace(add_exchange=lambda *_args, **_kwargs: None))

    def _raise_tool_map():
        raise RuntimeError("agent registry unavailable")

    monkeypatch.setattr(chat, "get_supervisor_tool_agent_map", _raise_tool_map)

    # Stream infrastructure should never be reached; provide sentinels to verify.
    stream_infra_called = False

    async def _register_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        nonlocal stream_infra_called
        stream_infra_called = True
        return True

    monkeypatch.setattr(chat, "register_active_stream", _register_active_stream)

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.chat_stream_endpoint(
                chat_message=chat.ChatMessage(message="hello", session_id="session-toolmap-fail"),
                user={"sub": "auth-sub", "cognito:groups": []},
            )
        )

    assert exc.value.status_code == 500
    assert "Internal configuration error" in exc.value.detail
    assert not stream_infra_called, "Stream registration should not run when tool-map resolution fails"
