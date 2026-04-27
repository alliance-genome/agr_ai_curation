"""Unit tests for chat misc/document/history endpoints and non-stream chat path."""

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException

from src.api import chat, chat_common, chat_documents, chat_sessions, chat_stream
from src.lib.chat_history_repository import (
    ASSISTANT_CHAT_KIND,
    AppendMessageResult,
    ChatMessageCursor,
    ChatMessagePage,
    ChatSessionRecord,
    ChatSessionPage,
    ChatSessionCursor,
    ChatSessionDetail,
    ChatMessageRecord,
)
from src.lib.curation_workspace import extraction_results as extraction_results_module


_CHAT_IMPLEMENTATION_MODULES = (chat_common, chat_documents, chat_sessions, chat_stream)


def _patch_chat_impl(monkeypatch, name: str, value) -> None:
    patched = False
    for module in _CHAT_IMPLEMENTATION_MODULES:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            patched = True
    if not patched:
        raise AttributeError(name)


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 4, 19, hour, minute, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _stub_non_stream_turn_claims(monkeypatch):
    chat._LOCAL_NON_STREAM_TURN_OWNERS.clear()

    async def _register_active_stream(
        _session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ) -> bool:
        return True

    async def _unregister_active_stream(
        _session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ) -> None:
        return None

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    yield
    chat._LOCAL_NON_STREAM_TURN_OWNERS.clear()


def _session_record(
    *,
    session_id: str,
    user_auth_sub: str = "user-1",
    chat_kind: str = ASSISTANT_CHAT_KIND,
    title: str | None = None,
    generated_title: str | None = None,
    active_document_id: UUID | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    last_message_at: datetime | None = None,
) -> ChatSessionRecord:
    created_value = created_at or _ts(9, 0)
    return ChatSessionRecord(
        session_id=session_id,
        user_auth_sub=user_auth_sub,
        chat_kind=chat_kind,
        title=title,
        generated_title=generated_title,
        active_document_id=active_document_id,
        created_at=created_value,
        updated_at=updated_at or created_value,
        last_message_at=last_message_at,
        deleted_at=None,
    )


def _message_record(
    *,
    session_id: str,
    role: str,
    content: str,
    chat_kind: str = ASSISTANT_CHAT_KIND,
    turn_id: str | None = None,
    message_type: str = "text",
    payload_json=None,
    trace_id: str | None = None,
    created_at: datetime | None = None,
) -> ChatMessageRecord:
    return ChatMessageRecord(
        message_id=uuid4(),
        session_id=session_id,
        chat_kind=chat_kind,
        turn_id=turn_id,
        role=role,
        message_type=message_type,
        content=content,
        payload_json=payload_json,
        trace_id=trace_id,
        created_at=created_at or _ts(9, 1),
    )


