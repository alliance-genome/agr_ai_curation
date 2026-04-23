"""Integration coverage for Agent Studio chat history tools and auth scoping."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, text

from src.api import agent_studio as api_module
from src.lib.chat_history_repository import (
    AGENT_STUDIO_CHAT_KIND,
    ASSISTANT_CHAT_KIND,
    ChatHistoryRepository,
)
from src.models.sql.chat_message import ChatMessage
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.chat_session import ChatSession
from src.models.sql.user import User


USER_A = "test_agent_studio_tools_user_a"
USER_B = "test_agent_studio_tools_user_b"
SESSION_PREFIX = "test_agent_studio_tools_session_"


def _ts(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 4, 23, hour, minute, second, tzinfo=timezone.utc)


def _ensure_chat_history_tables_exist(db_session) -> None:
    User.__table__.create(bind=db_session.get_bind(), checkfirst=True)
    PDFDocument.__table__.create(bind=db_session.get_bind(), checkfirst=True)
    ChatSession.__table__.create(bind=db_session.get_bind(), checkfirst=True)
    ChatMessage.__table__.create(bind=db_session.get_bind(), checkfirst=True)


@pytest.fixture
def db_session(test_db):
    _ensure_chat_history_tables_exist(test_db)

    test_db.execute(
        delete(ChatMessage).where(ChatMessage.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.execute(
        delete(ChatSession).where(ChatSession.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.execute(delete(User).where(User.auth_sub.in_((USER_A, USER_B))))
    test_db.commit()

    yield test_db

    test_db.execute(
        delete(ChatMessage).where(ChatMessage.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.execute(
        delete(ChatSession).where(ChatSession.session_id.like(f"{SESSION_PREFIX}%"))
    )
    test_db.execute(delete(User).where(User.auth_sub.in_((USER_A, USER_B))))
    test_db.commit()


def _create_user(db_session, *, auth_sub: str) -> None:
    db_session.add(
        User(
            auth_sub=auth_sub,
            email=f"{auth_sub}@example.org",
            display_name=auth_sub,
            is_active=True,
        )
    )
    db_session.flush()


def _refresh_session_search_vector(db_session, *, session_id: str) -> None:
    db_session.execute(
        text(
            """
            UPDATE chat_sessions
            SET
                last_message_at = (
                    SELECT MAX(chat_messages.created_at)
                    FROM chat_messages
                    WHERE chat_messages.session_id = :session_id
                ),
                search_vector = to_tsvector(
                    'english',
                    concat_ws(
                        ' ',
                        COALESCE(title, ''),
                        COALESCE(generated_title, ''),
                        COALESCE(
                            (
                                SELECT string_agg(chat_messages.content, ' ' ORDER BY chat_messages.created_at)
                                FROM chat_messages
                                WHERE chat_messages.session_id = :session_id
                            ),
                            ''
                        )
                    )
                )
            WHERE session_id = :session_id
            """
        ),
        {"session_id": session_id},
    )


def test_list_recent_chats_scopes_results_to_user_and_all_chat_kinds(db_session):
    repository = ChatHistoryRepository(db_session)
    _create_user(db_session, auth_sub=USER_A)
    _create_user(db_session, auth_sub=USER_B)

    repository.create_session(
        session_id=f"{SESSION_PREFIX}assistant-visible",
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
        title="Visible assistant session",
        created_at=_ts(9, 0),
    )
    repository.append_message(
        session_id=f"{SESSION_PREFIX}assistant-visible",
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
        role="user",
        content="assistant visible content",
        turn_id="turn-a-1",
        created_at=_ts(9, 5),
    )
    repository.create_session(
        session_id=f"{SESSION_PREFIX}studio-visible",
        user_auth_sub=USER_A,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        title="Visible studio session",
        created_at=_ts(10, 0),
    )
    repository.append_message(
        session_id=f"{SESSION_PREFIX}studio-visible",
        user_auth_sub=USER_A,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        role="assistant",
        content="studio visible content",
        turn_id="turn-s-1",
        created_at=_ts(10, 10),
    )
    repository.create_session(
        session_id=f"{SESSION_PREFIX}assistant-hidden",
        user_auth_sub=USER_B,
        chat_kind=ASSISTANT_CHAT_KIND,
        title="Hidden other-user session",
        created_at=_ts(11, 0),
    )
    repository.append_message(
        session_id=f"{SESSION_PREFIX}assistant-hidden",
        user_auth_sub=USER_B,
        chat_kind=ASSISTANT_CHAT_KIND,
        role="user",
        content="hidden content",
        turn_id="turn-b-1",
        created_at=_ts(11, 5),
    )
    db_session.commit()

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="list_recent_chats",
            tool_input={"chat_kind": "all", "limit": 5},
            context=None,
            user_email=f"{USER_A}@example.org",
            user_auth_sub=USER_A,
            messages=[],
        )
    )

    assert result["success"] is True
    assert result["total_sessions"] == 2
    assert [session["session_id"] for session in result["sessions"]] == [
        f"{SESSION_PREFIX}studio-visible",
        f"{SESSION_PREFIX}assistant-visible",
    ]
    assert {session["chat_kind"] for session in result["sessions"]} == {
        ASSISTANT_CHAT_KIND,
        AGENT_STUDIO_CHAT_KIND,
    }


def test_search_chat_history_returns_ranked_results_for_all_chat_kinds(db_session):
    repository = ChatHistoryRepository(db_session)
    _create_user(db_session, auth_sub=USER_A)
    _create_user(db_session, auth_sub=USER_B)
    ranked_high_session_id = f"{SESSION_PREFIX}ranked-high"
    ranked_low_session_id = f"{SESSION_PREFIX}ranked-low"
    ranked_hidden_session_id = f"{SESSION_PREFIX}ranked-hidden"

    repository.create_session(
        session_id=ranked_high_session_id,
        user_auth_sub=USER_A,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        title="Repair repair planning",
        created_at=_ts(11, 0),
    )
    repository.append_message(
        session_id=ranked_high_session_id,
        user_auth_sub=USER_A,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        role="user",
        content="repair evidence with more repair detail",
        turn_id="turn-rank-high-1",
        created_at=_ts(11, 5),
    )
    repository.create_session(
        session_id=ranked_low_session_id,
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
        title="Repair note",
        created_at=_ts(12, 0),
    )
    repository.append_message(
        session_id=ranked_low_session_id,
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
        role="assistant",
        content="single repair mention",
        turn_id="turn-rank-low-1",
        created_at=_ts(12, 5),
    )
    repository.create_session(
        session_id=ranked_hidden_session_id,
        user_auth_sub=USER_B,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        title="Repair hidden session",
        created_at=_ts(13, 0),
    )
    repository.append_message(
        session_id=ranked_hidden_session_id,
        user_auth_sub=USER_B,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        role="user",
        content="repair secret notes",
        turn_id="turn-rank-hidden-1",
        created_at=_ts(13, 5),
    )
    _refresh_session_search_vector(db_session, session_id=ranked_high_session_id)
    _refresh_session_search_vector(db_session, session_id=ranked_low_session_id)
    _refresh_session_search_vector(db_session, session_id=ranked_hidden_session_id)
    db_session.commit()

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="search_chat_history",
            tool_input={"query": "repair", "chat_kind": "all", "limit": 5},
            context=None,
            user_email=f"{USER_A}@example.org",
            user_auth_sub=USER_A,
            messages=[],
        )
    )

    assert result["success"] is True
    assert result["total_sessions"] == 2
    assert [session["session_id"] for session in result["sessions"]] == [
        ranked_high_session_id,
        ranked_low_session_id,
    ]


def test_get_chat_conversation_returns_full_transcript_across_pages(db_session):
    repository = ChatHistoryRepository(db_session)
    _create_user(db_session, auth_sub=USER_A)

    session_id = f"{SESSION_PREFIX}full-transcript"
    repository.create_session(
        session_id=session_id,
        user_auth_sub=USER_A,
        chat_kind=AGENT_STUDIO_CHAT_KIND,
        title="Long transcript session",
        created_at=_ts(14, 0),
    )
    for index in range(205):
        repository.append_message(
            session_id=session_id,
            user_auth_sub=USER_A,
            chat_kind=AGENT_STUDIO_CHAT_KIND,
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}",
            turn_id=f"turn-{index}",
            created_at=_ts(14, index // 60, index % 60),
        )
    db_session.commit()

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="get_chat_conversation",
            tool_input={"session_id": session_id},
            context=None,
            user_email=f"{USER_A}@example.org",
            user_auth_sub=USER_A,
            messages=[],
        )
    )

    assert result["success"] is True
    assert result["chat_kind"] == AGENT_STUDIO_CHAT_KIND
    assert result["message_count"] == 205
    assert len(result["messages"]) == 205
    assert result["messages"][0]["content"] == "message-0"
    assert result["messages"][-1]["content"] == "message-204"


def test_get_chat_conversation_denies_cross_user_lookup(db_session):
    repository = ChatHistoryRepository(db_session)
    _create_user(db_session, auth_sub=USER_A)
    _create_user(db_session, auth_sub=USER_B)

    session_id = f"{SESSION_PREFIX}private-session"
    repository.create_session(
        session_id=session_id,
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
        title="Private session",
        created_at=_ts(15, 0),
    )
    repository.append_message(
        session_id=session_id,
        user_auth_sub=USER_A,
        chat_kind=ASSISTANT_CHAT_KIND,
        role="user",
        content="top secret",
        turn_id="turn-private-1",
        created_at=_ts(15, 1),
    )
    db_session.commit()

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="get_chat_conversation",
            tool_input={"session_id": session_id},
            context=None,
            user_email=f"{USER_B}@example.org",
            user_auth_sub=USER_B,
            messages=[],
        )
    )

    assert result == {
        "success": False,
        "error": "Chat session not found.",
    }
