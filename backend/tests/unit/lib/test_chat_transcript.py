"""Unit tests for durable chat transcript helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from src.lib.chat_history_repository import ChatHistorySessionNotFoundError, ChatMessageCursor, ChatMessageRecord
from src.lib.chat_transcript import (
    FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY,
    collect_durable_text_exchanges,
    count_session_text_messages,
    latest_assistant_message_for_session,
    list_session_text_exchanges,
)


def _message(
    *,
    session_id: str,
    role: str,
    content: str,
    message_type: str = "text",
    payload_json=None,
    minute: int = 0,
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
        created_at=datetime(2026, 4, 21, 12, minute, tzinfo=timezone.utc),
    )


class _FakeRepository:
    def __init__(self, pages: dict[tuple[str, str], list[list[ChatMessageRecord]]]) -> None:
        self._pages = pages
        self.calls: list[tuple[str, str, object]] = []

    def list_messages(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        limit: int,
        cursor,
    ):
        del limit
        self.calls.append((session_id, user_auth_sub, cursor))
        page_sets = self._pages.get((user_auth_sub, session_id))
        if page_sets is None:
            raise ChatHistorySessionNotFoundError("Chat session not found")

        page_index = 0 if cursor is None else int(cursor)
        items = page_sets[page_index]
        next_cursor = page_index + 1 if page_index + 1 < len(page_sets) else None
        return SimpleNamespace(items=items, next_cursor=next_cursor)


def test_collect_durable_text_exchanges_preserves_completed_pairs_and_flow_memory():
    exchanges, pending_user_message = collect_durable_text_exchanges(
        [
            _message(session_id="session-1", role="user", content="first question", minute=1),
            _message(session_id="session-1", role="assistant", content="first answer", minute=2),
            _message(session_id="session-1", role="user", content="run flow", minute=3),
            _message(
                session_id="session-1",
                role="flow",
                content="visible flow summary",
                message_type="flow_summary",
                payload_json={FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY: "hidden assistant flow memory"},
                minute=4,
            ),
            _message(session_id="session-1", role="user", content="unfinished question", minute=5),
        ]
    )

    assert exchanges == [
        ("first question", "first answer"),
        ("run flow", "hidden assistant flow memory"),
    ]
    assert pending_user_message == "unfinished question"


def test_list_session_text_exchanges_pages_until_completion():
    repository = _FakeRepository(
        {
            ("user-1", "session-1"): [
                [
                    _message(session_id="session-1", role="user", content="first question", minute=1),
                    _message(session_id="session-1", role="assistant", content="first answer", minute=2),
                ],
                [
                    _message(session_id="session-1", role="user", content="second question", minute=3),
                    _message(session_id="session-1", role="assistant", content="second answer", minute=4),
                ],
            ]
        }
    )

    assert list_session_text_exchanges(
        session_id="session-1",
        user_id="user-1",
        repository=repository,
    ) == [
        ("first question", "first answer"),
        ("second question", "second answer"),
    ]
    assert repository.calls == [
        ("session-1", "user-1", None),
        ("session-1", "user-1", 1),
    ]


def test_latest_assistant_message_for_session_returns_none_when_session_is_missing():
    repository = _FakeRepository({})

    assert latest_assistant_message_for_session(
        session_id="missing-session",
        user_id="user-1",
        repository=repository,
    ) is None
    assert count_session_text_messages(
        session_id="missing-session",
        user_id="user-1",
        repository=repository,
    ) == 0