class FakeChatHistoryRepository:
    def __init__(
        self,
        *,
        sessions: list[ChatSessionRecord] | None = None,
        detail_messages: dict[tuple[str, str], list[ChatMessageRecord]] | None = None,
        visible_document_ids: set[UUID] | None = None,
    ) -> None:
        self.sessions = {
            (record.user_auth_sub, record.session_id): record
            for record in (sessions or [])
        }
        self.detail_messages = detail_messages or {}
        self.visible_document_ids = visible_document_ids
        self.create_calls: list[dict[str, object]] = []
        self.get_or_create_calls: list[dict[str, object]] = []
        self.append_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self.count_calls: list[dict[str, object]] = []
        self.rename_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.visible_document_calls: list[dict[str, object]] = []
        self._message_counter = 0

    def create_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        title: str | None = None,
        generated_title: str | None = None,
        active_document_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ChatSessionRecord:
        record = _session_record(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=chat_kind,
            title=title,
            generated_title=generated_title,
            active_document_id=active_document_id,
            created_at=created_at or _ts(10, 0),
            updated_at=created_at or _ts(10, 0),
        )
        self.sessions[(user_auth_sub, session_id)] = record
        self.create_calls.append(
            {
                "session_id": session_id,
                "user_auth_sub": user_auth_sub,
                "chat_kind": chat_kind,
                "title": title,
                "generated_title": generated_title,
                "active_document_id": active_document_id,
            }
        )
        return record

    def get_visible_document_id(
        self,
        *,
        document_id: UUID,
        user_auth_sub: str,
    ) -> UUID | None:
        self.visible_document_calls.append(
            {
                "document_id": document_id,
                "user_auth_sub": user_auth_sub,
            }
        )
        if self.visible_document_ids is None:
            return document_id
        return document_id if document_id in self.visible_document_ids else None

    def get_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
    ) -> ChatSessionRecord | None:
        return self.sessions.get((user_auth_sub, session_id))

    def get_or_create_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        title: str | None = None,
        generated_title: str | None = None,
        active_document_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ChatSessionRecord:
        self.get_or_create_calls.append(
            {
                "session_id": session_id,
                "user_auth_sub": user_auth_sub,
                "chat_kind": chat_kind,
                "title": title,
                "generated_title": generated_title,
                "active_document_id": active_document_id,
            }
        )
        existing = self.sessions.get((user_auth_sub, session_id))
        if existing is not None and existing.chat_kind == chat_kind:
            return existing
        return self.create_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=chat_kind,
            title=title,
            generated_title=generated_title,
            active_document_id=active_document_id,
            created_at=created_at,
        )

    def append_message(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        role: str,
        content: str,
        message_type: str = "text",
        turn_id: str | None = None,
        payload_json=None,
        trace_id: str | None = None,
        created_at: datetime | None = None,
    ) -> AppendMessageResult:
        self.append_calls.append(
            {
                "session_id": session_id,
                "user_auth_sub": user_auth_sub,
                "chat_kind": chat_kind,
                "role": role,
                "content": content,
                "message_type": message_type,
                "turn_id": turn_id,
                "payload_json": payload_json,
                "trace_id": trace_id,
            }
        )
        if not session_id.strip():
            raise ValueError("session_id is required")
        if not role.strip():
            raise ValueError("role is required")
        if not content.strip():
            raise ValueError("content is required")
        if message_type is not None and not str(message_type).strip():
            raise ValueError("message_type is required")
        if turn_id is not None and not turn_id.strip():
            raise ValueError("turn_id cannot be blank")

        session = self.sessions.get((user_auth_sub, session_id))
        if session is None or session.chat_kind != chat_kind:
            raise ValueError("session_id is required")

        existing = None
        if turn_id is not None:
            existing = self.get_message_by_turn_id(
                session_id=session_id,
                user_auth_sub=user_auth_sub,
                turn_id=turn_id,
                role=role,
            )
        if existing is not None:
            return AppendMessageResult(message=existing, created=False)

        self._message_counter += 1
        record = ChatMessageRecord(
            message_id=uuid4(),
            session_id=session_id,
            chat_kind=chat_kind,
            turn_id=turn_id,
            role=role,
            message_type=message_type,
            content=content,
            payload_json=payload_json,
            trace_id=trace_id,
            created_at=created_at or _ts(12, self._message_counter),
        )
        message_key = (user_auth_sub, session_id)
        self.detail_messages.setdefault(message_key, []).append(record)
        self.sessions[message_key] = _session_record(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=session.chat_kind,
            title=session.title,
            generated_title=session.generated_title,
            active_document_id=session.active_document_id,
            created_at=session.created_at,
            updated_at=record.created_at,
            last_message_at=record.created_at,
        )
        return AppendMessageResult(message=record, created=True)

    def get_message_by_turn_id(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        turn_id: str,
        role: str,
    ) -> ChatMessageRecord | None:
        if not session_id.strip():
            raise ValueError("session_id is required")
        if not role.strip():
            raise ValueError("role is required")
        normalized_turn_id = turn_id.strip()
        if not normalized_turn_id:
            raise ValueError("turn_id is required")
        if (user_auth_sub, session_id) not in self.sessions:
            raise ValueError(f"session {session_id} not found")

        for message in self.detail_messages.get((user_auth_sub, session_id), []):
            if message.turn_id == normalized_turn_id and message.role == role:
                return message
        return None

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
        message_page = self.list_messages(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=session.chat_kind,
            limit=message_limit,
            cursor=message_cursor,
        )
        return ChatSessionDetail(
            session=session,
            messages=message_page.items,
            next_message_cursor=message_page.next_cursor,
        )

    def list_messages(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        limit: int = 100,
        cursor: ChatMessageCursor | None = None,
    ) -> ChatMessagePage:
        if not session_id.strip():
            raise ValueError("session_id is required")
        session = self.sessions.get((user_auth_sub, session_id))
        if session is None or session.chat_kind != chat_kind:
            return ChatMessagePage(items=[], next_cursor=None)

        messages = sorted(
            [
                message
                for message in self.detail_messages.get((user_auth_sub, session_id), [])
                if message.chat_kind == chat_kind
            ],
            key=lambda message: (message.created_at, message.message_id),
        )
        if cursor is not None:
            messages = [
                message
                for message in messages
                if (message.created_at, message.message_id) > (cursor.created_at, cursor.message_id)
            ]

        has_more = len(messages) > limit
        items = messages[:limit]
        next_cursor = None
        if has_more and items:
            last_message = items[-1]
            next_cursor = ChatMessageCursor(
                created_at=last_message.created_at,
                message_id=last_message.message_id,
            )

        return ChatMessagePage(items=items, next_cursor=next_cursor)

    def list_sessions(
        self,
        *,
        user_auth_sub: str,
        chat_kind: str,
        limit: int = 20,
        cursor: ChatSessionCursor | None = None,
        active_document_id: UUID | None = None,
    ) -> ChatSessionPage:
        self.list_calls.append(
            {
                "user_auth_sub": user_auth_sub,
                "chat_kind": chat_kind,
                "limit": limit,
                "cursor": cursor,
                "active_document_id": active_document_id,
            }
        )
        items = self._visible_sessions(
            user_auth_sub=user_auth_sub,
            chat_kind=chat_kind,
            active_document_id=active_document_id,
        )
        return ChatSessionPage(items=items[:limit], next_cursor=None)

    def search_sessions(
        self,
        *,
        user_auth_sub: str,
        chat_kind: str,
        query: str,
        limit: int = 20,
        cursor: ChatSessionCursor | None = None,
        active_document_id: UUID | None = None,
    ) -> ChatSessionPage:
        self.search_calls.append(
            {
                "user_auth_sub": user_auth_sub,
                "chat_kind": chat_kind,
                "query": query,
                "limit": limit,
                "cursor": cursor,
                "active_document_id": active_document_id,
            }
        )
        items = self._visible_sessions(
            user_auth_sub=user_auth_sub,
            chat_kind=chat_kind,
            active_document_id=active_document_id,
            query=query,
        )
        return ChatSessionPage(items=items[:limit], next_cursor=None)

    def count_sessions(
        self,
        *,
        user_auth_sub: str,
        chat_kind: str,
        query: str | None = None,
        active_document_id: UUID | None = None,
    ) -> int:
        self.count_calls.append(
            {
                "user_auth_sub": user_auth_sub,
                "chat_kind": chat_kind,
                "query": query,
                "active_document_id": active_document_id,
            }
        )
        return len(
            self._visible_sessions(
                user_auth_sub=user_auth_sub,
                chat_kind=chat_kind,
                active_document_id=active_document_id,
                query=query,
            )
        )

    def rename_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        title: str,
    ) -> ChatSessionRecord | None:
        self.rename_calls.append(
            {
                "session_id": session_id,
                "user_auth_sub": user_auth_sub,
                "chat_kind": chat_kind,
                "title": title,
            }
        )
        if not session_id.strip():
            raise ValueError("session_id is required")
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("title is required")
        session = self.sessions.get((user_auth_sub, session_id))
        if session is None or session.chat_kind != chat_kind:
            return None
        updated = _session_record(
            session_id=session.session_id,
            user_auth_sub=session.user_auth_sub,
            chat_kind=session.chat_kind,
            title=normalized_title,
            generated_title=session.generated_title,
            active_document_id=session.active_document_id,
            created_at=session.created_at,
            updated_at=_ts(11, 15),
            last_message_at=session.last_message_at,
        )
        self.sessions[(user_auth_sub, session_id)] = updated
        return updated

    def set_generated_title(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        generated_title: str,
    ) -> ChatSessionRecord | None:
        session = self.sessions.get((user_auth_sub, session_id))
        if session is None or session.chat_kind != chat_kind:
            return None
        if session.title is not None or session.generated_title is not None:
            return session

        updated = _session_record(
            session_id=session.session_id,
            user_auth_sub=session.user_auth_sub,
            chat_kind=session.chat_kind,
            title=session.title,
            generated_title=generated_title,
            active_document_id=session.active_document_id,
            created_at=session.created_at,
            updated_at=_ts(11, 20),
            last_message_at=session.last_message_at,
        )
        self.sessions[(user_auth_sub, session_id)] = updated
        return updated

    def soft_delete_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        deleted_at: datetime | None = None,
    ) -> bool:
        self.delete_calls.append(
            {
                "session_id": session_id,
                "user_auth_sub": user_auth_sub,
                "chat_kind": chat_kind,
                "deleted_at": deleted_at,
            }
        )
        if not session_id.strip():
            raise ValueError("session_id is required")
        session = self.sessions.get((user_auth_sub, session_id))
        if session is None or session.chat_kind != chat_kind:
            return False
        self.sessions.pop((user_auth_sub, session_id), None)
        return True

    def _visible_sessions(
        self,
        *,
        user_auth_sub: str,
        chat_kind: str,
        active_document_id: UUID | None = None,
        query: str | None = None,
    ) -> list[ChatSessionRecord]:
        sessions = [
            record
            for (owner, _session_id), record in self.sessions.items()
            if owner == user_auth_sub and (
                chat_kind == "all" or record.chat_kind == chat_kind
            )
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
                if normalized_query in (record.effective_title or "").lower()
            ]
        return sorted(
            sessions,
            key=lambda record: (record.recent_activity_at, record.session_id),
            reverse=True,
        )


def _db_stub(*, commits: list[str] | None = None, rollbacks: list[str] | None = None):
    return SimpleNamespace(
        commit=lambda: commits.append("commit") if commits is not None else None,
        rollback=lambda: rollbacks.append("rollback") if rollbacks is not None else None,
    )


def test_build_context_messages_from_history_appends_current_user_turn():
    context_messages = chat._build_context_messages_from_history(
        [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ],
        user_message="follow-up question",
    )

    assert context_messages == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "follow-up question"},
    ]


def test_build_context_messages_from_history_raises_on_malformed_history_message():
    with pytest.raises(ValueError, match="history_messages\\[0\\] is missing a role"):
        chat._build_context_messages_from_history(
            [{"role": "", "content": "first question"}],
            user_message="follow-up question",
        )


