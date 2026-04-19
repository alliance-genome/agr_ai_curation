"""Unit tests for chat misc/document/history endpoints and non-stream chat path."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.api import chat
from src.lib.chat_history_repository import (
    ChatSessionRecord,
    ChatSessionPage,
    ChatSessionCursor,
    ChatSessionDetail,
    ChatMessageRecord,
)
from src.lib.curation_workspace import extraction_results as extraction_results_module


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 4, 19, hour, minute, tzinfo=timezone.utc)


def _session_record(
    *,
    session_id: str,
    user_auth_sub: str = "user-1",
    title: str | None = None,
    active_document_id: UUID | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    last_message_at: datetime | None = None,
) -> ChatSessionRecord:
    created_value = created_at or _ts(9, 0)
    return ChatSessionRecord(
        session_id=session_id,
        user_auth_sub=user_auth_sub,
        title=title,
        active_document_id=active_document_id,
        created_at=created_value,
        updated_at=updated_at or created_value,
        last_message_at=last_message_at,
        deleted_at=None,
    )


class FakeChatHistoryRepository:
    def __init__(
        self,
        *,
        sessions: list[ChatSessionRecord] | None = None,
        detail_messages: dict[tuple[str, str], list[ChatMessageRecord]] | None = None,
    ) -> None:
        self.sessions = {
            (record.user_auth_sub, record.session_id): record
            for record in (sessions or [])
        }
        self.detail_messages = detail_messages or {}
        self.create_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self.count_calls: list[dict[str, object]] = []
        self.rename_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []

    def create_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        title: str | None = None,
        active_document_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ChatSessionRecord:
        record = _session_record(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            title=title,
            active_document_id=active_document_id,
            created_at=created_at or _ts(10, 0),
            updated_at=created_at or _ts(10, 0),
        )
        self.sessions[(user_auth_sub, session_id)] = record
        self.create_calls.append(
            {
                "session_id": session_id,
                "user_auth_sub": user_auth_sub,
                "title": title,
                "active_document_id": active_document_id,
            }
        )
        return record

    def get_session_detail(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        message_limit: int = 100,
        message_cursor=None,
    ) -> ChatSessionDetail | None:
        if not session_id.strip():
            raise ValueError("session_id is required")
        session = self.sessions.get((user_auth_sub, session_id))
        if session is None:
            return None
        messages = list(self.detail_messages.get((user_auth_sub, session_id), []))
        return ChatSessionDetail(
            session=session,
            messages=messages[:message_limit],
            next_message_cursor=None,
        )

    def list_sessions(
        self,
        *,
        user_auth_sub: str,
        limit: int = 20,
        cursor: ChatSessionCursor | None = None,
        active_document_id: UUID | None = None,
    ) -> ChatSessionPage:
        self.list_calls.append(
            {
                "user_auth_sub": user_auth_sub,
                "limit": limit,
                "cursor": cursor,
                "active_document_id": active_document_id,
            }
        )
        items = self._visible_sessions(
            user_auth_sub=user_auth_sub,
            active_document_id=active_document_id,
        )
        return ChatSessionPage(items=items[:limit], next_cursor=None)

    def search_sessions(
        self,
        *,
        user_auth_sub: str,
        query: str,
        limit: int = 20,
        cursor: ChatSessionCursor | None = None,
        active_document_id: UUID | None = None,
    ) -> ChatSessionPage:
        self.search_calls.append(
            {
                "user_auth_sub": user_auth_sub,
                "query": query,
                "limit": limit,
                "cursor": cursor,
                "active_document_id": active_document_id,
            }
        )
        items = self._visible_sessions(
            user_auth_sub=user_auth_sub,
            active_document_id=active_document_id,
            query=query,
        )
        return ChatSessionPage(items=items[:limit], next_cursor=None)

    def count_sessions(
        self,
        *,
        user_auth_sub: str,
        query: str | None = None,
        active_document_id: UUID | None = None,
    ) -> int:
        self.count_calls.append(
            {
                "user_auth_sub": user_auth_sub,
                "query": query,
                "active_document_id": active_document_id,
            }
        )
        return len(
            self._visible_sessions(
                user_auth_sub=user_auth_sub,
                active_document_id=active_document_id,
                query=query,
            )
        )

    def rename_session(self, *, session_id: str, user_auth_sub: str, title: str) -> ChatSessionRecord | None:
        self.rename_calls.append(
            {"session_id": session_id, "user_auth_sub": user_auth_sub, "title": title}
        )
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("title is required")
        session = self.sessions.get((user_auth_sub, session_id))
        if session is None:
            return None
        updated = _session_record(
            session_id=session.session_id,
            user_auth_sub=session.user_auth_sub,
            title=normalized_title,
            active_document_id=session.active_document_id,
            created_at=session.created_at,
            updated_at=_ts(11, 15),
            last_message_at=session.last_message_at,
        )
        self.sessions[(user_auth_sub, session_id)] = updated
        return updated

    def soft_delete_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        deleted_at: datetime | None = None,
    ) -> bool:
        self.delete_calls.append(
            {"session_id": session_id, "user_auth_sub": user_auth_sub, "deleted_at": deleted_at}
        )
        return self.sessions.pop((user_auth_sub, session_id), None) is not None

    def _visible_sessions(
        self,
        *,
        user_auth_sub: str,
        active_document_id: UUID | None = None,
        query: str | None = None,
    ) -> list[ChatSessionRecord]:
        sessions = [
            record
            for (owner, _session_id), record in self.sessions.items()
            if owner == user_auth_sub
        ]
        if active_document_id is not None:
            sessions = [
                record for record in sessions if record.active_document_id == active_document_id
            ]
        if query:
            normalized_query = query.lower()
            sessions = [
                record
                for record in sessions
                if normalized_query in (record.title or "").lower()
            ]
        return sorted(
            sessions,
            key=lambda record: (record.recent_activity_at, record.session_id),
            reverse=True,
        )


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
async def test_create_session_returns_uuid_and_persists_active_document(monkeypatch):
    repository = FakeChatHistoryRepository()
    commits: list[str] = []
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)
    monkeypatch.setattr(
        chat,
        "document_state",
        SimpleNamespace(
            get_document=lambda _uid: {
                "id": "8b7be2ce-2f34-4c30-8f47-26a8cb5cd1a8",
                "filename": "paper.pdf",
                "chunk_count": 11,
            }
        ),
    )

    payload = await chat.create_session(
        SimpleNamespace(commit=lambda: commits.append("commit"), rollback=lambda: None),
        {"sub": "user-1"},
    )

    UUID(payload.session_id)
    assert payload.active_document_id == "8b7be2ce-2f34-4c30-8f47-26a8cb5cd1a8"
    assert payload.active_document.filename == "paper.pdf"
    assert commits == ["commit"]
    assert repository.create_calls[0]["user_auth_sub"] == "user-1"
    assert str(repository.create_calls[0]["active_document_id"]) == payload.active_document_id


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
async def test_chat_endpoint_uses_last_run_finished_response(monkeypatch):
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
        yield {"type": "RUN_FINISHED", "data": {"response": "intermediate answer"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "final stabilized answer"}}

    monkeypatch.setattr(chat, "run_agent_streamed", _stream)

    result = await chat.chat_endpoint(
        chat.ChatMessage(message="hello", session_id="session-2"),
        {"sub": "user-1", "cognito:groups": []},
    )

    assert result.response == "final stabilized answer"
    assert add_calls == [("user-1", "session-2", "hello", "final stabilized answer")]


@pytest.mark.asyncio
async def test_chat_endpoint_passes_model_overrides_to_runner(monkeypatch):
    captured = {}
    monkeypatch.setattr(chat, "set_current_session_id", lambda _sid: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _uid: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "get_supervisor_tool_agent_map", lambda: {})
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])
    monkeypatch.setattr(chat, "conversation_manager", SimpleNamespace(add_exchange=lambda *_args: None))

    async def _stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "final answer from overrides"}}

    monkeypatch.setattr(chat, "run_agent_streamed", _stream)

    await chat.chat_endpoint(
        chat.ChatMessage(
            message="hello",
            session_id="session-override",
            model="gpt-5.4-nano",
            specialist_model="gpt-5.4-nano",
            supervisor_temperature=0.0,
            specialist_temperature=0.0,
            supervisor_reasoning="minimal",
            specialist_reasoning="minimal",
        ),
        {"sub": "user-1", "cognito:groups": []},
    )

    assert captured["supervisor_model"] == "gpt-5.4-nano"
    assert captured["specialist_model"] == "gpt-5.4-nano"
    assert captured["supervisor_temperature"] == 0.0
    assert captured["specialist_temperature"] == 0.0
    assert captured["supervisor_reasoning"] == "minimal"
    assert captured["specialist_reasoning"] == "minimal"


@pytest.mark.asyncio
async def test_chat_endpoint_leaves_model_overrides_unset_when_omitted(monkeypatch):
    captured = {}
    monkeypatch.setattr(chat, "set_current_session_id", lambda _sid: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _uid: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "get_supervisor_tool_agent_map", lambda: {})
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])
    monkeypatch.setattr(chat, "conversation_manager", SimpleNamespace(add_exchange=lambda *_args: None))

    async def _stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-defaults"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "final answer from config defaults"}}

    monkeypatch.setattr(chat, "run_agent_streamed", _stream)

    await chat.chat_endpoint(
        chat.ChatMessage(message="hello", session_id="session-defaults"),
        {"sub": "user-1", "cognito:groups": []},
    )

    assert captured["supervisor_model"] is None
    assert captured["specialist_model"] is None
    assert captured["supervisor_temperature"] is None
    assert captured["specialist_temperature"] is None
    assert captured["supervisor_reasoning"] is None
    assert captured["specialist_reasoning"] is None


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
    monkeypatch.setattr(
        extraction_results_module,
        "_get_agent_curation_metadata",
        lambda _agent_key: {"adapter_key": "gene_expression", "launchable": True},
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
    assert "Internal configuration error" in exc.value.detail
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
async def test_get_conversation_status_and_reset_and_config_endpoints(monkeypatch):
    manager = SimpleNamespace(
        get_memory_stats=lambda _uid: {"is_active": True, "conversation_id": "c-1"},
        reset_conversation=lambda _uid: True,
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
        await chat.get_session_history("s-1", db=object(), user={})
    assert exc_hist.value.status_code == 401

    with pytest.raises(HTTPException) as exc_list:
        await chat.get_all_sessions_stats(db=object(), user={})
    assert exc_list.value.status_code == 401

    with pytest.raises(HTTPException) as exc_rename:
        await chat.rename_session("s-1", chat.RenameSessionRequest(title="Renamed"), db=object(), user={})
    assert exc_rename.value.status_code == 401


@pytest.mark.asyncio
async def test_get_all_sessions_stats_returns_filtered_search_results(monkeypatch):
    document_a = UUID("8b7be2ce-2f34-4c30-8f47-26a8cb5cd1a8")
    document_b = UUID("6a5229e4-0546-4311-a1c1-f0ca5057ae3b")
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(
                session_id="session-newest",
                title="Alpha summary",
                active_document_id=document_a,
                created_at=_ts(9, 0),
                last_message_at=_ts(12, 0),
            ),
            _session_record(
                session_id="session-middle",
                title="Beta notes",
                active_document_id=document_b,
                created_at=_ts(10, 0),
                last_message_at=_ts(11, 0),
            ),
            _session_record(
                session_id="session-other-user",
                user_auth_sub="user-2",
                title="Alpha hidden",
                active_document_id=document_a,
                created_at=_ts(8, 0),
                last_message_at=_ts(13, 0),
            ),
        ]
    )
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)

    filtered = await chat.get_all_sessions_stats(
        limit=10,
        cursor=None,
        query=None,
        document_id=str(document_a),
        db=object(),
        user={"sub": "user-1"},
    )
    assert filtered.total_sessions == 1
    assert [session.session_id for session in filtered.sessions] == ["session-newest"]
    assert repository.list_calls[0]["active_document_id"] == document_a

    searched = await chat.get_all_sessions_stats(
        limit=10,
        cursor=None,
        query="Alpha",
        document_id=None,
        db=object(),
        user={"sub": "user-1"},
    )
    assert searched.total_sessions == 1
    assert [session.session_id for session in searched.sessions] == ["session-newest"]
    assert repository.search_calls[0]["query"] == "Alpha"


@pytest.mark.asyncio
async def test_get_all_sessions_stats_returns_empty_state(monkeypatch):
    repository = FakeChatHistoryRepository()
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)

    payload = await chat.get_all_sessions_stats(
        limit=20,
        cursor=None,
        query=None,
        document_id=None,
        db=object(),
        user={"sub": "user-1"},
    )

    assert payload.total_sessions == 0
    assert payload.sessions == []
    assert payload.next_cursor is None


@pytest.mark.asyncio
async def test_get_session_history_returns_durable_detail_with_active_document(monkeypatch):
    active_document_id = UUID("8b7be2ce-2f34-4c30-8f47-26a8cb5cd1a8")
    message_id = uuid4()
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(
                session_id="session-detail",
                title="Resume me",
                active_document_id=active_document_id,
                created_at=_ts(9, 0),
                last_message_at=_ts(9, 30),
            )
        ],
        detail_messages={
            ("user-1", "session-detail"): [
                ChatMessageRecord(
                    message_id=message_id,
                    session_id="session-detail",
                    turn_id="turn-1",
                    role="assistant",
                    message_type="text",
                    content="Detailed reply",
                    payload_json={"step": "answer"},
                    trace_id="trace-1",
                    created_at=_ts(9, 31),
                )
            ]
        },
    )
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)
    monkeypatch.setattr(
        chat,
        "get_document",
        lambda _user_id, _document_id: _async_value(
            {"document": {"id": str(active_document_id), "filename": "paper.pdf", "chunk_count": 5}}
        ),
    )

    payload = await chat.get_session_history(
        "session-detail",
        message_limit=50,
        message_cursor=None,
        db=object(),
        user={"sub": "user-1"},
    )

    assert payload.session.session_id == "session-detail"
    assert payload.active_document.filename == "paper.pdf"
    assert payload.messages[0].message_id == str(message_id)
    assert payload.messages[0].payload_json == {"step": "answer"}


@pytest.mark.asyncio
async def test_get_session_history_returns_null_active_document_when_document_is_missing(monkeypatch):
    active_document_id = UUID("8b7be2ce-2f34-4c30-8f47-26a8cb5cd1a8")
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(
                session_id="session-detail",
                title="Resume me",
                active_document_id=active_document_id,
            )
        ]
    )
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)

    async def _raise_not_found(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="Document not found")

    monkeypatch.setattr(chat, "get_document", _raise_not_found)

    payload = await chat.get_session_history(
        "session-detail",
        message_limit=50,
        message_cursor=None,
        db=object(),
        user={"sub": "user-1"},
    )

    assert payload.session.session_id == "session-detail"
    assert payload.active_document is None


@pytest.mark.asyncio
async def test_get_session_history_propagates_unexpected_document_lookup_failures(monkeypatch):
    active_document_id = UUID("8b7be2ce-2f34-4c30-8f47-26a8cb5cd1a8")
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(
                session_id="session-detail",
                title="Resume me",
                active_document_id=active_document_id,
            )
        ]
    )
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)

    async def _raise_server_error(*_args, **_kwargs):
        raise HTTPException(status_code=500, detail="Document service failure")

    monkeypatch.setattr(chat, "get_document", _raise_server_error)

    with pytest.raises(HTTPException) as exc:
        await chat.get_session_history(
            "session-detail",
            message_limit=50,
            message_cursor=None,
            db=object(),
            user={"sub": "user-1"},
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == "Document service failure"


@pytest.mark.asyncio
async def test_rename_session_updates_title(monkeypatch):
    commits: list[str] = []
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(session_id="session-rename", user_auth_sub="user-1", title="Original"),
        ]
    )
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)

    payload = await chat.rename_session(
        "session-rename",
        chat.RenameSessionRequest(title="  Renamed title  "),
        db=SimpleNamespace(commit=lambda: commits.append("commit"), rollback=lambda: None),
        user={"sub": "user-1"},
    )

    assert payload.session.title == "Renamed title"
    assert commits == ["commit"]


@pytest.mark.asyncio
async def test_rename_session_returns_404_for_other_users_session(monkeypatch):
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(session_id="session-foreign", user_auth_sub="user-2", title="Private"),
        ]
    )
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)

    with pytest.raises(HTTPException) as exc:
        await chat.rename_session(
            "session-foreign",
            chat.RenameSessionRequest(title="Renamed"),
            db=SimpleNamespace(commit=lambda: None, rollback=lambda: None),
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_session_returns_404_for_other_users_session(monkeypatch):
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(session_id="session-foreign", user_auth_sub="user-2", title="Private"),
        ]
    )
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)

    with pytest.raises(HTTPException) as exc:
        await chat.delete_session(
            "session-foreign",
            db=SimpleNamespace(commit=lambda: None, rollback=lambda: None),
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_bulk_delete_sessions_only_deletes_visible_sessions(monkeypatch):
    commits: list[str] = []
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(session_id="session-a", user_auth_sub="user-1", title="A"),
            _session_record(session_id="session-b", user_auth_sub="user-1", title="B"),
            _session_record(session_id="session-c", user_auth_sub="user-2", title="Private"),
        ]
    )
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)

    payload = await chat.bulk_delete_sessions(
        chat.BulkDeleteSessionsRequest(
            session_ids=["session-a", "session-c", "session-a", "session-b"]
        ),
        db=SimpleNamespace(commit=lambda: commits.append("commit"), rollback=lambda: None),
        user={"sub": "user-1"},
    )

    assert payload.requested_count == 3
    assert payload.deleted_count == 2
    assert payload.deleted_session_ids == ["session-a", "session-b"]
    assert commits == ["commit"]


def _async_value(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner()
