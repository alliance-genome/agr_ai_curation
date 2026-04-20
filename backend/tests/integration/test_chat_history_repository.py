"""Integration tests for the durable chat history repository."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.lib.chat_history_repository import ChatHistoryRepository
from src.models.sql.chat_message import ChatMessage
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.chat_session import ChatSession
from src.models.sql.user import User


USER_A = "chat-repo-user-a"
USER_B = "chat-repo-user-b"
SESSION_PREFIX = "chat-repo-test-"
DOCUMENT_PREFIX = "chat-repo-doc-"


def _ts(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 4, 19, hour, minute, second, tzinfo=timezone.utc)


@pytest.fixture
def db_session(test_db):
    test_db.execute(
        delete(PDFDocument).where(PDFDocument.filename.like(f"{DOCUMENT_PREFIX}%"))
    )
    test_db.execute(
        delete(ChatMessage).where(ChatMessage.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.execute(
        delete(ChatSession).where(ChatSession.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.execute(
        delete(User).where(User.auth_sub.in_((USER_A, USER_B)))
    )
    test_db.commit()

    yield test_db

    test_db.execute(
        delete(PDFDocument).where(PDFDocument.filename.like(f"{DOCUMENT_PREFIX}%"))
    )
    test_db.execute(
        delete(ChatMessage).where(ChatMessage.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.execute(
        delete(ChatSession).where(ChatSession.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.execute(
        delete(User).where(User.auth_sub.in_((USER_A, USER_B)))
    )
    test_db.commit()


def _create_user(db_session, *, auth_sub: str) -> User:
    user = User(
        auth_sub=auth_sub,
        email=f"{auth_sub}@example.org",
        display_name=auth_sub,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _create_document(db_session, *, document_id, suffix: str, user_id: int | None = None) -> None:
    db_session.add(
        PDFDocument(
            id=document_id,
            filename=f"{DOCUMENT_PREFIX}{suffix}.pdf",
            file_path=f"/tmp/{DOCUMENT_PREFIX}{suffix}.pdf",
            file_hash=f"{suffix:0>64}"[:64],
            file_size=1024,
            page_count=2,
            user_id=user_id,
        )
    )
    db_session.flush()


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


def test_count_and_document_filters_respect_user_scope(db_session):
    repository = ChatHistoryRepository(db_session)
    document_a = uuid4()
    document_b = uuid4()
    _create_document(db_session, document_id=document_a, suffix="doc-a")
    _create_document(db_session, document_id=document_b, suffix="doc-b")

    repository.create_session(
        session_id=f"{SESSION_PREFIX}doc-a-1",
        user_auth_sub=USER_A,
        title="Alpha doc session",
        active_document_id=document_a,
        created_at=_ts(9, 0),
    )
    repository.create_session(
        session_id=f"{SESSION_PREFIX}doc-a-2",
        user_auth_sub=USER_A,
        title="Second alpha doc session",
        active_document_id=document_a,
        created_at=_ts(9, 30),
    )
    repository.create_session(
        session_id=f"{SESSION_PREFIX}doc-b-1",
        user_auth_sub=USER_A,
        title="Beta doc session",
        active_document_id=document_b,
        created_at=_ts(10, 0),
    )
    repository.create_session(
        session_id=f"{SESSION_PREFIX}other-user-doc-a",
        user_auth_sub=USER_B,
        title="Hidden alpha doc session",
        active_document_id=document_a,
        created_at=_ts(10, 30),
    )
    db_session.commit()

    filtered = repository.list_sessions(
        user_auth_sub=USER_A,
        active_document_id=document_a,
    )
    assert [item.session_id for item in filtered.items] == [
        f"{SESSION_PREFIX}doc-a-2",
        f"{SESSION_PREFIX}doc-a-1",
    ]
    assert repository.count_sessions(user_auth_sub=USER_A) == 3
    assert (
        repository.count_sessions(
            user_auth_sub=USER_A,
            active_document_id=document_a,
        )
        == 2
    )
    assert (
        repository.count_sessions(
            user_auth_sub=USER_A,
            query="alpha",
            active_document_id=document_a,
        )
        == 2
    )


def test_get_visible_document_id_is_scoped_to_the_authenticated_user(db_session):
    repository = ChatHistoryRepository(db_session)
    user_a = _create_user(db_session, auth_sub=USER_A)
    user_b = _create_user(db_session, auth_sub=USER_B)
    visible_document_id = uuid4()
    hidden_document_id = uuid4()
    orphan_document_id = uuid4()

    _create_document(
        db_session,
        document_id=visible_document_id,
        suffix="visible",
        user_id=user_a.id,
    )
    _create_document(
        db_session,
        document_id=hidden_document_id,
        suffix="hidden",
        user_id=user_b.id,
    )
    _create_document(
        db_session,
        document_id=orphan_document_id,
        suffix="orphan",
    )
    db_session.commit()

    assert (
        repository.get_visible_document_id(
            document_id=visible_document_id,
            user_auth_sub=USER_A,
        )
        == visible_document_id
    )
    assert (
        repository.get_visible_document_id(
            document_id=hidden_document_id,
            user_auth_sub=USER_A,
        )
        is None
    )
    assert (
        repository.get_visible_document_id(
            document_id=orphan_document_id,
            user_auth_sub=USER_A,
        )
        is None
    )
    assert (
        repository.get_visible_document_id(
            document_id=uuid4(),
            user_auth_sub=USER_A,
        )
        is None
    )


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