def test_build_context_messages_from_durable_messages_preserves_completed_exchange_semantics():
    repository = FakeChatHistoryRepository(
        sessions=[_session_record(session_id="session-context")],
        detail_messages={
            ("user-1", "session-context"): [
                _message_record(
                    session_id="session-context",
                    role="user",
                    content="first question",
                    turn_id="turn-1",
                    created_at=_ts(9, 1),
                ),
                _message_record(
                    session_id="session-context",
                    role="assistant",
                    content="first answer",
                    turn_id="turn-1",
                    created_at=_ts(9, 2),
                ),
                _message_record(
                    session_id="session-context",
                    role="user",
                    content="stale interrupted question",
                    message_type="text",
                    created_at=_ts(9, 3),
                ),
                _message_record(
                    session_id="session-context",
                    role="flow",
                    content="flow memory",
                    message_type="text",
                    created_at=_ts(9, 4),
                ),
            ]
        },
    )

    context_messages = chat._build_context_messages_from_durable_messages(
        repository,
        user_id="user-1",
        session_id="session-context",
        user_message="current question",
    )

    assert context_messages == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "current question"},
    ]


def test_build_context_messages_from_durable_messages_rehydrates_flow_memory_from_summary_rows():
    repository = FakeChatHistoryRepository(
        sessions=[_session_record(session_id="session-flow-context")],
        detail_messages={
            ("user-1", "session-flow-context"): [
                _message_record(
                    session_id="session-flow-context",
                    role="user",
                    content="Run gene selection flow",
                    turn_id="turn-flow-1",
                    created_at=_ts(9, 1),
                ),
                _message_record(
                    session_id="session-flow-context",
                    role="flow",
                    content="Selected TP53 for highest evidence confidence.",
                    turn_id="turn-flow-1",
                    message_type=chat.FLOW_SUMMARY_MESSAGE_TYPE,
                    payload_json={
                        "flow_id": "flow-1",
                        "status": "completed",
                        chat.FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY: "hidden assistant flow memory",
                    },
                    created_at=_ts(9, 2),
                ),
            ]
        },
    )

    context_messages = chat._build_context_messages_from_durable_messages(
        repository,
        user_id="user-1",
        session_id="session-flow-context",
        user_message="follow-up question",
    )

    assert context_messages == [
        {"role": "user", "content": "Run gene selection flow"},
        {"role": "assistant", "content": "hidden assistant flow memory"},
        {"role": "user", "content": "follow-up question"},
    ]



@pytest.mark.asyncio
async def test_load_document_for_chat_success(monkeypatch):
    captured = {}
    doc_payload = {"id": "doc-1", "filename": "paper.pdf", "chunk_count": 10}

    async def _get_document(_user_sub, _doc_id):
        return {"document": doc_payload}

    _patch_chat_impl(monkeypatch, "get_document", _get_document)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(set_document=lambda user, doc: captured.setdefault(user, doc)))
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda user, doc_id: captured.setdefault("cache", (user, doc_id)))

    result = await chat.load_document_for_chat(chat.LoadDocumentRequest(document_id="doc-1"), {"sub": "user-1"})
    assert result.active is True
    assert result.document.id == "doc-1"
    assert captured["user-1"]["filename"] == "paper.pdf"
    assert captured["cache"] == ("user-1", "doc-1")


@pytest.mark.asyncio
async def test_load_document_for_chat_404_on_value_error(monkeypatch, caplog):
    async def _raise(*_args, **_kwargs):
        raise ValueError("missing")

    _patch_chat_impl(monkeypatch, "get_document", _raise)
    caplog.set_level(logging.WARNING, logger=chat.logger.name)

    with pytest.raises(HTTPException) as exc:
        await chat.load_document_for_chat(chat.LoadDocumentRequest(document_id="doc-404"), {"sub": "user-1"})
    assert exc.value.status_code == 404
    assert exc.value.detail == "Document not found"
    assert "missing" in caplog.text


@pytest.mark.asyncio
async def test_load_document_for_chat_500_when_summary_missing(monkeypatch):
    _patch_chat_impl(monkeypatch, "get_document", lambda *_args, **_kwargs: _async_value({"not_document": {}}))

    with pytest.raises(HTTPException) as exc:
        await chat.load_document_for_chat(chat.LoadDocumentRequest(document_id="doc-1"), {"sub": "user-1"})
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_get_loaded_document_and_clear_document(monkeypatch):
    stored = {"id": "doc-1", "filename": "paper.pdf"}

    _patch_chat_impl(
        monkeypatch,
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
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    payload = await chat.clear_loaded_document({"sub": "user-1"})
    assert payload.active is False
    assert "No document was loaded" in payload.message


@pytest.mark.asyncio
async def test_create_session_returns_uuid_and_persists_active_document(monkeypatch):
    repository = FakeChatHistoryRepository()
    commits: list[str] = []
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(
        monkeypatch,
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
        chat.CreateSessionRequest(chat_kind="assistant_chat"),
        SimpleNamespace(commit=lambda: commits.append("commit"), rollback=lambda: None),
        {"sub": "user-1"},
    )

    UUID(payload.session_id)
    assert payload.active_document_id == "8b7be2ce-2f34-4c30-8f47-26a8cb5cd1a8"
    assert payload.active_document.filename == "paper.pdf"
    assert commits == ["commit"]
    assert repository.create_calls[0]["user_auth_sub"] == "user-1"
    assert str(repository.create_calls[0]["active_document_id"]) == payload.active_document_id
    assert repository.visible_document_calls == [
        {
            "document_id": UUID("8b7be2ce-2f34-4c30-8f47-26a8cb5cd1a8"),
            "user_auth_sub": "user-1",
        }
    ]


@pytest.mark.asyncio
async def test_create_session_drops_unavailable_active_document(monkeypatch):
    stale_document_id = UUID("8b7be2ce-2f34-4c30-8f47-26a8cb5cd1a8")
    repository = FakeChatHistoryRepository(visible_document_ids=set())
    commits: list[str] = []
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(
        monkeypatch,
        "document_state",
        SimpleNamespace(
            get_document=lambda _uid: {
                "id": str(stale_document_id),
                "filename": "deleted-paper.pdf",
                "chunk_count": 11,
            }
        ),
    )

    payload = await chat.create_session(
        chat.CreateSessionRequest(chat_kind="assistant_chat"),
        SimpleNamespace(commit=lambda: commits.append("commit"), rollback=lambda: None),
        {"sub": "user-1"},
    )

    UUID(payload.session_id)
    assert payload.active_document_id is None
    assert payload.active_document is None
    assert commits == ["commit"]
    assert repository.create_calls[0]["user_auth_sub"] == "user-1"
    assert repository.create_calls[0]["active_document_id"] is None
    assert repository.visible_document_calls == [
        {
            "document_id": stale_document_id,
            "user_auth_sub": "user-1",
        }
    ]


@pytest.mark.asyncio
async def test_create_session_drops_invalid_active_document_uuid(monkeypatch):
    repository = FakeChatHistoryRepository()
    commits: list[str] = []
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(
        monkeypatch,
        "document_state",
        SimpleNamespace(
            get_document=lambda _uid: {
                "id": "not-a-uuid",
                "filename": "stale-paper.pdf",
                "chunk_count": 11,
            }
        ),
    )

    payload = await chat.create_session(
        chat.CreateSessionRequest(chat_kind="assistant_chat"),
        SimpleNamespace(commit=lambda: commits.append("commit"), rollback=lambda: None),
        {"sub": "user-1"},
    )

    UUID(payload.session_id)
    assert payload.active_document_id is None
    assert payload.active_document is None
    assert commits == ["commit"]
    assert repository.create_calls[0]["user_auth_sub"] == "user-1"
    assert repository.create_calls[0]["active_document_id"] is None
    assert repository.visible_document_calls == []


def test_fake_chat_history_repository_turn_lookup_requires_existing_session():
    repository = FakeChatHistoryRepository()

    with pytest.raises(ValueError, match="session missing-session not found"):
        repository.get_message_by_turn_id(
            session_id="missing-session",
            user_auth_sub="user-1",
            turn_id="turn-1",
            role="assistant",
        )


@pytest.mark.asyncio
async def test_chat_endpoint_success(monkeypatch):
    commits: list[str] = []
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

    async def _stream(**_kwargs):
        assert [(call["role"], call["content"]) for call in repository.append_calls] == [
            ("user", "hello")
        ]
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "final answer"}}

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)

    result = await chat.chat_endpoint(
        chat.ChatMessage(message="hello", session_id="session-1", turn_id="turn-1"),
        {"sub": "user-1", "cognito:groups": []},
        db=_db_stub(commits=commits),
    )
    assert result.response == "final answer"
    assert result.session_id == "session-1"
    assert commits == ["commit", "commit"]
    assert repository.get_or_create_calls == [
        {
            "session_id": "session-1",
            "user_auth_sub": "user-1",
            "chat_kind": ASSISTANT_CHAT_KIND,
            "title": None,
            "generated_title": None,
            "active_document_id": None,
        }
    ]
    assert [call["role"] for call in repository.append_calls] == ["user", "assistant"]
    assert repository.append_calls[0]["turn_id"] == "turn-1"
    assert repository.append_calls[1]["turn_id"] == "turn-1"
    assert repository.append_calls[1]["trace_id"] == "trace-1"


