"""Tests for durable chat history SQLAlchemy models."""

from src.models.sql.chat_message import ChatMessage
from src.models.sql.chat_session import ChatSession


def _index_names(model) -> set[str]:
    return {index.name for index in model.__table__.indexes}


def _constraint_names(model) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if constraint.name
    }


def test_chat_session_uses_auth_sub_ownership_and_expected_indexes():
    session = ChatSession(
        session_id="session-123",
        user_auth_sub="auth0|user-123",
        chat_kind="assistant_chat",
    )

    assert "session_id" in ChatSession.__table__.c
    assert "user_auth_sub" in ChatSession.__table__.c
    assert "chat_kind" in ChatSession.__table__.c
    assert "user_id" not in ChatSession.__table__.c
    assert "search_vector" in ChatSession.__table__.c
    assert "deleted_at" in ChatSession.__table__.c
    assert "generated_title" in ChatSession.__table__.c
    assert session.chat_kind == "assistant_chat"
    assert session.title is None

    assert _index_names(ChatSession) == {
        "ix_chat_sessions_user_auth_sub",
        "ix_chat_sessions_recent_activity",
        "ix_chat_sessions_active_document_id",
        "ix_chat_sessions_search_vector_assistant_chat",
        "ix_chat_sessions_search_vector_agent_studio",
    }
    assert _constraint_names(ChatSession) >= {
        "ck_chat_sessions_session_id_not_empty",
        "ck_chat_sessions_user_auth_sub_not_empty",
        "ck_chat_sessions_chat_kind",
        "ck_chat_sessions_title_not_empty",
        "ck_chat_sessions_generated_title_not_empty",
    }

    foreign_keys = list(ChatSession.__table__.c["active_document_id"].foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "pdf_documents.id"


def test_chat_message_supports_turn_ids_search_payloads_and_flow_role():
    message = ChatMessage(
        session_id="session-123",
        chat_kind="assistant_chat",
        turn_id="turn-123",
        role="flow",
        message_type="flow_step_evidence",
        content="Flow step evidence summary",
        payload_json={"tool_name": "record_evidence"},
    )

    assert "message_id" in ChatMessage.__table__.c
    assert "turn_id" in ChatMessage.__table__.c
    assert "chat_kind" in ChatMessage.__table__.c
    assert "payload_json" in ChatMessage.__table__.c
    assert "search_vector" in ChatMessage.__table__.c
    assert message.chat_kind == "assistant_chat"
    assert message.role == "flow"
    assert message.payload_json == {"tool_name": "record_evidence"}
    assert str(ChatMessage.__table__.c["message_type"].server_default.arg) == "text"

    assert _index_names(ChatMessage) == {
        "ix_chat_messages_session_timeline",
        "ix_chat_messages_turn_lookup",
        "uq_chat_messages_user_turn",
        "uq_chat_messages_assistant_turn",
        "ix_chat_messages_search_vector_assistant_chat",
        "ix_chat_messages_search_vector_agent_studio",
    }
    assert _constraint_names(ChatMessage) >= {
        "ck_chat_messages_role",
        "ck_chat_messages_session_id_not_empty",
        "ck_chat_messages_chat_kind",
        "ck_chat_messages_turn_id_not_empty",
        "ck_chat_messages_message_type_not_empty",
        "ck_chat_messages_content_not_empty",
    }

    foreign_keys = list(ChatMessage.__table__.c["session_id"].foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "chat_sessions.session_id"
