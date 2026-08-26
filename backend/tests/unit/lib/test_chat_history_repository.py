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
from src.lib.persistence_sanitization import sanitize_persisted_json_value
from src.models.sql.chat_message import ChatMessage
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


def test_list_messages_passes_after_created_at_to_message_query(monkeypatch):
    repository = ChatHistoryRepository(MagicMock())
    session = _session_model()
    session.chat_kind = ASSISTANT_CHAT_KIND
    after_created_at = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    captured_kwargs = {}

    monkeypatch.setattr(repository, "_require_active_session_for_kind", lambda **_kwargs: session)

    def _list_messages_for_session(**kwargs):
        captured_kwargs.update(kwargs)
        return ChatMessagePage(items=[], next_cursor=None)

    monkeypatch.setattr(repository, "_list_messages_for_session", _list_messages_for_session)

    page = repository.list_messages(
        session_id="session-1",
        user_auth_sub="auth-sub-1",
        chat_kind=ASSISTANT_CHAT_KIND,
        limit=25,
        after_created_at=after_created_at,
    )

    assert page.items == []
    assert captured_kwargs["after_created_at"] == after_created_at


def test_append_message_sanitizes_nested_postgres_incompatible_text(monkeypatch):
    db = MagicMock()
    repository = ChatHistoryRepository(db)
    session = _session_model()
    session.chat_kind = ASSISTANT_CHAT_KIND
    monkeypatch.setattr(
        repository,
        "_require_active_session_for_kind",
        lambda **_kwargs: session,
    )

    repository.append_message(
        session_id="session-1",
        user_auth_sub="auth-sub-1",
        chat_kind=ASSISTANT_CHAT_KIND,
        role="flow",
        content="Evidence A\x00B",
        message_type="flow_step_evidence",
        payload_json={
            "type": "FLOW_STEP_EVIDENCE",
            "details": {
                "evidence_records": [
                    {
                        "quote": "A\x00B",
                        "nested": {"unsafe\x00key": "C\x00D"},
                        "sequence": ("E\x00F", {"value": "G\x00H"}),
                    }
                ]
            },
            "evidence_count": 1,
            "supported_unicode": "café Δ",
            "unchanged_values": [7, True, None],
        },
    )

    persisted = db.add.call_args.args[0]
    assert isinstance(persisted, ChatMessage)
    assert persisted.content == "Evidence AB"
    assert persisted.payload_json == {
        "type": "FLOW_STEP_EVIDENCE",
        "details": {
            "evidence_records": [
                {
                    "quote": "AB",
                    "nested": {"unsafekey": "CD"},
                    "sequence": ("EF", {"value": "GH"}),
                }
            ]
        },
        "evidence_count": 1,
        "supported_unicode": "café Δ",
        "unchanged_values": [7, True, None],
    }
    assert db.flush.call_count == 1


def test_persistence_sanitizer_leaves_ordinary_payload_unchanged_and_is_idempotent():
    ordinary_payload = {
        "text": "ordinary café Δ",
        "sequence": [1, True, None, ("nested", {"key": "value"})],
    }
    unsafe_payload = {
        "unsafe\x00key": ["A\x00B", ("C\x00D", {"value": "E\x00F"})]
    }

    assert sanitize_persisted_json_value(ordinary_payload) == ordinary_payload
    sanitized_once = sanitize_persisted_json_value(unsafe_payload)
    assert sanitize_persisted_json_value(sanitized_once) == sanitized_once
    assert sanitized_once == {"unsafekey": ["AB", ("CD", {"value": "EF"})]}


def test_update_message_sanitizes_payload_before_flush(monkeypatch):
    db = MagicMock()
    repository = ChatHistoryRepository(db)
    session = _session_model()
    session.chat_kind = ASSISTANT_CHAT_KIND
    message = ChatMessage(
        session_id="session-1",
        chat_kind=ASSISTANT_CHAT_KIND,
        turn_id="turn-1",
        role="user",
        message_type="text",
        content="Run flow",
        payload_json={"state": "prepared"},
    )
    message.created_at = datetime(2026, 4, 22, 12, 1, tzinfo=timezone.utc)
    db.scalar.return_value = message
    monkeypatch.setattr(repository, "_require_active_session", lambda **_kwargs: session)

    repository.update_message_by_turn_id(
        session_id="session-1",
        user_auth_sub="auth-sub-1",
        turn_id="turn-1",
        role="user",
        payload_json={"unsafe\x00key": {"value": "A\x00B"}},
    )

    assert message.payload_json == {"unsafekey": {"value": "AB"}}
    assert db.flush.call_count == 1