@pytest.mark.asyncio
async def test_chat_endpoint_uses_last_run_finished_response(monkeypatch):
    commits: list[str] = []
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

    async def _stream(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "intermediate answer"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "final stabilized answer"}}

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)

    result = await chat.chat_endpoint(
        chat.ChatMessage(message="hello", session_id="session-2", turn_id="turn-2"),
        {"sub": "user-1", "cognito:groups": []},
        db=_db_stub(commits=commits),
    )

    assert result.response == "final stabilized answer"
    assert repository.append_calls[-1]["content"] == "final stabilized answer"
    assert commits == ["commit", "commit"]


@pytest.mark.asyncio
async def test_chat_endpoint_retries_failed_turn_once_prior_claim_is_released(monkeypatch, caplog):
    commits: list[str] = []
    register_calls = []
    unregister_calls = []
    streamed_context_messages = []
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

    async def _register_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ) -> bool:
        register_calls.append((session_id, user_id, stream_token))
        return True

    async def _unregister_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ) -> None:
        unregister_calls.append((session_id, user_id, stream_token))

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)
    caplog.set_level(logging.ERROR, logger=chat.logger.name)

    run_attempt = 0

    async def _stream(**kwargs):
        nonlocal run_attempt
        run_attempt += 1
        streamed_context_messages.append(kwargs["context_messages"])
        if run_attempt == 1:
            yield {"type": "RUN_ERROR", "data": {"message": "model exploded"}}
            return
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-retry"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "recovered answer"}}

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(
            chat.ChatMessage(message="hello", session_id="session-retry", turn_id="turn-retry"),
            {"sub": "user-1", "cognito:groups": []},
            db=_db_stub(commits=commits),
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to process chat request"
    assert "model exploded" not in str(exc.value.detail)
    assert "model exploded" in caplog.text
    assert commits == ["commit"]
    assert [call["role"] for call in repository.append_calls] == ["user"]
    assert "non-stream-turn:session-retry:turn-retry" not in chat._LOCAL_NON_STREAM_TURN_OWNERS

    result = await chat.chat_endpoint(
        chat.ChatMessage(
            message="different retry body should be ignored",
            session_id="session-retry",
            turn_id="turn-retry",
        ),
        {"sub": "user-1", "cognito:groups": []},
        db=_db_stub(commits=commits),
    )

    assert result.response == "recovered answer"
    assert result.session_id == "session-retry"
    assert commits == ["commit", "commit", "commit"]
    assert streamed_context_messages == [
        [{"role": "user", "content": "hello"}],
        [{"role": "user", "content": "hello"}],
    ]
    assert [call["role"] for call in repository.append_calls] == ["user", "user", "assistant"]
    assert register_calls[0][0] == "non-stream-turn:session-retry:turn-retry"
    assert register_calls[1][0] == "non-stream-turn:session-retry:turn-retry"
    assert unregister_calls[0][0] == "non-stream-turn:session-retry:turn-retry"
    assert unregister_calls[1][0] == "non-stream-turn:session-retry:turn-retry"
    assert "non-stream-turn:session-retry:turn-retry" not in chat._LOCAL_NON_STREAM_TURN_OWNERS


@pytest.mark.asyncio
async def test_chat_endpoint_retries_after_tool_map_failure_releases_same_turn_claim(monkeypatch):
    commits: list[str] = []
    register_calls = []
    unregister_calls = []
    streamed_context_messages = []
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

    async def _register_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ) -> bool:
        register_calls.append((session_id, user_id, stream_token))
        return True

    async def _unregister_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ) -> None:
        unregister_calls.append((session_id, user_id, stream_token))

    _patch_chat_impl(monkeypatch, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, "unregister_active_stream", _unregister_active_stream)

    def _raise_tool_map():
        raise RuntimeError("agent registry unavailable")

    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", _raise_tool_map)

    async def _stream(**kwargs):
        streamed_context_messages.append(kwargs["context_messages"])
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-tool-map"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "recovered answer"}}

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(
            chat.ChatMessage(message="hello", session_id="session-tool-map", turn_id="turn-tool-map"),
            {"sub": "user-1", "cognito:groups": []},
            db=_db_stub(commits=commits),
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == "Internal configuration error: unable to process chat request"
    assert commits == ["commit"]
    assert [call["role"] for call in repository.append_calls] == ["user"]
    assert "non-stream-turn:session-tool-map:turn-tool-map" not in chat._LOCAL_NON_STREAM_TURN_OWNERS

    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})

    result = await chat.chat_endpoint(
        chat.ChatMessage(
            message="different retry body should be ignored",
            session_id="session-tool-map",
            turn_id="turn-tool-map",
        ),
        {"sub": "user-1", "cognito:groups": []},
        db=_db_stub(commits=commits),
    )

    assert result.response == "recovered answer"
    assert result.session_id == "session-tool-map"
    assert commits == ["commit", "commit", "commit"]
    assert streamed_context_messages == [[{"role": "user", "content": "hello"}]]
    assert [call["role"] for call in repository.append_calls] == ["user", "user", "assistant"]
    assert register_calls[0][0] == "non-stream-turn:session-tool-map:turn-tool-map"
    assert register_calls[1][0] == "non-stream-turn:session-tool-map:turn-tool-map"
    assert unregister_calls[0][0] == "non-stream-turn:session-tool-map:turn-tool-map"
    assert unregister_calls[1][0] == "non-stream-turn:session-tool-map:turn-tool-map"
    assert "non-stream-turn:session-tool-map:turn-tool-map" not in chat._LOCAL_NON_STREAM_TURN_OWNERS


