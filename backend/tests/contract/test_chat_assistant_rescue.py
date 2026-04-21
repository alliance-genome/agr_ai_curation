"""Live contract tests for POST /api/chat/{session_id}/assistant-rescue."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from src.models.sql.chat_message import ChatMessage as ChatMessageModel


def test_assistant_rescue_requires_authentication(contract_client):
    response = contract_client.post(
        "/api/chat/fake-session/assistant-rescue",
        json={"turn_id": "turn-1", "content": "rescued"},
    )

    assert response.status_code == 401
    assert "detail" in response.json()


def test_assistant_rescue_returns_404_for_other_users_session(
    contract_client,
    chat_contract_auth_headers,
    seed_chat_contract_session,
):
    session_id = f"contract-rescue-hidden-{uuid4().hex[:8]}"
    seed_chat_contract_session(
        session_id=session_id,
        user_auth_sub="contract-chat-other-user",
        title="Hidden rescue session",
        messages=[
            {
                "role": "user",
                "content": "Hidden prompt",
                "turn_id": "turn-hidden-1",
            }
        ],
    )

    response = contract_client.post(
        f"/api/chat/{session_id}/assistant-rescue",
        headers=chat_contract_auth_headers,
        json={
            "turn_id": "turn-hidden-1",
            "content": "rescued hidden reply",
            "trace_id": "trace-hidden-1",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Chat session not found"


def test_assistant_rescue_returns_409_when_user_turn_is_missing(
    contract_client,
    chat_contract_auth_headers,
    seed_chat_contract_session,
):
    session_id = f"contract-rescue-missing-{uuid4().hex[:8]}"
    seed_chat_contract_session(
        session_id=session_id,
        title="Missing user turn session",
    )

    response = contract_client.post(
        f"/api/chat/{session_id}/assistant-rescue",
        headers=chat_contract_auth_headers,
        json={
            "turn_id": "turn-missing-1",
            "content": "rescued missing reply",
            "trace_id": "trace-missing-1",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Chat user turn not found"


def test_assistant_rescue_creates_missing_assistant_row_and_is_idempotent(
    contract_client,
    chat_contract_auth_headers,
    chat_contract_db,
    seed_chat_contract_session,
):
    session_id = f"contract-rescue-owned-{uuid4().hex[:8]}"
    turn_id = f"turn-owned-{uuid4().hex[:8]}"
    seed_chat_contract_session(
        session_id=session_id,
        title="Owned rescue session",
        messages=[
            {
                "role": "user",
                "content": "Owned prompt",
                "turn_id": turn_id,
            }
        ],
    )

    first_response = contract_client.post(
        f"/api/chat/{session_id}/assistant-rescue",
        headers=chat_contract_auth_headers,
        json={
            "turn_id": turn_id,
            "content": "rescued durable reply",
            "trace_id": "trace-owned-1",
        },
    )

    assert first_response.status_code == 200, first_response.text
    first_payload = first_response.json()
    assert first_payload == {
        "session_id": session_id,
        "turn_id": turn_id,
        "created": True,
        "trace_id": "trace-owned-1",
    }

    second_response = contract_client.post(
        f"/api/chat/{session_id}/assistant-rescue",
        headers=chat_contract_auth_headers,
        json={
            "turn_id": turn_id,
            "content": "rescued durable reply",
            "trace_id": "trace-owned-1",
        },
    )

    assert second_response.status_code == 200, second_response.text
    second_payload = second_response.json()
    assert second_payload == {
        "session_id": session_id,
        "turn_id": turn_id,
        "created": False,
        "trace_id": "trace-owned-1",
    }

    chat_contract_db.expire_all()
    assistant_rows = chat_contract_db.scalars(
        select(ChatMessageModel)
        .where(
            ChatMessageModel.session_id == session_id,
            ChatMessageModel.role == "assistant",
            ChatMessageModel.turn_id == turn_id,
        )
        .order_by(ChatMessageModel.created_at.asc(), ChatMessageModel.message_id.asc())
    ).all()

    assert len(assistant_rows) == 1
    assert assistant_rows[0].content == "rescued durable reply"
    assert assistant_rows[0].trace_id == "trace-owned-1"
