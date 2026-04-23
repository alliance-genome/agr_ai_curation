"""Live contract tests for POST /api/chat/session."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.sql.chat_session import ChatSession as ChatSessionModel


def _parse_iso8601(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_create_session_requires_authentication(contract_client):
    response = contract_client.post(
        "/api/chat/session",
        json={"chat_kind": "assistant_chat"},
    )

    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.parametrize("chat_kind", ["assistant_chat", "agent_studio"])
def test_create_session_returns_durable_schema_and_persists_session(
    contract_client,
    chat_contract_auth_headers,
    chat_contract_db,
    chat_kind,
):
    response = contract_client.post(
        "/api/chat/session",
        headers=chat_contract_auth_headers,
        json={"chat_kind": chat_kind},
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    assert set(payload) >= {
        "session_id",
        "created_at",
        "updated_at",
        "title",
        "active_document_id",
        "active_document",
    }
    UUID(payload["session_id"])
    created_at = _parse_iso8601(payload["created_at"])
    updated_at = _parse_iso8601(payload["updated_at"])
    assert updated_at >= created_at
    assert payload["title"] is None
    assert payload["active_document_id"] is None
    assert payload["active_document"] is None

    chat_contract_db.expire_all()
    row = chat_contract_db.scalar(
        select(ChatSessionModel).where(
            ChatSessionModel.session_id == payload["session_id"]
        )
    )
    assert row is not None
    assert row.user_auth_sub == "api-key-test-user"
    assert row.chat_kind == chat_kind
    assert row.deleted_at is None
    assert row.last_message_at is None