@pytest.mark.asyncio
async def test_chat_endpoint_omits_unfinished_prior_user_turn_from_context_messages(monkeypatch):
    commits: list[str] = []
    captured_context_messages = []
    repository = FakeChatHistoryRepository(
        sessions=[_session_record(session_id="session-context")],
        detail_messages={
            ("user-1", "session-context"): [
                _message_record(
                    session_id="session-context",
                    role="user",
                    content="first question",
                    turn_id="turn-1",
                    created_at=_ts(9, 1),
                ),
                _message_record(
                    session_id="session-context",
                    role="assistant",
                    content="first answer",
                    turn_id="turn-1",
                    created_at=_ts(9, 2),
                ),
                _message_record(
                    session_id="session-context",
                    role="user",
                    content="stale interrupted question",
                    turn_id="turn-stale",
                    created_at=_ts(9, 3),
                ),
            ]
        },
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})

    async def _stream(**kwargs):
        captured_context_messages.append(kwargs["context_messages"])
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-current"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "fresh answer"}}

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)

    result = await chat.chat_endpoint(
        chat.ChatMessage(message="current question", session_id="session-context", turn_id="turn-current"),
        {"sub": "user-1", "cognito:groups": []},
        db=_db_stub(commits=commits),
    )

    assert result.response == "fresh answer"
    assert captured_context_messages == [
        [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "current question"},
        ]
    ]
    assert [call["role"] for call in repository.append_calls] == ["user", "assistant"]
    assert commits == ["commit", "commit"]


@pytest.mark.asyncio
async def test_chat_endpoint_rejects_same_turn_while_claim_is_still_active(monkeypatch):
    claim_key = "non-stream-turn:session-active:turn-active"
    chat._LOCAL_NON_STREAM_TURN_OWNERS[claim_key] = "existing-claim"

    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(
            chat.ChatMessage(message="hello", session_id="session-active", turn_id="turn-active"),
            {"sub": "user-1", "cognito:groups": []},
            db=_db_stub(),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Chat turn is already in progress"
    assert repository.append_calls == []


@pytest.mark.asyncio
async def test_chat_endpoint_replays_completed_turn_without_rerunning(monkeypatch):
    commits: list[str] = []
    repository = FakeChatHistoryRepository(
        sessions=[_session_record(session_id="session-replay")],
        detail_messages={
            ("user-1", "session-replay"): [
                _message_record(
                    session_id="session-replay",
                    role="user",
                    content="hello",
                    turn_id="turn-replay",
                    created_at=_ts(9, 1),
                ),
                _message_record(
                    session_id="session-replay",
                    role="assistant",
                    content="stored answer",
                    turn_id="turn-replay",
                    trace_id="trace-stored",
                    created_at=_ts(9, 2),
                ),
            ]
        },
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(
        monkeypatch,
        "get_supervisor_tool_agent_map",
        lambda: pytest.fail("tool map should not resolve for a completed replayed turn"),
    )

    async def _stream(**_kwargs):
        pytest.fail("run_agent_streamed should not run for a completed replayed turn")
        yield  # pragma: no cover

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)

    result = await chat.chat_endpoint(
        chat.ChatMessage(message="hello", session_id="session-replay", turn_id="turn-replay"),
        {"sub": "user-1", "cognito:groups": []},
        db=_db_stub(commits=commits),
    )

    assert result.response == "stored answer"
    assert result.session_id == "session-replay"
    assert commits == ["commit"]
    assert [call["role"] for call in repository.append_calls] == ["user"]
    assert "non-stream-turn:session-replay:turn-replay" not in chat._LOCAL_NON_STREAM_TURN_OWNERS


@pytest.mark.asyncio
async def test_chat_endpoint_replay_reseeds_prompt_history_for_the_next_turn(monkeypatch):
    commits: list[str] = []
    captured_context_messages = []
    repository = FakeChatHistoryRepository(
        sessions=[_session_record(session_id="session-replay")],
        detail_messages={
            ("user-1", "session-replay"): [
                _message_record(
                    session_id="session-replay",
                    role="user",
                    content="first question",
                    turn_id="turn-1",
                    created_at=_ts(9, 1),
                ),
                _message_record(
                    session_id="session-replay",
                    role="assistant",
                    content="first answer",
                    turn_id="turn-1",
                    created_at=_ts(9, 2),
                ),
                _message_record(
                    session_id="session-replay",
                    role="user",
                    content="replayed question",
                    turn_id="turn-replay",
                    created_at=_ts(9, 3),
                ),
                _message_record(
                    session_id="session-replay",
                    role="assistant",
                    content="stored answer",
                    turn_id="turn-replay",
                    trace_id="trace-stored",
                    created_at=_ts(9, 4),
                ),
            ]
        },
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(
        monkeypatch,
        "get_supervisor_tool_agent_map",
        lambda: pytest.fail("tool map should not resolve for a completed replayed turn"),
    )

    async def _unexpected_stream(**_kwargs):
        pytest.fail("run_agent_streamed should not run for a completed replayed turn")
        yield  # pragma: no cover

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _unexpected_stream)

    replay_result = await chat.chat_endpoint(
        chat.ChatMessage(message="replayed question", session_id="session-replay", turn_id="turn-replay"),
        {"sub": "user-1", "cognito:groups": []},
        db=_db_stub(commits=commits),
    )

    assert replay_result.response == "stored answer"
    assert chat._build_context_messages_from_durable_messages(
        repository,
        user_id="user-1",
        session_id="session-replay",
        user_message="",
    ) == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "replayed question"},
        {"role": "assistant", "content": "stored answer"},
        {"role": "user", "content": ""},
    ]

    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})

    async def _stream(**kwargs):
        captured_context_messages.append(kwargs["context_messages"])
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-next"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "next answer"}}

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)

    result = await chat.chat_endpoint(
        chat.ChatMessage(message="follow-up question", session_id="session-replay", turn_id="turn-next"),
        {"sub": "user-1", "cognito:groups": []},
        db=_db_stub(commits=commits),
    )

    assert result.response == "next answer"
    assert captured_context_messages == [
        [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "replayed question"},
            {"role": "assistant", "content": "stored answer"},
            {"role": "user", "content": "follow-up question"},
        ]
    ]
    assert commits == ["commit", "commit", "commit"]


@pytest.mark.asyncio
async def test_chat_endpoint_follow_up_turn_rehydrates_replayed_exchange_on_stale_worker(monkeypatch):
    commits: list[str] = []
    captured_context_messages = []
    repository = FakeChatHistoryRepository(
        sessions=[_session_record(session_id="session-replay")],
        detail_messages={
            ("user-1", "session-replay"): [
                _message_record(
                    session_id="session-replay",
                    role="user",
                    content="first question",
                    turn_id="turn-1",
                    created_at=_ts(9, 1),
                ),
                _message_record(
                    session_id="session-replay",
                    role="assistant",
                    content="first answer",
                    turn_id="turn-1",
                    created_at=_ts(9, 2),
                ),
                _message_record(
                    session_id="session-replay",
                    role="user",
                    content="replayed question",
                    turn_id="turn-replay",
                    created_at=_ts(9, 3),
                ),
                _message_record(
                    session_id="session-replay",
                    role="assistant",
                    content="stored answer",
                    turn_id="turn-replay",
                    trace_id="trace-stored",
                    created_at=_ts(9, 4),
                ),
            ]
        },
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})

    async def _stream(**kwargs):
        captured_context_messages.append(kwargs["context_messages"])
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-next"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "next answer"}}

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)

    result = await chat.chat_endpoint(
        chat.ChatMessage(message="follow-up question", session_id="session-replay", turn_id="turn-next"),
        {"sub": "user-1", "cognito:groups": []},
        db=_db_stub(commits=commits),
    )

    assert result.response == "next answer"
    assert captured_context_messages == [
        [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "replayed question"},
            {"role": "assistant", "content": "stored answer"},
            {"role": "user", "content": "follow-up question"},
        ]
    ]
    assert chat._build_context_messages_from_durable_messages(
        repository,
        user_id="user-1",
        session_id="session-replay",
        user_message="",
    ) == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "replayed question"},
        {"role": "assistant", "content": "stored answer"},
        {"role": "user", "content": "follow-up question"},
        {"role": "assistant", "content": "next answer"},
        {"role": "user", "content": ""},
    ]
    assert commits == ["commit", "commit"]


