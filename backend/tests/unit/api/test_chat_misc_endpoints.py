"""Unit tests for chat misc/document/history endpoints and non-stream chat path."""

import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from src.api import chat
from src.lib.conversation_manager import SessionAccessError


@pytest.mark.asyncio
async def test_get_conversation_history_for_session_converts_exchange_format(monkeypatch):
    monkeypatch.setattr(
        chat,
        "conversation_manager",
        SimpleNamespace(
            history_enabled=True,
            get_session_history=lambda _uid, _sid: [
                {"user": "u1", "assistant": "a1"},
                {"user": "u2", "assistant": ""},
                {"user": "", "assistant": "a3"},
            ],
        ),
    )

    history = chat._get_conversation_history_for_session("user-1", "session-1")
    assert history == [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a3"},
    ]


@pytest.mark.asyncio
async def test_get_conversation_history_for_session_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setattr(chat, "conversation_manager", SimpleNamespace(history_enabled=False))
    assert chat._get_conversation_history_for_session("user-1", "session-1") == []


@pytest.mark.asyncio
async def test_load_document_for_chat_success(monkeypatch):
    captured = {}
    doc_payload = {"id": "doc-1", "filename": "paper.pdf", "chunk_count": 10}

    async def _get_document(_user_sub, _doc_id):
        return {"document": doc_payload}

    monkeypatch.setattr(chat, "get_document", _get_document)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(set_document=lambda user, doc: captured.setdefault(user, doc)))
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda user, doc_id: captured.setdefault("cache", (user, doc_id)))

    result = await chat.load_document_for_chat(chat.LoadDocumentRequest(document_id="doc-1"), {"sub": "user-1"})
    assert result.active is True
    assert result.document.id == "doc-1"
    assert captured["user-1"]["filename"] == "paper.pdf"
    assert captured["cache"] == ("user-1", "doc-1")


@pytest.mark.asyncio
async def test_load_document_for_chat_404_on_value_error(monkeypatch):
    async def _raise(*_args, **_kwargs):
        raise ValueError("missing")

    monkeypatch.setattr(chat, "get_document", _raise)

    with pytest.raises(HTTPException) as exc:
        await chat.load_document_for_chat(chat.LoadDocumentRequest(document_id="doc-404"), {"sub": "user-1"})
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_load_document_for_chat_500_when_summary_missing(monkeypatch):
    monkeypatch.setattr(chat, "get_document", lambda *_args, **_kwargs: _async_value({"not_document": {}}))

    with pytest.raises(HTTPException) as exc:
        await chat.load_document_for_chat(chat.LoadDocumentRequest(document_id="doc-1"), {"sub": "user-1"})
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_get_loaded_document_and_clear_document(monkeypatch):
    stored = {"id": "doc-1", "filename": "paper.pdf"}

    monkeypatch.setattr(
        chat,
        "document_state",
        SimpleNamespace(
            get_document=lambda _uid: stored,
            clear_document=lambda _uid: stored.clear(),
        ),
    )

    status = await chat.get_loaded_document({"sub": "user-1"})
    assert status.active is True
    assert status.document.id == "doc-1"

    cleared = await chat.clear_loaded_document({"sub": "user-1"})
    assert cleared.active is False
    assert cleared.document.id == "doc-1"


@pytest.mark.asyncio
async def test_clear_loaded_document_when_none(monkeypatch):
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    payload = await chat.clear_loaded_document({"sub": "user-1"})
    assert payload.active is False
    assert "No document was loaded" in payload.message


@pytest.mark.asyncio
async def test_create_session_returns_uuid_and_timestamp():
    payload = await chat.create_session({"sub": "user-1"})
    UUID(payload.session_id)
    assert isinstance(payload.created_at, str)


