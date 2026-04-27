"""Unit tests for /api/chat/stream endpoint lifecycle handling."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import ANY
from uuid import uuid4

from fastapi.responses import StreamingResponse
import pytest

from src.api import chat, chat_common, chat_stream
from src.lib.curation_workspace import extraction_results as extraction_results_module
from src.lib.openai_agents.evidence_summary import build_evidence_record_id


_CHAT_IMPLEMENTATION_MODULES = (chat_common, chat_stream)


def _patch_chat_impl(monkeypatch, name: str, value) -> None:
    patched = False
    for module in _CHAT_IMPLEMENTATION_MODULES:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            patched = True
    if not patched:
        raise AttributeError(name)


async def _consume_stream(response: StreamingResponse) -> list[dict]:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)

    payloads = []
    for line in "".join(chunks).splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def _expected_evidence_record() -> dict[str, object]:
    record = {
        "entity": "crumb",
        "verified_quote": "Crumb is essential for maintaining epithelial polarity.",
        "page": 4,
        "section": "Results",
        "subsection": "Gene Expression Analysis",
        "chunk_id": "abc123",
        "figure_reference": "Figure 2A",
    }
    record["evidence_record_id"] = build_evidence_record_id(evidence_record=record)
    return record


def _db_stub(*, commits: list[str] | None = None, rollbacks: list[str] | None = None):
    return SimpleNamespace(
        commit=lambda: commits.append("commit") if commits is not None else None,
        rollback=lambda: rollbacks.append("rollback") if rollbacks is not None else None,
    )


def _assistant_record(*, session_id: str, turn_id: str, content: str, trace_id: str | None = None):
    return chat.ChatMessageRecord(
        message_id=uuid4(),
        session_id=session_id,
        chat_kind=chat.ASSISTANT_CHAT_KIND,
        turn_id=turn_id,
        role="assistant",
        message_type="text",
        content=content,
        payload_json=None,
        trace_id=trace_id,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture(autouse=True)
def _stub_stream_turn_persistence(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: object())
    _patch_chat_impl(
        monkeypatch,
        "_resolve_session_create_active_document",
        lambda **_kwargs: (None, None),
    )
    _patch_chat_impl(
        monkeypatch,
        "_backfill_chat_session_generated_title",
        lambda *_args, **_kwargs: None,
    )

    def _prepare(
        *,
        repository,
        db,
        session_id: str,
        user_id: str,
        user_message: str,
        requested_turn_id: str | None,
        active_document_id,
    ):
        return chat.PreparedChatStreamTurn(
            turn_id=requested_turn_id or "generated-turn",
            effective_user_message=user_message,
            context_messages=[{"role": "user", "content": user_message}],
        )

    def _finalize(
        *,
        session_id: str,
        user_id: str,
        turn_id: str,
        user_message: str,
        assistant_message: str,
        trace_id: str | None,
        extraction_candidates,
        document_id: str | None,
    ):
        chat._persist_extraction_candidates(
            candidates=extraction_candidates,
            document_id=document_id,
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            source_kind=chat.CurationExtractionSourceKind.CHAT,
        )
        return _assistant_record(
            session_id=session_id,
            turn_id=turn_id,
            content=assistant_message,
            trace_id=trace_id,
        )

    _patch_chat_impl(monkeypatch, "_prepare_chat_stream_turn", _prepare)
    _patch_chat_impl(monkeypatch, "_persist_completed_chat_stream_turn", _finalize)
    yield
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()


def test_chat_stream_endpoint_has_idempotent_cleanup_background_task(monkeypatch):
    calls = {"register": [], "unregister": [], "clear": []}

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

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

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="hello", session_id="session-chat-stream"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    assert isinstance(response, StreamingResponse)
    assert response.background is not None

    events = asyncio.run(_consume_stream(response))
    assert [event["type"] for event in events] == ["RUN_STARTED", "turn_completed"]

    # Explicitly invoke response background task to verify cleanup remains idempotent.
    asyncio.run(response.background())

    assert calls["register"] == [("session-chat-stream", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-chat-stream", "auth-sub", ANY)]
    assert calls["clear"] == ["session-chat-stream"]
    assert "session-chat-stream" not in chat._LOCAL_CANCEL_EVENTS
    assert "session-chat-stream" not in chat._LOCAL_SESSION_OWNERS


def test_chat_stream_endpoint_sanitizes_prepare_turn_validation_error(monkeypatch, caplog):
    calls = {"register": [], "unregister": [], "clear": []}

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})

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

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)

    def _raise_prepare(**_kwargs):
        raise ValueError("stream turn invalid because hidden detail leaked")

    _patch_chat_impl(monkeypatch, "_prepare_chat_stream_turn", _raise_prepare)
    caplog.set_level(logging.WARNING, logger=chat.logger.name)

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.chat_stream_endpoint(
                chat_message=chat.ChatMessage(message="hello", session_id="session-stream-invalid"),
                user={"sub": "auth-sub", "cognito:groups": []},
                db=_db_stub(rollbacks=[]),
            )
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid chat request"
    assert "stream turn invalid because hidden detail leaked" in caplog.text
    assert calls["register"] == [("session-stream-invalid", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-stream-invalid", "auth-sub", ANY)]
    assert calls["clear"] == ["session-stream-invalid"]


def test_chat_stream_endpoint_background_backfill_uses_final_assistant_aware_title(monkeypatch):
    captured_backfill_calls = []

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(
        monkeypatch,
        "_build_context_messages_from_durable_messages",
        lambda *_args, **_kwargs: (
            [{"role": "user", "content": _kwargs.get("user_message", "")}]
            if _kwargs.get("user_message") is not None
            else []
        ),
    )
    _patch_chat_impl(
        monkeypatch,
        "_generate_title_from_turn",
        lambda *, user_message, assistant_message=None: (
            "assistant-aware-title" if assistant_message else "user-only-title"
        ),
    )
    _patch_chat_impl(
        monkeypatch,
        "_backfill_chat_session_generated_title",
        lambda session_id, user_id, preferred_generated_title=None: captured_backfill_calls.append(
            (session_id, user_id, preferred_generated_title)
        ),
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
        yield {"type": "RUN_FINISHED", "data": {"response": "assistant reply"}}

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(
                message="Summarize the evidence",
                session_id="session-stream-title",
            ),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == ["RUN_STARTED", "turn_completed"]
    assert captured_backfill_calls == [
        ("session-stream-title", "auth-sub", "assistant-aware-title")
    ]


def test_chat_stream_endpoint_passes_model_overrides_to_runner(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    captured = {}

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

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

    async def _run_agent_streamed(**kwargs):
        captured.update(kwargs)
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-stream", "model": "gpt-5.4-nano"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "stream complete"}}

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(
                message="hello",
                session_id="session-stream-override",
                model="gpt-5.4-nano",
                specialist_model="gpt-5.4-nano",
                supervisor_temperature=0.0,
                specialist_temperature=0.0,
                supervisor_reasoning="minimal",
                specialist_reasoning="minimal",
            ),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    assert [event["type"] for event in events] == ["RUN_STARTED", "turn_completed"]
    assert captured["supervisor_model"] == "gpt-5.4-nano"
    assert captured["specialist_model"] == "gpt-5.4-nano"
    assert captured["supervisor_temperature"] == 0.0
    assert captured["specialist_temperature"] == 0.0
    assert captured["supervisor_reasoning"] == "minimal"
    assert captured["specialist_reasoning"] == "minimal"
    assert captured["context_messages"] == [{"role": "user", "content": "hello"}]


def test_chat_stream_endpoint_leaves_model_overrides_unset_when_omitted(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    captured = {}

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

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

    async def _run_agent_streamed(**kwargs):
        captured.update(kwargs)
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-stream-defaults"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "stream complete"}}

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(
                message="hello",
                session_id="session-stream-defaults",
            ),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    assert [event["type"] for event in events] == ["RUN_STARTED", "turn_completed"]
    assert captured["supervisor_model"] is None
    assert captured["specialist_model"] is None
    assert captured["supervisor_temperature"] is None
    assert captured["specialist_temperature"] is None
    assert captured["supervisor_reasoning"] is None
    assert captured["specialist_reasoning"] is None
    assert captured["context_messages"] == [{"role": "user", "content": "hello"}]


def test_chat_stream_endpoint_rejects_same_user_when_session_already_active(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()
    existing_event = asyncio.Event()
    chat._LOCAL_SESSION_OWNERS["session-active-same-user"] = "auth-sub"
    chat._LOCAL_CANCEL_EVENTS["session-active-same-user"] = existing_event

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

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

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(
        monkeypatch,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: {"id": "doc-1", "filename": "paper.pdf"}),
    )
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))
    _patch_chat_impl(
        monkeypatch,
        "get_supervisor_tool_agent_map",
        lambda: {"ask_gene_expression_specialist": "gene-expression"},
    )
    monkeypatch.setattr(
        extraction_results_module,
        "_get_agent_curation_metadata",
        lambda _agent_key: {"adapter_key": "gene_expression", "launchable": True},
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

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)
    _patch_chat_impl(
        monkeypatch,
        "persist_extraction_results",
        lambda requests, **_kwargs: persisted_requests.extend(requests),
    )

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="Extract findings", session_id="session-chat-persist"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == ["RUN_STARTED", "TOOL_COMPLETE", "turn_completed"]
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
    assert persisted_request.metadata["tool_name"] == "ask_gene_expression_specialist"
    assert persisted_request.metadata["envelope_destination"] == "gene_expression"


def test_chat_stream_endpoint_emits_evidence_summary_after_record_evidence(monkeypatch):
    expected_record = _expected_evidence_record()
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})

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
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-evidence"}}
        yield {
            "type": "TOOL_COMPLETE",
            "details": {"toolName": "record_evidence"},
            "internal": {
                "tool_input": {
                    "entity": "crumb",
                    "chunk_id": "abc123",
                    "claimed_quote": "Crumb is essential for maintaining epithelial polarity.",
                },
                "tool_output": json.dumps(
                    {
                        "status": "verified",
                        "verified_quote": "Crumb is essential for maintaining epithelial polarity.",
                        "page": 4,
                        "section": "Results",
                        "subsection": "Gene Expression Analysis",
                        "figure_reference": "Figure 2A",
                    }
                ),
            },
        }
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="Extract verified evidence", session_id="session-chat-evidence"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == [
        "RUN_STARTED",
        "TOOL_COMPLETE",
        "evidence_summary",
        "turn_completed",
    ]
    assert events[2]["evidence_records"] == [expected_record]


def test_chat_stream_endpoint_uses_runner_emitted_evidence_summary(monkeypatch):
    expected_record = _expected_evidence_record()
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {"ask_gene_extractor_specialist": "gene_extractor"})
    _patch_chat_impl(
        monkeypatch,
        "get_agent_curation_metadata",
        lambda agent_key: {"adapter_key": "gene", "launchable": True}
        if agent_key == "gene_extractor"
        else None,
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
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-evidence-runner-summary"}}
        yield {
            "type": "TOOL_COMPLETE",
            "details": {"toolName": "record_evidence"},
            "internal": {
                "tool_input": {
                    "entity": "crumb",
                    "chunk_id": "abc123",
                    "claimed_quote": "Crumb is essential for maintaining epithelial polarity.",
                },
                "tool_output": json.dumps(
                    {
                        "status": "verified",
                        "verified_quote": "Crumb is essential for maintaining epithelial polarity.",
                        "page": 4,
                        "section": "Results",
                        "subsection": "Gene Expression Analysis",
                        "figure_reference": "Figure 2A",
                    }
                ),
            },
        }
        yield {
            "type": "evidence_summary",
            "timestamp": "2026-03-28T12:00:00Z",
            "tool_name": "ask_gene_extractor_specialist",
            "evidence_records": [expected_record],
        }
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="Extract verified evidence", session_id="session-chat-evidence-runner"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == [
        "RUN_STARTED",
        "TOOL_COMPLETE",
        "evidence_summary",
        "turn_completed",
    ]
    assert sum(1 for event in events if event.get("type") == "evidence_summary") == 1
    evidence_summary_event = next(
        (event for event in events if event.get("type") == "evidence_summary"),
        None
    )
    assert evidence_summary_event is not None
    assert evidence_summary_event["evidence_records"] == [expected_record]
    assert evidence_summary_event["curation_supported"] is True
    assert evidence_summary_event["curation_agent_key"] == "gene_extractor"
    assert evidence_summary_event["curation_adapter_key"] == "gene"


def test_chat_stream_endpoint_flattens_details_evidence_summary(monkeypatch):
    expected_record = _expected_evidence_record()
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})

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
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-evidence-legacy-summary"}}
        yield {
            "type": "TOOL_COMPLETE",
            "details": {"toolName": "record_evidence"},
            "internal": {
                "tool_input": {
                    "entity": "crumb",
                    "chunk_id": "abc123",
                    "claimed_quote": "Crumb is essential for maintaining epithelial polarity.",
                },
                "tool_output": json.dumps(
                    {
                        "status": "verified",
                        "verified_quote": "Crumb is essential for maintaining epithelial polarity.",
                        "page": 4,
                        "section": "Results",
                        "subsection": "Gene Expression Analysis",
                        "figure_reference": "Figure 2A",
                    }
                ),
            },
        }
        yield {
            "type": "evidence_summary",
            "timestamp": "2026-03-28T12:00:00Z",
            "details": {
                "evidence_records": [expected_record],
            },
        }
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="legacy evidence summary", session_id="session-chat-evidence-legacy"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == [
        "RUN_STARTED",
        "TOOL_COMPLETE",
        "evidence_summary",
        "turn_completed",
    ]

    evidence_summary_event = next(
        (event for event in events if event.get("type") == "evidence_summary"),
        None
    )
    assert evidence_summary_event is not None
    assert evidence_summary_event["evidence_records"] == [expected_record]


def test_chat_stream_endpoint_infers_scope_for_scope_free_extraction_envelopes(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    persisted_requests = []

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(
        monkeypatch,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: {"id": "doc-1", "filename": "paper.pdf"}),
    )
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))
    _patch_chat_impl(
        monkeypatch,
        "get_supervisor_tool_agent_map",
        lambda: {"ask_gene_extractor_specialist": "gene_extractor"},
    )
    monkeypatch.setattr(
        extraction_results_module,
        "_get_agent_curation_metadata",
        lambda _agent_key: {"adapter_key": "gene", "launchable": True},
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
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-456"}}
        yield {
            "type": "TOOL_COMPLETE",
            "details": {"toolName": "ask_gene_extractor_specialist"},
            "internal": {
                "tool_output": json.dumps(
                    {
                        "genes": [{"mention": "tinman"}],
                        "items": [{"label": "tinman"}],
                        "raw_mentions": [{"mention": "tinman", "evidence": []}],
                        "exclusions": [],
                        "ambiguities": [],
                        "run_summary": {
                            "candidate_count": 4,
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

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)
    _patch_chat_impl(
        monkeypatch,
        "persist_extraction_results",
        lambda requests, **_kwargs: persisted_requests.extend(requests),
    )

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="Extract focus genes", session_id="session-chat-infer"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == ["RUN_STARTED", "TOOL_COMPLETE", "turn_completed"]
    assert len(persisted_requests) == 1
    persisted_request = persisted_requests[0]
    assert persisted_request.agent_key == "gene_extractor"
    assert persisted_request.adapter_key == "gene"


def test_chat_stream_endpoint_emits_turn_failed_when_completion_side_effect_persistence_fails(monkeypatch, caplog):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(
        monkeypatch,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: {"id": "doc-1", "filename": "paper.pdf"}),
    )
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))
    _patch_chat_impl(
        monkeypatch,
        "get_supervisor_tool_agent_map",
        lambda: {"ask_gene_expression_specialist": "gene-expression"},
    )
    monkeypatch.setattr(
        extraction_results_module,
        "_get_agent_curation_metadata",
        lambda _agent_key: {"adapter_key": "gene_expression", "launchable": True},
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

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)

    def _raise_persistence(_requests, **_kwargs):
        raise RuntimeError("db unavailable")

    _patch_chat_impl(monkeypatch, "persist_extraction_results", _raise_persistence)
    caplog.set_level(logging.ERROR, logger=chat.logger.name)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="Extract findings", session_id="session-chat-persist-fail"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    event_types = [event["type"] for event in events]
    assert event_types == ["RUN_STARTED", "TOOL_COMPLETE", "SUPERVISOR_ERROR", "turn_failed"]
    assert events[2]["details"]["error"] == "Failed to save chat side effects."
    assert "db unavailable" not in json.dumps(events)
    assert "db unavailable" in caplog.text
    assert events[-1]["error_type"] == "RuntimeError"


def test_chat_stream_endpoint_emits_turn_save_failed_when_assistant_persistence_requires_rescue(monkeypatch, caplog):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})

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
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-456"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    def _raise_assistant_save_failed(**_kwargs):
        raise chat.ChatStreamAssistantSaveFailedError(
            "Failed to persist stream assistant turn"
        ) from RuntimeError("assistant row unavailable")

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)
    _patch_chat_impl(monkeypatch, "_persist_completed_chat_stream_turn", _raise_assistant_save_failed)
    caplog.set_level(logging.ERROR, logger=chat.logger.name)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="hello", session_id="session-chat-assistant-save-fail"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    event_types = [event["type"] for event in events]
    assert event_types == ["RUN_STARTED", "SUPERVISOR_ERROR", "turn_save_failed"]
    assert events[1]["details"]["error"] == "Failed to save the assistant response."
    assert "assistant row unavailable" not in json.dumps(events)
    assert "assistant row unavailable" in caplog.text
    assert events[-1]["error_type"] == "RuntimeError"


def test_chat_stream_endpoint_sanitizes_runner_run_error_event(monkeypatch, caplog):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})

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
        yield {"type": "RUN_ERROR", "data": {"message": "runner exploded", "error_type": "RuntimeError"}}

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)
    caplog.set_level(logging.ERROR, logger=chat.logger.name)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="hello", session_id="session-chat-run-error"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == ["turn_failed"]
    assert (
        events[0]["message"]
        == "An error occurred. Please provide feedback using the ⋮ menu on this message, then try your query again."
    )
    assert "runner exploded" not in json.dumps(events)
    assert "runner exploded" in caplog.text


def test_chat_stream_endpoint_emits_turn_interrupted_on_cancel_signal(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    check_calls = {"count": 0}
    finalize_calls = []

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})

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
        check_calls["count"] += 1
        return check_calls["count"] > 1

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-stop"}}
        yield {"type": "TOOL_COMPLETE", "details": {"toolName": "should-not-arrive"}}

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)
    _patch_chat_impl(
        monkeypatch,
        "_persist_completed_chat_stream_turn",
        lambda **_kwargs: finalize_calls.append("finalize"),
    )

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="hello", session_id="session-stop"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == ["RUN_STARTED", "turn_interrupted"]
    assert events[-1]["turn_id"] == "generated-turn"
    assert finalize_calls == []


def test_chat_stream_endpoint_replays_existing_assistant_turn_without_runner(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(
        monkeypatch,
        "_prepare_chat_stream_turn",
        lambda **_kwargs: chat.PreparedChatStreamTurn(
            turn_id="turn-replay",
            effective_user_message="hello",
            context_messages=[],
            replay_assistant_turn=_assistant_record(
                session_id="session-replay",
                turn_id="turn-replay",
                content="stored response",
                trace_id="trace-replay",
            ),
        ),
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
        raise AssertionError("runner should not be invoked for replayed turns")
        yield

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="hello", session_id="session-replay", turn_id="turn-replay"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == ["TEXT_MESSAGE_CONTENT", "turn_completed"]
    assert events[0]["content"] == "stored response"
    assert events[1]["turn_id"] == "turn-replay"
    assert events[1]["trace_id"] == "trace-replay"


def test_chat_stream_endpoint_emits_session_gone_when_session_disappears_before_completion_save(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})

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
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-gone"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    def _raise_session_gone(**_kwargs):
        raise chat.ChatHistorySessionNotFoundError("Chat session not found")

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, "run_agent_streamed", _run_agent_streamed)
    _patch_chat_impl(monkeypatch, "_persist_completed_chat_stream_turn", _raise_session_gone)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="hello", session_id="session-gone"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == ["RUN_STARTED", "session_gone"]
    assert events[-1]["trace_id"] == "trace-gone"


@pytest.mark.asyncio
async def test_assistant_rescue_endpoint_is_idempotent_on_turn_id(monkeypatch):
    commits: list[str] = []
    assistant_record = _assistant_record(
        session_id="session-rescue",
        turn_id="turn-rescue",
        content="rescued response",
        trace_id="trace-rescue",
    )
    created_values = iter([True, False])

    repository = SimpleNamespace(
        get_session=lambda **_kwargs: object(),
        get_message_by_turn_id=lambda **kwargs: SimpleNamespace(content="hello")
        if kwargs["role"] == "user"
        else None,
        append_message=lambda **_kwargs: SimpleNamespace(
            message=assistant_record,
            created=next(created_values),
        ),
    )

    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    first_response = await chat.assistant_rescue(
        session_id="session-rescue",
        request=chat.AssistantRescueRequest(
            turn_id="turn-rescue",
            content="rescued response",
            trace_id="trace-rescue",
        ),
        db=_db_stub(commits=commits),
        user={"sub": "auth-sub"},
    )
    second_response = await chat.assistant_rescue(
        session_id="session-rescue",
        request=chat.AssistantRescueRequest(
            turn_id="turn-rescue",
            content="rescued response",
            trace_id="trace-rescue",
        ),
        db=_db_stub(commits=commits),
        user={"sub": "auth-sub"},
    )

    assert first_response.created is True
    assert second_response.created is False
    assert first_response.trace_id == "trace-rescue"
    assert second_response.trace_id == "trace-rescue"
    assert commits == ["commit", "commit"]


@pytest.mark.asyncio
async def test_assistant_rescue_endpoint_returns_404_when_session_is_missing(monkeypatch):
    repository = SimpleNamespace(
        get_session=lambda **_kwargs: None,
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    with pytest.raises(chat.HTTPException) as exc:
        await chat.assistant_rescue(
            session_id="missing-session",
            request=chat.AssistantRescueRequest(
                turn_id="turn-rescue",
                content="rescued response",
            ),
            db=_db_stub(),
            user={"sub": "auth-sub"},
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_assistant_rescue_endpoint_sanitizes_validation_error(monkeypatch, caplog):
    repository = SimpleNamespace(
        get_session=lambda **_kwargs: SimpleNamespace(session_id="session-rescue"),
        get_message_by_turn_id=lambda **_kwargs: (_ for _ in ()).throw(ValueError("assistant rescue invariant exploded")),
    )
    rollbacks: list[str] = []
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    caplog.set_level(logging.WARNING, logger=chat.logger.name)

    with pytest.raises(chat.HTTPException) as exc:
        await chat.assistant_rescue(
            session_id="session-rescue",
            request=chat.AssistantRescueRequest(
                turn_id="turn-rescue",
                content="rescued response",
            ),
            db=_db_stub(rollbacks=rollbacks),
            user={"sub": "auth-sub"},
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid assistant rescue request"
    assert rollbacks == ["rollback"]
    assert "assistant rescue invariant exploded" in caplog.text


def test_chat_stream_endpoint_raises_when_tool_map_resolution_fails(monkeypatch):
    """Regression: ALL-137 — tool-map resolution failure must fail closed, not silently disable extraction."""
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(
        monkeypatch,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: {"id": "doc-1", "filename": "paper.pdf"}),
    )
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

    def _raise_tool_map():
        raise RuntimeError("agent registry unavailable")

    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", _raise_tool_map)

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

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)

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