@pytest.mark.asyncio
async def test_chat_endpoint_passes_model_overrides_to_runner(monkeypatch):
    captured = {}
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

    async def _stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "final answer from overrides"}}

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)

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
        db=_db_stub(),
    )

    assert captured["supervisor_model"] == "gpt-5.4-nano"
    assert captured["specialist_model"] == "gpt-5.4-nano"
    assert captured["supervisor_temperature"] == 0.0
    assert captured["specialist_temperature"] == 0.0
    assert captured["supervisor_reasoning"] == "minimal"
    assert captured["specialist_reasoning"] == "minimal"
    assert captured["context_messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_chat_endpoint_leaves_model_overrides_unset_when_omitted(monkeypatch):
    captured = {}
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

    async def _stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-defaults"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "final answer from config defaults"}}

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)

    await chat.chat_endpoint(
        chat.ChatMessage(message="hello", session_id="session-defaults"),
        {"sub": "user-1", "cognito:groups": []},
        db=_db_stub(),
    )

    assert captured["supervisor_model"] is None
    assert captured["specialist_model"] is None
    assert captured["supervisor_temperature"] is None
    assert captured["specialist_temperature"] is None
    assert captured["supervisor_reasoning"] is None
    assert captured["specialist_reasoning"] is None
    assert captured["context_messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_chat_endpoint_raises_http_401_without_user_id():
    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(chat.ChatMessage(message="hello"), {"cognito:groups": []})
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_chat_endpoint_raises_500_on_run_error_event(monkeypatch, caplog):
    commits: list[str] = []
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

    async def _stream(**_kwargs):
        yield {"type": "RUN_ERROR", "data": {"message": "model exploded"}}

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)
    caplog.set_level(logging.ERROR, logger=chat.logger.name)

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(
            chat.ChatMessage(message="hello", session_id="session-1", turn_id="turn-error"),
            {"sub": "user-1", "cognito:groups": []},
            db=_db_stub(commits=commits),
        )
    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to process chat request"
    assert "model exploded" not in str(exc.value.detail)
    assert "model exploded" in caplog.text
    assert commits == ["commit"]
    assert [call["role"] for call in repository.append_calls] == ["user"]


@pytest.mark.asyncio
async def test_chat_endpoint_raises_500_when_extraction_persistence_fails(monkeypatch):
    commits: list[str] = []
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
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

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream)
    _patch_chat_impl(
        monkeypatch,
        "persist_extraction_results",
        lambda _requests, db=None: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(
            chat.ChatMessage(message="hello", session_id="session-1", turn_id="turn-1"),
            {"sub": "user-1", "cognito:groups": []},
            db=_db_stub(commits=commits),
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to persist chat response"
    assert commits == ["commit"]
    assert [call["role"] for call in repository.append_calls] == ["user"]


@pytest.mark.asyncio
async def test_chat_endpoint_raises_500_when_tool_map_resolution_fails(monkeypatch):
    """Regression: ALL-137 — tool-map resolution failure must fail closed, not silently disable extraction."""
    commits: list[str] = []
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(
        monkeypatch,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: {"id": "doc-1", "filename": "paper.pdf"}),
    )
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(
        monkeypatch,
        "get_supervisor_tool_agent_map",
        lambda: (_ for _ in ()).throw(RuntimeError("agent registry unavailable")),
    )

    # run_agent_streamed should never be reached; provide a sentinel to verify.
    stream_called = False

    async def _stream_sentinel(**_kwargs):
        nonlocal stream_called
        stream_called = True
        yield {"type": "RUN_FINISHED", "data": {"response": "should not reach"}}

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _stream_sentinel)

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(
            chat.ChatMessage(message="hello", session_id="session-1"),
            {"sub": "user-1", "cognito:groups": []},
            db=_db_stub(commits=commits),
        )

    assert exc.value.status_code == 500
    assert "Internal configuration error" in exc.value.detail
    assert not stream_called, "Agent stream should not run when tool-map resolution fails"
    assert commits == ["commit"]
    assert [call["role"] for call in repository.append_calls] == ["user"]


