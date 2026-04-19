"""Integration tests for the durable chat history repository."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete

from src.lib.chat_history_repository import ChatHistoryRepository
from src.models.sql.chat_message import ChatMessage
from src.models.sql.chat_session import ChatSession


USER_A = "chat-repo-user-a"
USER_B = "chat-repo-user-b"
SESSION_PREFIX = "chat-repo-test-"


def _ts(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 4, 19, hour, minute, second, tzinfo=timezone.utc)


@pytest.fixture
def db_session(test_db):
    test_db.execute(
        delete(ChatMessage).where(ChatMessage.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.execute(
        delete(ChatSession).where(ChatSession.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.commit()

    yield test_db

    test_db.execute(
        delete(ChatMessage).where(ChatMessage.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.execute(
        delete(ChatSession).where(ChatSession.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.commit()


def test_list_and_search_are_scoped_to_the_authenticated_user(db_session):
    repository = ChatHistoryRepository(db_session)

    repository.create_session(
        session_id=f"{SESSION_PREFIX}user-a",
        user_auth_sub=USER_A,
        title="Alpha findings",
        created_at=_ts(9, 0),
    )
    repository.append_message(
        session_id=f"{SESSION_PREFIX}user-a",
        user_auth_sub=USER_A,
        role="user",
        content="Alpha pathway evidence",
        turn_id="turn-a-1",
        created_at=_ts(9, 5),
    )

    repository.create_session(
        session_id=f"{SESSION_PREFIX}user-b",
        user_auth_sub=USER_B,
        title="Hidden beta session",
        created_at=_ts(10, 0),
    )
    repository.append_message(
        session_id=f"{SESSION_PREFIX}user-b",
        user_auth_sub=USER_B,
        role="user",
        content="Beta private evidence",
        turn_id="turn-b-1",
        created_at=_ts(10, 5),
    )
    db_session.commit()

    visible_to_user_a = repository.list_sessions(user_auth_sub=USER_A)
    assert [item.session_id for item in visible_to_user_a.items] == [
        f"{SESSION_PREFIX}user-a"
    ]

    search_user_a = repository.search_sessions(
        user_auth_sub=USER_A,
        query="Alpha",
    )
    assert [item.session_id for item in search_user_a.items] == [
        f"{SESSION_PREFIX}user-a"
    ]

    search_user_a_for_other_content = repository.search_sessions(
        user_auth_sub=USER_A,
        query="Beta",
    )
    assert search_user_a_for_other_content.items == []
    assert (
        repository.get_session_detail(
            session_id=f"{SESSION_PREFIX}user-b",
            user_auth_sub=USER_A,
        )
        is None
    )


def test_list_sessions_uses_recent_activity_keyset_pagination(db_session):
    repository = ChatHistoryRepository(db_session)

    repository.create_session(
        session_id=f"{SESSION_PREFIX}oldest",
        user_auth_sub=USER_A,
        title="Oldest session",
        created_at=_ts(9, 0),
    )
    repository.create_session(
        session_id=f"{SESSION_PREFIX}middle",
        user_auth_sub=USER_A,
        title="Middle session",
        created_at=_ts(9, 30),
    )
    repository.append_message(
        session_id=f"{SESSION_PREFIX}middle",
        user_auth_sub=USER_A,
        role="user",
        content="Middle activity",
        turn_id="turn-middle-1",
        created_at=_ts(11, 0),
    )
    repository.create_session(
        session_id=f"{SESSION_PREFIX}newest",
        user_auth_sub=USER_A,
        title="Newest session",
        created_at=_ts(10, 0),
    )
    repository.append_message(
        session_id=f"{SESSION_PREFIX}newest",
        user_auth_sub=USER_A,
        role="user",
        content="Newest activity",
        turn_id="turn-newest-1",
        created_at=_ts(12, 0),
    )
    db_session.commit()

    first_page = repository.list_sessions(user_auth_sub=USER_A, limit=2)
    assert [item.session_id for item in first_page.items] == [
        f"{SESSION_PREFIX}newest",
        f"{SESSION_PREFIX}middle",
    ]
    assert first_page.next_cursor is not None
    assert first_page.next_cursor.session_id == f"{SESSION_PREFIX}middle"

    second_page = repository.list_sessions(
        user_auth_sub=USER_A,
        limit=2,
        cursor=first_page.next_cursor,
    )
    assert [item.session_id for item in second_page.items] == [
        f"{SESSION_PREFIX}oldest"
    ]
    assert second_page.next_cursor is None


def test_get_session_detail_paginates_messages_in_chronological_order(db_session):
    repository = ChatHistoryRepository(db_session)
    session_id = f"{SESSION_PREFIX}detail"

    repository.create_session(
        session_id=session_id,
        user_auth_sub=USER_A,
        title="Detail session",
        created_at=_ts(8, 0),
    )
    repository.append_message(
        session_id=session_id,
        user_auth_sub=USER_A,
        role="user",
        content="First message",
        turn_id="turn-detail-1",
        created_at=_ts(8, 1),
    )
    repository.append_message(
        session_id=session_id,
        user_auth_sub=USER_A,
        role="assistant",
        content="Second message",
        turn_id="turn-detail-1",
        created_at=_ts(8, 2),
    )
    repository.append_message(
        session_id=session_id,
        user_auth_sub=USER_A,
        role="flow",
        content="Third message",
        created_at=_ts(8, 3),
    )
    db_session.commit()

    detail = repository.get_session_detail(
        session_id=session_id,
        user_auth_sub=USER_A,
        message_limit=2,
    )
    assert detail is not None
    assert [message.content for message in detail.messages] == [
        "First message",
        "Second message",
    ]
    assert detail.next_message_cursor is not None

    next_page = repository.list_messages(
        session_id=session_id,
        user_auth_sub=USER_A,
        limit=2,
        cursor=detail.next_message_cursor,
    )
    assert [message.content for message in next_page.items] == ["Third message"]
    assert next_page.next_cursor is None


def test_rename_and_soft_delete_hide_deleted_sessions_from_reads(db_session):
    repository = ChatHistoryRepository(db_session)
    session_id = f"{SESSION_PREFIX}rename-delete"

    repository.create_session(
        session_id=session_id,
        user_auth_sub=USER_A,
        title="Original title",
        created_at=_ts(13, 0),
    )
    repository.append_message(
        session_id=session_id,
        user_auth_sub=USER_A,
        role="user",
        content="Delete me after rename",
        turn_id="turn-rename-1",
        created_at=_ts(13, 5),
    )
    db_session.commit()

    renamed = repository.rename_session(
        session_id=session_id,
        user_auth_sub=USER_A,
        title="Renamed title",
    )
    assert renamed is not None
    db_session.commit()

    renamed_search = repository.search_sessions(
        user_auth_sub=USER_A,
        query="Renamed",
    )
    assert [item.session_id for item in renamed_search.items] == [session_id]

    deleted = repository.soft_delete_session(
        session_id=session_id,
        user_auth_sub=USER_A,
        deleted_at=_ts(14, 0),
    )
    assert deleted is True
    db_session.commit()

    assert repository.get_session(session_id=session_id, user_auth_sub=USER_A) is None
    assert repository.get_session_detail(
        session_id=session_id,
        user_auth_sub=USER_A,
    ) is None
    assert repository.list_sessions(user_auth_sub=USER_A).items == []
    assert repository.search_sessions(user_auth_sub=USER_A, query="Renamed").items == []
    assert (
        repository.soft_delete_session(
            session_id=session_id,
            user_auth_sub=USER_A,
        )
        is False
    )


def test_duplicate_turn_ids_use_savepoints_without_rolling_back_the_outer_transaction(
    db_session,
):
    repository = ChatHistoryRepository(db_session)
    session_id = f"{SESSION_PREFIX}duplicate-turn"

    with db_session.begin():
        repository.create_session(
            session_id=session_id,
            user_auth_sub=USER_A,
            title="Replay-safe session",
            created_at=_ts(15, 0),
        )
        first_user_message = repository.append_message(
            session_id=session_id,
            user_auth_sub=USER_A,
            role="user",
            content="Original prompt",
            turn_id="turn-replay-1",
            created_at=_ts(15, 1),
        )
        replayed_user_message = repository.append_message(
            session_id=session_id,
            user_auth_sub=USER_A,
            role="user",
            content="Replay prompt should reuse the stored row",
            turn_id="turn-replay-1",
            created_at=_ts(15, 2),
        )
        assistant_message = repository.append_message(
            session_id=session_id,
            user_auth_sub=USER_A,
            role="assistant",
            content="Assistant reply still commits",
            turn_id="turn-replay-1",
            created_at=_ts(15, 3),
        )

    assert first_user_message.created is True
    assert replayed_user_message.created is False
    assert replayed_user_message.message.message_id == first_user_message.message.message_id
    assert assistant_message.created is True

    detail = repository.get_session_detail(
        session_id=session_id,
        user_auth_sub=USER_A,
    )
    assert detail is not None
    assert [(message.role, message.content) for message in detail.messages] == [
        ("user", "Original prompt"),
        ("assistant", "Assistant reply still commits"),
    ]
    assert detail.session.last_message_at == _ts(15, 3)