@pytest.mark.asyncio
async def test_chat_endpoint_success(monkeypatch):
    add_calls = []
    monkeypatch.setattr(chat, "set_current_session_id", lambda _sid: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _uid: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "get_supervisor_tool_agent_map", lambda: {})
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])
    monkeypatch.setattr(chat, "conversation_manager", SimpleNamespace(add_exchange=lambda *args: add_calls.append(args)))

    async def _stream(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "final answer"}}

    monkeypatch.setattr(chat, "run_agent_streamed", _stream)

    result = await chat.chat_endpoint(chat.ChatMessage(message="hello", session_id="session-1"), {"sub": "user-1", "cognito:groups": []})
    assert result.response == "final answer"
    assert result.session_id == "session-1"
    assert add_calls == [("user-1", "session-1", "hello", "final answer")]


@pytest.mark.asyncio
async def test_chat_endpoint_raises_http_401_without_user_id():
    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(chat.ChatMessage(message="hello"), {"cognito:groups": []})
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_chat_endpoint_raises_500_on_run_error_event(monkeypatch):
    monkeypatch.setattr(chat, "set_current_session_id", lambda _sid: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _uid: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "get_supervisor_tool_agent_map", lambda: {})
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])
    monkeypatch.setattr(chat, "conversation_manager", SimpleNamespace(add_exchange=lambda *_args: None))

    async def _stream(**_kwargs):
        yield {"type": "RUN_ERROR", "data": {"message": "model exploded"}}

    monkeypatch.setattr(chat, "run_agent_streamed", _stream)

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(chat.ChatMessage(message="hello", session_id="session-1"), {"sub": "user-1", "cognito:groups": []})
    assert exc.value.status_code == 500
    assert "model exploded" in exc.value.detail


@pytest.mark.asyncio
async def test_chat_endpoint_raises_500_when_extraction_persistence_fails(monkeypatch):
    add_calls = []
    monkeypatch.setattr(chat, "set_current_session_id", lambda _sid: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _uid: None)
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
        SimpleNamespace(add_exchange=lambda *args: add_calls.append(args)),
    )
    monkeypatch.setattr(
        chat,
        "get_supervisor_tool_agent_map",
        lambda: {"ask_gene_expression_specialist": "gene-expression"},
    )

    async def _stream(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
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
        yield {"type": "RUN_FINISHED", "data": {"response": "final answer"}}

    monkeypatch.setattr(chat, "run_agent_streamed", _stream)
    monkeypatch.setattr(
        chat,
        "persist_extraction_results",
        lambda _requests: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(
            chat.ChatMessage(message="hello", session_id="session-1"),
            {"sub": "user-1", "cognito:groups": []},
        )

    assert exc.value.status_code == 500
    assert "db unavailable" in exc.value.detail
    assert add_calls == []


@pytest.mark.asyncio
async def test_chat_endpoint_raises_500_when_tool_map_resolution_fails(monkeypatch):
    """Regression: ALL-137 — tool-map resolution failure must fail closed, not silently disable extraction."""
    monkeypatch.setattr(chat, "set_current_session_id", lambda _sid: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _uid: None)
    monkeypatch.setattr(
        chat,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: {"id": "doc-1", "filename": "paper.pdf"}),
    )
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "conversation_manager", SimpleNamespace(add_exchange=lambda *_args: None))

    def _raise_tool_map():
        raise RuntimeError("agent registry unavailable")

    monkeypatch.setattr(chat, "get_supervisor_tool_agent_map", _raise_tool_map)

    # run_agent_streamed should never be reached; provide a sentinel to verify.
    stream_called = False

    async def _stream_sentinel(**_kwargs):
        nonlocal stream_called
        stream_called = True
        yield {"type": "RUN_FINISHED", "data": {"response": "should not reach"}}

    monkeypatch.setattr(chat, "run_agent_streamed", _stream_sentinel)

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(
            chat.ChatMessage(message="hello", session_id="session-1"),
            {"sub": "user-1", "cognito:groups": []},
        )

    assert exc.value.status_code == 500
    assert "agent registry unavailable" in exc.value.detail
    assert not stream_called, "Agent stream should not run when tool-map resolution fails"


