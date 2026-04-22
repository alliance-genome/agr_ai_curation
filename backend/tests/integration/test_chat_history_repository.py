"""Integration tests for the durable chat history repository."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from src.lib.chat_history_repository import (
    AGENT_STUDIO_CHAT_KIND,
    ASSISTANT_CHAT_KIND,
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
)
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


def _ensure_chat_history_tables_exist(db_session) -> None:
    User.__table__.create(bind=db_session.get_bind(), checkfirst=True)
    PDFDocument.__table__.create(bind=db_session.get_bind(), checkfirst=True)
    ChatSession.__table__.create(bind=db_session.get_bind(), checkfirst=True)
    ChatMessage.__table__.create(bind=db_session.get_bind(), checkfirst=True)


def _create_session(
    repository: ChatHistoryRepository,
    *,
    chat_kind: str = ASSISTANT_CHAT_KIND,
    **kwargs,
):
    return repository.create_session(chat_kind=chat_kind, **kwargs)


def _append_message(
    repository: ChatHistoryRepository,
    *,
    chat_kind: str = ASSISTANT_CHAT_KIND,
    **kwargs,
):
    return repository.append_message(chat_kind=chat_kind, **kwargs)


def _list_sessions(
    repository: ChatHistoryRepository,
    *,
    chat_kind: str = ASSISTANT_CHAT_KIND,
    **kwargs,
):
    return repository.list_sessions(chat_kind=chat_kind, **kwargs)


def _search_sessions(
    repository: ChatHistoryRepository,
    *,
    chat_kind: str = ASSISTANT_CHAT_KIND,
    **kwargs,
):
    return repository.search_sessions(chat_kind=chat_kind, **kwargs)


def _count_sessions(
    repository: ChatHistoryRepository,
    *,
    chat_kind: str = ASSISTANT_CHAT_KIND,
    **kwargs,
):
    return repository.count_sessions(chat_kind=chat_kind, **kwargs)


def _rename_session(
    repository: ChatHistoryRepository,
    *,
    chat_kind: str = ASSISTANT_CHAT_KIND,
    **kwargs,
):
    return repository.rename_session(chat_kind=chat_kind, **kwargs)


def _set_generated_title(
    repository: ChatHistoryRepository,
    *,
    chat_kind: str = ASSISTANT_CHAT_KIND,
    **kwargs,
):
    return repository.set_generated_title(chat_kind=chat_kind, **kwargs)


def _soft_delete_session(
    repository: ChatHistoryRepository,
    *,
    chat_kind: str = ASSISTANT_CHAT_KIND,
    **kwargs,
):
    return repository.soft_delete_session(chat_kind=chat_kind, **kwargs)


def _list_messages(
    repository: ChatHistoryRepository,
    *,
    chat_kind: str = ASSISTANT_CHAT_KIND,
    **kwargs,
):
    return repository.list_messages(chat_kind=chat_kind, **kwargs)


@pytest.fixture
def db_session(test_db):
    _ensure_chat_history_tables_exist(test_db)

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

    _create_session(
        repository,
        session_id=f"{SESSION_PREFIX}user-a",
        user_auth_sub=USER_A,
        title="Alpha findings",
        created_at=_ts(9, 0),
    )
    _append_message(
        repository,
        session_id=f"{SESSION_PREFIX}user-a",
        user_auth_sub=USER_A,
        role="user",
        content="Alpha pathway evidence",
        turn_id="turn-a-1",
        created_at=_ts(9, 5),
    )

    _create_session(
        repository,
        session_id=f"{SESSION_PREFIX}user-b",
        user_auth_sub=USER_B,
        title="Hidden beta session",
        created_at=_ts(10, 0),
    )
    _append_message(
        repository,
        session_id=f"{SESSION_PREFIX}user-b",
        user_auth_sub=USER_B,
        role="user",
        content="Beta private evidence",
        turn_id="turn-b-1",
        created_at=_ts(10, 5),
    )
    db_session.commit()

    visible_to_user_a = _list_sessions(repository, user_auth_sub=USER_A)
    assert [item.session_id for item in visible_to_user_a.items] == [
        f"{SESSION_PREFIX}user-a"
    ]

    search_user_a = _search_sessions(
        repository,
        user_auth_sub=USER_A,
        query="Alpha",
    )
    assert [item.session_id for item in search_user_a.items] == [
        f"{SESSION_PREFIX}user-a"
    ]

    search_user_a_for_other_content = _search_sessions(
        repository,
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

    _create_session(
        repository,
        session_id=f"{SESSION_PREFIX}oldest",
        user_auth_sub=USER_A,
        title="Oldest session",
        created_at=_ts(9, 0),
    )
    _create_session(
        repository,
        session_id=f"{SESSION_PREFIX}middle",
        user_auth_sub=USER_A,
        title="Middle session",
        created_at=_ts(9, 30),
    )
    _append_message(
        repository,
        session_id=f"{SESSION_PREFIX}middle",
        user_auth_sub=USER_A,
        role="user",
        content="Middle activity",
        turn_id="turn-middle-1",
        created_at=_ts(11, 0),
    )
    _create_session(
        repository,
        session_id=f"{SESSION_PREFIX}newest",
        user_auth_sub=USER_A,
        title="Newest session",
        created_at=_ts(10, 0),
    )
    _append_message(
        repository,
        session_id=f"{SESSION_PREFIX}newest",
        user_auth_sub=USER_A,
        role="user",
        content="Newest activity",
        turn_id="turn-newest-1",
        created_at=_ts(12, 0),
    )
    db_session.commit()

    first_page = _list_sessions(repository, user_auth_sub=USER_A, limit=2)
    assert [item.session_id for item in first_page.items] == [
        f"{SESSION_PREFIX}newest",
        f"{SESSION_PREFIX}middle",
    ]
    assert first_page.next_cursor is not None
    assert first_page.next_cursor.session_id == f"{SESSION_PREFIX}middle"

    second_page = _list_sessions(
        repository,
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

    _create_session(
        repository,
        session_id=f"{SESSION_PREFIX}doc-a-1",
        user_auth_sub=USER_A,
        title="Alpha doc session",
        active_document_id=document_a,
        created_at=_ts(9, 0),
    )
    _create_session(
        repository,
        session_id=f"{SESSION_PREFIX}doc-a-2",
        user_auth_sub=USER_A,
        title="Second alpha doc session",
        active_document_id=document_a,
        created_at=_ts(9, 30),
    )
    _create_session(
        repository,
        session_id=f"{SESSION_PREFIX}doc-b-1",
        user_auth_sub=USER_A,
        title="Beta doc session",
        active_document_id=document_b,
        created_at=_ts(10, 0),
    )
    _create_session(
        repository,
        session_id=f"{SESSION_PREFIX}other-user-doc-a",
        user_auth_sub=USER_B,
        title="Hidden alpha doc session",
        active_document_id=document_a,
        created_at=_ts(10, 30),
    )
    db_session.commit()

    filtered = _list_sessions(
        repository,
        user_auth_sub=USER_A,
        active_document_id=document_a,
    )
    assert [item.session_id for item in filtered.items] == [
        f"{SESSION_PREFIX}doc-a-2",
        f"{SESSION_PREFIX}doc-a-1",
    ]
    assert _count_sessions(repository, user_auth_sub=USER_A) == 3
    assert (
        _count_sessions(
            repository,
            user_auth_sub=USER_A,
            active_document_id=document_a,
        )
        == 2
    )
    assert (
        _count_sessions(
            repository,
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

    _create_session(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        title="Detail session",
        created_at=_ts(8, 0),
    )
    _append_message(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        role="user",
        content="First message",
        turn_id="turn-detail-1",
        created_at=_ts(8, 1),
    )
    _append_message(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        role="assistant",
        content="Second message",
        turn_id="turn-detail-1",
        created_at=_ts(8, 2),
    )
    _append_message(
        repository,
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
    assert detail.session.chat_kind == ASSISTANT_CHAT_KIND
    assert [message.content for message in detail.messages] == [
        "First message",
        "Second message",
    ]
    assert [message.chat_kind for message in detail.messages] == [
        ASSISTANT_CHAT_KIND,
        ASSISTANT_CHAT_KIND,
    ]
    assert detail.next_message_cursor is not None

    next_page = _list_messages(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        limit=2,
        cursor=detail.next_message_cursor,
    )
    assert [message.content for message in next_page.items] == ["Third message"]
    assert next_page.next_cursor is None


def test_get_message_by_turn_id_returns_one_visible_turn_row(db_session):
    repository = ChatHistoryRepository(db_session)
    session_id = f"{SESSION_PREFIX}turn-lookup"

    _create_session(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        title="Turn lookup session",
        created_at=_ts(12, 0),
    )
    _append_message(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        role="user",
        content="Persist this turn",
        turn_id="turn-lookup-1",
        created_at=_ts(12, 1),
    )
    _append_message(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        role="assistant",
        content="Replay this answer",
        turn_id="turn-lookup-1",
        created_at=_ts(12, 2),
    )
    db_session.commit()

    assistant_message = repository.get_message_by_turn_id(
        session_id=session_id,
        user_auth_sub=USER_A,
        turn_id="turn-lookup-1",
        role="assistant",
    )
    assert assistant_message is not None
    assert assistant_message.chat_kind == ASSISTANT_CHAT_KIND
    assert assistant_message.content == "Replay this answer"

    with pytest.raises(ChatHistorySessionNotFoundError):
        repository.get_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=USER_B,
            turn_id="turn-lookup-1",
            role="assistant",
        )


def test_chat_kind_scopes_explicit_queries_and_by_id_reads(db_session):
    repository = ChatHistoryRepository(db_session)
    assistant_session_id = f"{SESSION_PREFIX}assistant-kind"
    studio_session_id = f"{SESSION_PREFIX}studio-kind"

    _create_session(
        repository,
        session_id=assistant_session_id,
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
        title="Assistant TP53 session",
        created_at=_ts(12, 10),
    )
    _append_message(
        repository,
        session_id=assistant_session_id,
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
        role="assistant",
        content="Assistant TP53 answer",
        turn_id="turn-kind-assistant-1",
        created_at=_ts(12, 11),
    )

    _create_session(
        repository,
        session_id=studio_session_id,
        user_auth_sub=USER_A,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        title="Agent studio EGFR session",
        created_at=_ts(12, 20),
    )
    _append_message(
        repository,
        session_id=studio_session_id,
        user_auth_sub=USER_A,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        role="assistant",
        content="Agent studio EGFR answer",
        turn_id="turn-kind-studio-1",
        created_at=_ts(12, 21),
    )
    db_session.commit()

    assistant_page = _list_sessions(
        repository,
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
    )
    studio_page = _list_sessions(
        repository,
        user_auth_sub=USER_A,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
    )
    assert [item.session_id for item in assistant_page.items] == [assistant_session_id]
    assert [item.chat_kind for item in assistant_page.items] == [ASSISTANT_CHAT_KIND]
    assert [item.session_id for item in studio_page.items] == [studio_session_id]
    assert [item.chat_kind for item in studio_page.items] == [AGENT_STUDIO_CHAT_KIND]

    assistant_search = _search_sessions(
        repository,
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
        query="TP53",
    )
    studio_search = _search_sessions(
        repository,
        user_auth_sub=USER_A,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        query="EGFR",
    )
    assert [item.session_id for item in assistant_search.items] == [assistant_session_id]
    assert [item.session_id for item in studio_search.items] == [studio_session_id]
    assert (
        _count_sessions(
            repository,
            user_auth_sub=USER_A,
            chat_kind=ASSISTANT_CHAT_KIND,
        )
        == 1
    )
    assert (
        _count_sessions(
            repository,
            user_auth_sub=USER_A,
            chat_kind=AGENT_STUDIO_CHAT_KIND,
        )
        == 1
    )

    studio_detail = repository.get_session_detail(
        session_id=studio_session_id,
        user_auth_sub=USER_A,
    )
    assert studio_detail is not None
    assert studio_detail.session.chat_kind == AGENT_STUDIO_CHAT_KIND
    assert [message.chat_kind for message in studio_detail.messages] == [
        AGENT_STUDIO_CHAT_KIND
    ]

    assistant_messages = _list_messages(
        repository,
        session_id=assistant_session_id,
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
    )
    assert [message.chat_kind for message in assistant_messages.items] == [
        ASSISTANT_CHAT_KIND
    ]
    with pytest.raises(ChatHistorySessionNotFoundError):
        _list_messages(
            repository,
            session_id=studio_session_id,
            user_auth_sub=USER_A,
            chat_kind=ASSISTANT_CHAT_KIND,
        )

    studio_message = repository.get_message_by_turn_id(
        session_id=studio_session_id,
        user_auth_sub=USER_A,
        turn_id="turn-kind-studio-1",
        role="assistant",
    )
    assert studio_message is not None
    assert studio_message.chat_kind == AGENT_STUDIO_CHAT_KIND


def test_chat_kind_check_constraints_reject_unknown_values(db_session):
    invalid_session = ChatSession(
        session_id=f"{SESSION_PREFIX}invalid-kind-session",
        user_auth_sub=USER_A,
        chat_kind="unknown_kind",
    )
    db_session.add(invalid_session)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    _create_session(
        ChatHistoryRepository(db_session),
        session_id=f"{SESSION_PREFIX}valid-kind-session",
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
        created_at=_ts(12, 30),
    )
    db_session.commit()

    invalid_message = ChatMessage(
        session_id=f"{SESSION_PREFIX}valid-kind-session",
        chat_kind="unknown_kind",
        role="user",
        message_type="text",
        content="invalid kind message",
    )
    db_session.add(invalid_message)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_rename_and_soft_delete_hide_deleted_sessions_from_reads(db_session):
    repository = ChatHistoryRepository(db_session)
    session_id = f"{SESSION_PREFIX}rename-delete"

    _create_session(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        title="Original title",
        created_at=_ts(13, 0),
    )
    _append_message(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        role="user",
        content="Delete me after rename",
        turn_id="turn-rename-1",
        created_at=_ts(13, 5),
    )
    db_session.commit()

    renamed = _rename_session(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        title="Renamed title",
    )
    assert renamed is not None
    db_session.commit()

    renamed_search = _search_sessions(
        repository,
        user_auth_sub=USER_A,
        query="Renamed",
    )
    assert [item.session_id for item in renamed_search.items] == [session_id]

    deleted = _soft_delete_session(
        repository,
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
    assert _list_sessions(repository, user_auth_sub=USER_A).items == []
    assert _search_sessions(repository, user_auth_sub=USER_A, query="Renamed").items == []
    assert (
        _soft_delete_session(
            repository,
            session_id=session_id,
            user_auth_sub=USER_A,
        )
        is False
    )


def test_generated_titles_are_searchable_and_visible_when_user_title_is_missing(db_session):
    repository = ChatHistoryRepository(db_session)
    session_id = f"{SESSION_PREFIX}generated-title"

    _create_session(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        created_at=_ts(14, 10),
    )
    generated = _set_generated_title(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        generated_title="Auto summary for TP53 evidence",
    )
    assert generated is not None
    db_session.commit()

    session = repository.get_session(
        session_id=session_id,
        user_auth_sub=USER_A,
    )
    assert session is not None
    assert session.title is None
    assert session.generated_title == "Auto summary for TP53 evidence"
    assert session.effective_title == "Auto summary for TP53 evidence"

    search_results = _search_sessions(
        repository,
        user_auth_sub=USER_A,
        query="TP53",
    )
    assert [item.session_id for item in search_results.items] == [session_id]


def test_generated_titles_do_not_overwrite_user_managed_titles(db_session):
    repository = ChatHistoryRepository(db_session)
    session_id = f"{SESSION_PREFIX}user-title-wins"

    _create_session(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        title="Curator-chosen title",
        created_at=_ts(14, 20),
    )
    updated = _set_generated_title(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        generated_title="Automated title should be ignored",
    )
    assert updated is not None
    db_session.commit()

    session = repository.get_session(
        session_id=session_id,
        user_auth_sub=USER_A,
    )
    assert session is not None
    assert session.title == "Curator-chosen title"
    assert session.generated_title is None
    assert session.effective_title == "Curator-chosen title"


def test_generated_titles_do_not_overwrite_existing_automated_titles(db_session):
    repository = ChatHistoryRepository(db_session)
    session_id = f"{SESSION_PREFIX}auto-title-wins"

    _create_session(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        generated_title="Precomputed summary title",
        created_at=_ts(14, 25),
    )
    updated = _set_generated_title(
        repository,
        session_id=session_id,
        user_auth_sub=USER_A,
        generated_title="Refreshed auto summary title",
    )
    assert updated is not None
    db_session.commit()

    session = repository.get_session(
        session_id=session_id,
        user_auth_sub=USER_A,
    )
    assert session is not None
    assert session.generated_title == "Precomputed summary title"
    assert session.effective_title == "Precomputed summary title"


def test_duplicate_turn_ids_use_savepoints_without_rolling_back_the_outer_transaction(
    db_session,
):
    repository = ChatHistoryRepository(db_session)
    session_id = f"{SESSION_PREFIX}duplicate-turn"

    with db_session.begin():
        _create_session(
            repository,
            session_id=session_id,
            user_auth_sub=USER_A,
            title="Replay-safe session",
            created_at=_ts(15, 0),
        )
        first_user_message = _append_message(
            repository,
            session_id=session_id,
            user_auth_sub=USER_A,
            role="user",
            content="Original prompt",
            turn_id="turn-replay-1",
            created_at=_ts(15, 1),
        )
        replayed_user_message = _append_message(
            repository,
            session_id=session_id,
            user_auth_sub=USER_A,
            role="user",
            content="Replay prompt should reuse the stored row",
            turn_id="turn-replay-1",
            created_at=_ts(15, 2),
        )
        assistant_message = _append_message(
            repository,
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
