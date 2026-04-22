"""Unit tests for chat history repository session record shaping."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import MagicMock

from src.lib.chat_history_repository import (
    AGENT_STUDIO_CHAT_KIND,
    ASSISTANT_CHAT_KIND,
    ChatHistoryRepository,
    ChatMessagePage,
)
from src.models.sql.chat_session import ChatSession


def _session_model() -> ChatSession:
    session = ChatSession(
        session_id="session-1",
        user_auth_sub="auth-sub-1",
        title="Saved title",
        generated_title=None,
        active_document_id=uuid4(),
    )
    timestamp = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    session.created_at = timestamp
    session.updated_at = timestamp
    session.last_message_at = timestamp
    session.deleted_at = None
    return session


def test_get_session_detail_returns_none_when_model_has_no_chat_kind(monkeypatch):
    repository = ChatHistoryRepository(MagicMock())
    session = _session_model()

    monkeypatch.setattr(repository, "_get_active_session", lambda **_kwargs: session)
    monkeypatch.setattr(
        repository,
        "_list_messages_for_session",
        lambda **_kwargs: ChatMessagePage(items=[], next_cursor=None),
    )

    detail = repository.get_session_detail(
        session_id="session-1",
        user_auth_sub="auth-sub-1",
    )

    assert detail is not None
    assert detail.session.chat_kind is None


def test_get_session_detail_preserves_explicit_chat_kind(monkeypatch):
    repository = ChatHistoryRepository(MagicMock())
    session = _session_model()
    session.chat_kind = AGENT_STUDIO_CHAT_KIND

    monkeypatch.setattr(repository, "_get_active_session", lambda **_kwargs: session)
    monkeypatch.setattr(
        repository,
        "_list_messages_for_session",
        lambda **_kwargs: ChatMessagePage(items=[], next_cursor=None),
    )

    detail = repository.get_session_detail(
        session_id="session-1",
        user_auth_sub="auth-sub-1",
    )

    assert detail is not None
    assert detail.session.chat_kind == AGENT_STUDIO_CHAT_KIND


def test_get_session_detail_preserves_explicit_assistant_chat_kind(monkeypatch):
    repository = ChatHistoryRepository(MagicMock())
    session = _session_model()
    session.chat_kind = ASSISTANT_CHAT_KIND

    monkeypatch.setattr(repository, "_get_active_session", lambda **_kwargs: session)
    monkeypatch.setattr(
        repository,
        "_list_messages_for_session",
        lambda **_kwargs: ChatMessagePage(items=[], next_cursor=None),
    )

    detail = repository.get_session_detail(
        session_id="session-1",
        user_auth_sub="auth-sub-1",
    )

    assert detail is not None
    assert detail.session.chat_kind == ASSISTANT_CHAT_KIND