@pytest.mark.asyncio
async def test_chat_endpoint_sanitizes_non_stream_validation_error(monkeypatch, caplog):
    commits: list[str] = []
    rollbacks: list[str] = []
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])

    def _raise_value_error(**_kwargs):
        raise ValueError("repository session invariant exploded")

    repository.append_message = _raise_value_error
    caplog.set_level(logging.WARNING, logger=chat.logger.name)

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(
            chat.ChatMessage(message="hello", session_id="session-1", turn_id="turn-validation"),
            {"sub": "user-1", "cognito:groups": []},
            db=_db_stub(commits=commits, rollbacks=rollbacks),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid chat request"
    assert commits == []
    assert rollbacks == ["rollback"]
    assert "repository session invariant exploded" in caplog.text


@pytest.mark.asyncio
async def test_chat_endpoint_wraps_unexpected_exceptions(monkeypatch, caplog):
    commits: list[str] = []
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "set_current_session_id", lambda _sid: None)
    _patch_chat_impl(monkeypatch, "set_current_user_id", lambda _uid: None)
    _patch_chat_impl(monkeypatch, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    _patch_chat_impl(monkeypatch, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(monkeypatch, "get_supervisor_tool_agent_map", lambda: {})
    _patch_chat_impl(monkeypatch, "_build_context_messages_from_durable_messages", lambda *_args, **_kwargs: ([{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else []))

    async def _raise(**_kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    _patch_chat_impl(monkeypatch, "run_agent_streamed", _raise)
    caplog.set_level(logging.ERROR, logger=chat.logger.name)

    with pytest.raises(HTTPException) as exc:
        await chat.chat_endpoint(
            chat.ChatMessage(message="hello", session_id="session-1"),
            {"sub": "user-1", "cognito:groups": []},
            db=_db_stub(commits=commits),
        )
    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to process chat request"
    assert commits == ["commit"]
    assert [call["role"] for call in repository.append_calls] == ["user"]
    assert "boom" in caplog.text


@pytest.mark.asyncio
async def test_chat_status_reflects_openai_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    payload = await chat.chat_status({"sub": "user-1"})
    assert payload["service"] == "chat"
    assert payload["openai_key_configured"] is True


@pytest.mark.asyncio
async def test_get_conversation_status_and_reset_endpoints(monkeypatch):
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(session_id="session-current"),
        ],
        detail_messages={
            ("user-1", "session-current"): [
                _message_record(
                    session_id="session-current",
                    role="user",
                    content="first question",
                    turn_id="turn-1",
                    created_at=_ts(9, 1),
                ),
                _message_record(
                    session_id="session-current",
                    role="assistant",
                    content="first answer",
                    turn_id="turn-1",
                    created_at=_ts(9, 2),
                ),
            ],
        },
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "_resolve_session_create_active_document", lambda **_kwargs: (None, None))
    status = await chat.get_conversation_status(db=object(), user={"sub": "user-1"})
    assert status.is_active is True
    assert status.conversation_id == "session-current"
    assert status.memory_stats["memory_sizes"]["short_term"]["file_count"] == 1

    reset = await chat.reset_conversation(_db_stub(commits=[]), {"sub": "user-1"})
    assert reset.success is True
    assert reset.session_id is not None
    assert reset.memory_stats["conversation_id"] == reset.session_id
    assert reset.memory_stats["memory_sizes"]["short_term"]["file_count"] == 0


@pytest.mark.asyncio
async def test_conversation_endpoints_sanitize_internal_errors(monkeypatch, caplog):
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(
        monkeypatch,
        "_latest_visible_chat_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("conversation backend unavailable")),
    )
    caplog.set_level(logging.ERROR, logger=chat.logger.name)

    with pytest.raises(HTTPException) as exc_status:
        await chat.get_conversation_status(db=object(), user={"sub": "user-1"})

    assert exc_status.value.status_code == 500
    assert exc_status.value.detail == "Failed to retrieve conversation status"
    assert "conversation backend unavailable" in caplog.text

    caplog.clear()
    _patch_chat_impl(monkeypatch, "_resolve_session_create_active_document", lambda **_kwargs: (None, None))

    def _raise_create_session(**_kwargs):
        raise RuntimeError("conversation reset failed")

    repository.create_session = _raise_create_session

    with pytest.raises(HTTPException) as exc_reset:
        await chat.reset_conversation(_db_stub(commits=[]), {"sub": "user-1"})

    assert exc_reset.value.status_code == 500
    assert exc_reset.value.detail == "Failed to reset conversation"
    assert "conversation reset failed" in caplog.text


def test_chat_router_omits_legacy_chat_config_route():
    assert "/api/chat/config" not in {route.path for route in chat.router.routes}


@pytest.mark.asyncio
async def test_conversation_endpoints_require_user_sub():
    with pytest.raises(HTTPException) as exc_status:
        await chat.get_conversation_status(db=object(), user={})
    assert exc_status.value.status_code == 401

    with pytest.raises(HTTPException) as exc_reset:
        await chat.reset_conversation(db=object(), user={})
    assert exc_reset.value.status_code == 401

    with pytest.raises(HTTPException) as exc_hist:
        await chat.get_session_history("s-1", db=object(), user={})
    assert exc_hist.value.status_code == 401

    with pytest.raises(HTTPException) as exc_list:
        await chat.get_all_sessions_stats(chat_kind="assistant_chat", db=object(), user={})
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
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    filtered = await chat.get_all_sessions_stats(
        chat_kind="assistant_chat",
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
        chat_kind="assistant_chat",
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
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    payload = await chat.get_all_sessions_stats(
        chat_kind="assistant_chat",
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
async def test_get_all_sessions_stats_uses_generated_titles_and_schedules_lazy_backfill(monkeypatch):
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(
                session_id="session-generated",
                generated_title="Auto generated title",
                created_at=_ts(9, 0),
                last_message_at=_ts(12, 0),
            ),
            _session_record(
                session_id="session-missing",
                created_at=_ts(8, 0),
                last_message_at=_ts(11, 0),
            ),
        ]
    )
    background_tasks = BackgroundTasks()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    payload = await chat.get_all_sessions_stats(
        chat_kind="assistant_chat",
        limit=10,
        cursor=None,
        query=None,
        document_id=None,
        db=object(),
        user={"sub": "user-1"},
        background_tasks=background_tasks,
    )

    assert [session.title for session in payload.sessions] == [
        "Auto generated title",
        None,
    ]
    assert len(background_tasks.tasks) == 1


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
                    chat_kind=ASSISTANT_CHAT_KIND,
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
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(
        monkeypatch,
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
async def test_chat_history_routes_sanitize_validation_errors(monkeypatch, caplog):
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    caplog.set_level(logging.WARNING, logger=chat.logger.name)

    def _raise_history_error(**_kwargs):
        raise ValueError("history cursor exploded")

    repository.get_session_detail = _raise_history_error

    with pytest.raises(HTTPException) as exc_history:
        await chat.get_session_history(
            "session-detail",
            message_limit=50,
            message_cursor=None,
            db=object(),
            user={"sub": "user-1"},
        )

    assert exc_history.value.status_code == 400
    assert exc_history.value.detail == "Invalid chat history request"
    assert "history cursor exploded" in caplog.text

    caplog.clear()

    def _raise_search_error(**_kwargs):
        raise ValueError("search syntax exploded")

    repository.search_sessions = _raise_search_error

    with pytest.raises(HTTPException) as exc_list:
        await chat.get_all_sessions_stats(
            chat_kind="assistant_chat",
            limit=10,
            cursor=None,
            query="Alpha",
            document_id=None,
            db=object(),
            user={"sub": "user-1"},
        )

    assert exc_list.value.status_code == 400
    assert exc_list.value.detail == "Invalid chat history query"
    assert "search syntax exploded" in caplog.text


@pytest.mark.asyncio
async def test_get_session_history_uses_generated_title_from_first_page_and_queues_backfill(monkeypatch):
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(
                session_id="session-generated-detail",
                created_at=_ts(9, 0),
                last_message_at=_ts(9, 35),
            )
        ],
        detail_messages={
            ("user-1", "session-generated-detail"): [
                _message_record(
                    session_id="session-generated-detail",
                    role="user",
                    content="How does TP53 evidence differ across AGR chat history flows?",
                    created_at=_ts(9, 31),
                ),
                _message_record(
                    session_id="session-generated-detail",
                    role="assistant",
                    content="Here is the breakdown.",
                    created_at=_ts(9, 32),
                ),
            ]
        },
    )
    background_tasks = BackgroundTasks()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "get_document", lambda *_args, **_kwargs: _async_value({"document": None}))

    payload = await chat.get_session_history(
        "session-generated-detail",
        message_limit=50,
        message_cursor=None,
        db=object(),
        user={"sub": "user-1"},
        background_tasks=background_tasks,
    )

    assert payload.session.title == "How does TP53 evidence differ across AGR chat history flows?"
    assert len(background_tasks.tasks) == 1


def test_backfill_chat_session_generated_title_uses_transcript_when_user_title_is_absent(monkeypatch):
    commits: list[str] = []
    rollbacks: list[str] = []
    repository = FakeChatHistoryRepository(
        sessions=[_session_record(session_id="session-backfill")],
        detail_messages={
            ("user-1", "session-backfill"): [
                _message_record(
                    session_id="session-backfill",
                    role="user",
                    content="Hi",
                    created_at=_ts(9, 1),
                ),
                _message_record(
                    session_id="session-backfill",
                    role="assistant",
                    content="TP53 evidence summary for durable chat history",
                    created_at=_ts(9, 2),
                ),
            ]
        },
    )
    completion_db = SimpleNamespace(
        commit=lambda: commits.append("commit"),
        rollback=lambda: rollbacks.append("rollback"),
        close=lambda: None,
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "SessionLocal", lambda: completion_db)

    chat._backfill_chat_session_generated_title("session-backfill", "user-1")

    session = repository.sessions[("user-1", "session-backfill")]
    assert session.generated_title == "TP53 evidence summary for durable chat history"
    assert commits == ["commit"]
    assert rollbacks == []


def test_backfill_chat_session_generated_title_does_not_overwrite_user_title(monkeypatch):
    commits: list[str] = []
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(
                session_id="session-user-title",
                title="Renamed by curator",
            )
        ]
    )
    completion_db = SimpleNamespace(
        commit=lambda: commits.append("commit"),
        rollback=lambda: None,
        close=lambda: None,
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "SessionLocal", lambda: completion_db)

    chat._backfill_chat_session_generated_title(
        "session-user-title",
        "user-1",
        "Auto generated title",
    )

    session = repository.sessions[("user-1", "session-user-title")]
    assert session.title == "Renamed by curator"
    assert session.generated_title is None
    assert commits == []