@pytest.mark.asyncio
async def test_chat_endpoint_wraps_unexpected_exceptions(monkeypatch):
    monkeypatch.setattr(chat, "set_current_session_id", lambda _sid: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _uid: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "get_supervisor_tool_agent_map", lambda: {})
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])
    monkeypatch.setattr(chat, "conversation_manager", SimpleNamespace(add_exchange=lambda *_args: None))

    async def _raise(**_kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(chat, "run_agent_streamed", _raise)

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(chat.ChatMessage(message="hello", session_id="session-1"), {"sub": "user-1", "cognito:groups": []})
    assert exc.value.status_code == 500
    assert "boom" in exc.value.detail


@pytest.mark.asyncio
async def test_chat_status_reflects_openai_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    payload = await chat.chat_status({"sub": "user-1"})
    assert payload["service"] == "chat"
    assert payload["openai_key_configured"] is True


@pytest.mark.asyncio
async def test_get_conversation_status_and_reset_and_history_endpoints(monkeypatch):
    manager = SimpleNamespace(
        get_memory_stats=lambda _uid: {"is_active": True, "conversation_id": "c-1"},
        reset_conversation=lambda _uid: True,
        get_session_stats=lambda _uid, sid: {
            "session_id": sid,
            "exchange_count": 1,
            "max_exchanges": 30,
            "history": [{"user": "u", "assistant": "a"}],
        },
        clear_session_history=lambda _uid, _sid: None,
        get_all_sessions_stats=lambda _uid: {
            "total_sessions": 1,
            "max_sessions": 10,
            "history_enabled": True,
            "max_exchanges_per_session": 30,
            "sessions": ["s-1"],
        },
        history_enabled=True,
        max_exchanges=30,
        include_in_routing=True,
        include_in_response=True,
        max_sessions_per_user=10,
    )
    monkeypatch.setattr(chat, "conversation_manager", manager)

    status = await chat.get_conversation_status({"sub": "user-1"})
    assert status.is_active is True
    reset = await chat.reset_conversation({"sub": "user-1"})
    assert reset.success is True
    assert reset.session_id is not None
    history = await chat.get_session_history("s-1", {"sub": "user-1"})
    assert history.exchange_count == 1
    cleared = await chat.clear_session_history("s-1", {"sub": "user-1"})
    assert "History cleared" in cleared["message"]
    all_stats = await chat.get_all_sessions_stats({"sub": "user-1"})
    assert all_stats.total_sessions == 1
    config = await chat.get_chat_configuration({"sub": "user-1"})
    assert config.history["enabled"] is True


@pytest.mark.asyncio
async def test_conversation_endpoints_require_user_sub():
    with pytest.raises(HTTPException) as exc_status:
        await chat.get_conversation_status({})
    assert exc_status.value.status_code == 401

    with pytest.raises(HTTPException) as exc_reset:
        await chat.reset_conversation({})
    assert exc_reset.value.status_code == 401

    with pytest.raises(HTTPException) as exc_hist:
        await chat.get_session_history("s-1", {})
    assert exc_hist.value.status_code == 401

    with pytest.raises(HTTPException) as exc_clear:
        await chat.clear_session_history("s-1", {})
    assert exc_clear.value.status_code == 401

    with pytest.raises(HTTPException) as exc_all:
        await chat.get_all_sessions_stats({})
    assert exc_all.value.status_code == 401


@pytest.mark.asyncio
async def test_session_history_and_clear_raise_403_on_access_error(monkeypatch):
    manager = SimpleNamespace(
        get_session_stats=lambda _uid, _sid: (_ for _ in ()).throw(SessionAccessError("denied")),
        clear_session_history=lambda _uid, _sid: (_ for _ in ()).throw(SessionAccessError("denied")),
    )
    monkeypatch.setattr(chat, "conversation_manager", manager)

    with pytest.raises(HTTPException) as exc_hist:
        await chat.get_session_history("s-denied", {"sub": "user-1"})
    assert exc_hist.value.status_code == 403

    with pytest.raises(HTTPException) as exc_clear:
        await chat.clear_session_history("s-denied", {"sub": "user-1"})
    assert exc_clear.value.status_code == 403


def _async_value(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner()
