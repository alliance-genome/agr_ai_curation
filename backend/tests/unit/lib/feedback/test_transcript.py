"""Unit tests for feedback transcript capture and formatting helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from src.lib.chat_history_repository import ChatMessageRecord
from src.lib.feedback import transcript as transcript_module


def _message(
    *,
    session_id: str,
    role: str,
    content: str,
    minute: int,
    message_type: str = "text",
    payload_json=None,
) -> ChatMessageRecord:
    return ChatMessageRecord(
        message_id=uuid4(),
        session_id=session_id,
        turn_id=None,
        role=role,
        message_type=message_type,
        content=content,
        payload_json=payload_json,
        trace_id=None,
        created_at=datetime(2026, 4, 22, 12, minute, tzinfo=timezone.utc),
    )


class _Repository:
    def __init__(self, detail, page):
        self._detail = detail
        self._page = page
        self.calls: list[tuple[str, object]] = []

    def get_session_detail(self, *, session_id: str, user_auth_sub: str, message_limit: int):
        assert session_id == "session-1"
        assert user_auth_sub == "auth-sub-1"
        assert message_limit == transcript_module.TRANSCRIPT_PAGE_SIZE
        return self._detail

    def list_messages(self, *, session_id: str, user_auth_sub: str, limit: int, cursor):
        self.calls.append((session_id, cursor))
        assert session_id == "session-1"
        assert user_auth_sub == "auth-sub-1"
        assert limit == transcript_module.TRANSCRIPT_PAGE_SIZE
        return self._page


def test_capture_feedback_conversation_transcript_collects_all_pages():
    detail = SimpleNamespace(
        session=SimpleNamespace(
            session_id="session-1",
            title="Saved title",
            generated_title="Generated title",
            effective_title="Saved title",
            active_document_id=None,
            created_at=datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 22, 12, 5, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 22, 12, 6, tzinfo=timezone.utc),
            chat_kind="assistant_chat",
        ),
        messages=[
            _message(session_id="session-1", role="user", content="hello", minute=1),
        ],
        next_message_cursor="next-page",
    )
    page = SimpleNamespace(
        items=[
            _message(session_id="session-1", role="assistant", content="world", minute=2),
        ],
        next_cursor=None,
    )
    repository = _Repository(detail, page)

    transcript = transcript_module.capture_feedback_conversation_transcript(
        repository=repository,
        session_id="session-1",
        user_auth_sub="auth-sub-1",
    )

    assert transcript is not None
    assert transcript["message_count"] == 2
    assert transcript["session"]["session_id"] == "session-1"
    assert transcript["session"]["chat_kind"] == "assistant_chat"
    assert [message["content"] for message in transcript["messages"]] == ["hello", "world"]
    assert repository.calls == [("session-1", "next-page")]


def test_capture_feedback_conversation_transcript_returns_none_for_missing_session():
    repository = _Repository(detail=None, page=None)

    assert (
        transcript_module.capture_feedback_conversation_transcript(
            repository=repository,
            session_id="session-1",
            user_auth_sub="auth-sub-1",
        )
        is None
    )
    assert repository.calls == []


def test_format_feedback_transcript_section_uses_flow_assistant_memory_when_showing_full_snapshot():
    transcript = {
        "messages": [
            {"role": "user", "content": "turn 1"},
            {
                "role": "flow",
                "content": "summary",
                "payload_json": {transcript_module.FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY: "flow reply"},
            },
            {"role": "user", "content": "turn 2"},
            {"role": "assistant", "content": "reply 2"},
        ]
    }

    section = transcript_module.format_feedback_transcript_section(
        transcript=transcript,
        feedback_id="feedback-123",
    )

    assert section is not None
    assert "Conversation transcript snapshot:" in section
    assert "2. Assistant: flow reply" in section


def test_format_feedback_transcript_section_includes_excerpt_for_long_transcript():
    transcript = {
        "messages": [
            {"role": "user", "content": "turn 1"},
            {"role": "assistant", "content": "reply 1"},
            {"role": "user", "content": "turn 2"},
            {"role": "assistant", "content": "reply 2"},
            {"role": "user", "content": "turn 3"},
            {"role": "assistant", "content": "reply 3"},
            {"role": "user", "content": "turn 4"},
            {"role": "assistant", "content": "reply 4"},
        ]
    }

    section = transcript_module.format_feedback_transcript_section(
        transcript=transcript,
        feedback_id="feedback-123",
    )

    assert section is not None
    assert "Conversation transcript excerpt:" in section
    assert "Full durable transcript stored on feedback report feedback-123." in section
    assert "1. User: turn 1" in section
    assert "... 2 middle turns omitted ..." in section
    assert "7. User: turn 4" in section
    assert "8. Assistant: reply 4" in section