def test_backfill_chat_session_generated_title_skips_when_session_disappears_before_message_load(
    monkeypatch,
):
    commits: list[str] = []
    rollbacks: list[str] = []
    repository = FakeChatHistoryRepository(
        sessions=[_session_record(session_id="session-race")],
    )

    def _list_messages(
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        limit: int = 100,
        cursor=None,
    ):
        del session_id, user_auth_sub, chat_kind, limit, cursor
        raise chat.ChatHistorySessionNotFoundError("Chat session not found")

    repository.list_messages = _list_messages
    completion_db = SimpleNamespace(
        commit=lambda: commits.append("commit"),
        rollback=lambda: rollbacks.append("rollback"),
        close=lambda: None,
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "SessionLocal", lambda: completion_db)

    chat._backfill_chat_session_generated_title("session-race", "user-1")

    session = repository.sessions[("user-1", "session-race")]
    assert session.generated_title is None
    assert commits == []
    assert rollbacks == ["rollback"]


def test_backfill_chat_session_generated_title_logs_and_rolls_back_when_chat_kind_missing(
    monkeypatch,
    caplog,
):
    commits: list[str] = []
    rollbacks: list[str] = []
    repository = FakeChatHistoryRepository(
        sessions=[_session_record(session_id="session-missing-kind", chat_kind=None)]
    )
    completion_db = SimpleNamespace(
        commit=lambda: commits.append("commit"),
        rollback=lambda: rollbacks.append("rollback"),
        close=lambda: None,
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    _patch_chat_impl(monkeypatch, "SessionLocal", lambda: completion_db)

    with caplog.at_level("WARNING"):
        chat._backfill_chat_session_generated_title("session-missing-kind", "user-1")

    assert commits == []
    assert rollbacks == ["rollback"]
    assert "Failed to generate durable chat title" in caplog.text
    assert "Session session-missing-kind is missing chat_kind during durable title backfill" in caplog.text


def test_serialize_session_raises_when_chat_kind_is_missing():
    record = _session_record(session_id="session-missing-kind", chat_kind=None)

    with pytest.raises(
        ValueError,
        match="Session session-missing-kind is missing chat_kind during session serialization",
    ):
        chat._serialize_session(record)


def test_serialize_message_omits_internal_flow_summary_payload_keys():
    record = ChatMessageRecord(
        message_id=uuid4(),
        session_id="session-flow-detail",
        chat_kind=ASSISTANT_CHAT_KIND,
        turn_id="turn-flow-1",
        role="flow",
        message_type=chat.FLOW_SUMMARY_MESSAGE_TYPE,
        content="Selected TP53 for highest evidence confidence.",
        payload_json={
            "flow_id": "flow-1",
            "status": "completed",
            chat.FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY: "hidden assistant flow memory",
            chat._FLOW_TRANSCRIPT_REPLAY_TERMINAL_EVENTS_KEY: [{"type": "FLOW_FINISHED"}],
        },
        trace_id="trace-flow-1",
        created_at=_ts(9, 32),
    )

    payload = chat._serialize_message(record)

    assert payload.payload_json == {
        "flow_id": "flow-1",
        "status": "completed",
    }


@pytest.mark.asyncio
async def test_get_session_history_rejects_blank_session_id(monkeypatch):
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    with pytest.raises(HTTPException) as exc:
        await chat.get_session_history(
            "   ",
            message_limit=50,
            message_cursor=None,
            db=object(),
            user={"sub": "user-1"},
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "session_id is required"


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
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    async def _raise_missing_document(*_args, **_kwargs):
        raise ValueError(f"Document {active_document_id} not found")

    _patch_chat_impl(monkeypatch, "get_document", _raise_missing_document)

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
async def test_get_session_history_returns_null_active_document_when_document_is_missing_via_http(monkeypatch):
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
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    async def _raise_not_found(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="Document not found")

    _patch_chat_impl(monkeypatch, "get_document", _raise_not_found)

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
async def test_get_session_history_propagates_unexpected_document_lookup_value_errors(monkeypatch):
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
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    async def _raise_unexpected_value_error(*_args, **_kwargs):
        raise ValueError("User with auth_sub user-1 not found")

    _patch_chat_impl(monkeypatch, "get_document", _raise_unexpected_value_error)

    with pytest.raises(ValueError, match="User with auth_sub user-1 not found"):
        await chat.get_session_history(
            "session-detail",
            message_limit=50,
            message_cursor=None,
            db=object(),
            user={"sub": "user-1"},
        )


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
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    async def _raise_server_error(*_args, **_kwargs):
        raise HTTPException(status_code=500, detail="Document service failure")

    _patch_chat_impl(monkeypatch, "get_document", _raise_server_error)

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
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    payload = await chat.rename_session(
        "session-rename",
        chat.RenameSessionRequest(title="  Renamed title  "),
        db=SimpleNamespace(commit=lambda: commits.append("commit"), rollback=lambda: None),
        user={"sub": "user-1"},
    )

    assert payload.session.title == "Renamed title"
    assert commits == ["commit"]


@pytest.mark.asyncio
async def test_chat_session_mutation_routes_sanitize_validation_errors(monkeypatch, caplog):
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(session_id="session-rename", user_auth_sub="user-1", title="Original"),
            _session_record(session_id="session-delete", user_auth_sub="user-1", title="Delete me"),
        ]
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)
    caplog.set_level(logging.WARNING, logger=chat.logger.name)

    def _raise_rename_error(**_kwargs):
        raise ValueError("title whitespace exploded")

    repository.rename_session = _raise_rename_error
    rename_rollbacks: list[str] = []

    with pytest.raises(HTTPException) as exc_rename:
        await chat.rename_session(
            "session-rename",
            chat.RenameSessionRequest(title="Renamed"),
            db=SimpleNamespace(commit=lambda: None, rollback=lambda: rename_rollbacks.append("rollback")),
            user={"sub": "user-1"},
        )

    assert exc_rename.value.status_code == 400
    assert exc_rename.value.detail == "Invalid chat session update"
    assert rename_rollbacks == ["rollback"]
    assert "title whitespace exploded" in caplog.text

    caplog.clear()

    def _raise_delete_error(**_kwargs):
        raise ValueError("session id malformed")

    repository.soft_delete_session = _raise_delete_error
    delete_rollbacks: list[str] = []

    with pytest.raises(HTTPException) as exc_delete:
        await chat.delete_session(
            "session-delete",
            db=SimpleNamespace(commit=lambda: None, rollback=lambda: delete_rollbacks.append("rollback")),
            user={"sub": "user-1"},
        )

    assert exc_delete.value.status_code == 400
    assert exc_delete.value.detail == "Invalid chat session request"
    assert delete_rollbacks == ["rollback"]
    assert "session id malformed" in caplog.text


@pytest.mark.asyncio
async def test_rename_session_returns_404_for_other_users_session(monkeypatch):
    repository = FakeChatHistoryRepository(
        sessions=[
            _session_record(session_id="session-foreign", user_auth_sub="user-2", title="Private"),
        ]
    )
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

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
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    with pytest.raises(HTTPException) as exc:
        await chat.delete_session(
            "session-foreign",
            db=SimpleNamespace(commit=lambda: None, rollback=lambda: None),
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_session_rejects_blank_session_id(monkeypatch):
    commits: list[str] = []
    rollbacks: list[str] = []
    repository = FakeChatHistoryRepository()
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

    with pytest.raises(HTTPException) as exc:
        await chat.delete_session(
            "   ",
            db=SimpleNamespace(
                commit=lambda: commits.append("commit"),
                rollback=lambda: rollbacks.append("rollback"),
            ),
            user={"sub": "user-1"},
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "session_id is required"
    assert commits == []
    assert rollbacks == []


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
    _patch_chat_impl(monkeypatch, "_get_chat_history_repository", lambda _db: repository)

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
