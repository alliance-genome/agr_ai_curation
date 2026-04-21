"""Live contract tests for durable chat history endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4


def _ts(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 4, 19, hour, minute, second, tzinfo=timezone.utc)


def _parse_iso8601(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_history_list_requires_authentication(contract_client):
    response = contract_client.get("/api/chat/history")

    assert response.status_code == 401
    assert "detail" in response.json()


def test_history_list_returns_live_summary_schema_and_filters_by_user(
    contract_client,
    chat_contract_auth_headers,
    seed_chat_contract_session,
):
    query = f"contract-history-{uuid4().hex[:8]}"
    visible_session_id = f"{query}-visible"
    hidden_session_id = f"{query}-hidden"

    seed_chat_contract_session(
        session_id=visible_session_id,
        title=f"{query} visible",
        created_at=_ts(9, 0),
        messages=[
            {
                "role": "user",
                "content": "Visible history request",
                "turn_id": "turn-visible-1",
                "created_at": _ts(9, 1),
            },
            {
                "role": "assistant",
                "content": "Visible history response",
                "turn_id": "turn-visible-1",
                "trace_id": "trace-visible-1",
                "created_at": _ts(9, 2),
            },
        ],
    )
    seed_chat_contract_session(
        session_id=hidden_session_id,
        user_auth_sub="contract-chat-other-user",
        title=f"{query} hidden",
        created_at=_ts(10, 0),
        messages=[
            {
                "role": "user",
                "content": "Hidden history request",
                "turn_id": "turn-hidden-1",
                "created_at": _ts(10, 1),
            }
        ],
    )

    response = contract_client.get(
        "/api/chat/history",
        headers=chat_contract_auth_headers,
        params={"limit": 5, "query": query},
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["total_sessions"] == 1
    assert payload["limit"] == 5
    assert payload["query"] == query
    assert payload["document_id"] is None
    assert payload["next_cursor"] is None
    assert [session["session_id"] for session in payload["sessions"]] == [visible_session_id]

    summary = payload["sessions"][0]
    assert set(summary) >= {
        "session_id",
        "title",
        "active_document_id",
        "created_at",
        "updated_at",
        "last_message_at",
        "recent_activity_at",
    }
    assert summary["title"] == f"{query} visible"
    assert summary["active_document_id"] is None
    assert _parse_iso8601(summary["created_at"]) == _ts(9, 0)
    assert _parse_iso8601(summary["updated_at"]) is not None
    assert _parse_iso8601(summary["last_message_at"]) == _ts(9, 2)
    assert _parse_iso8601(summary["recent_activity_at"]) == _ts(9, 2)


def test_history_detail_requires_authentication(contract_client):
    response = contract_client.get("/api/chat/history/fake-session-id")

    assert response.status_code == 401
    assert "detail" in response.json()


def test_history_detail_returns_transcript_schema_and_message_cursor(
    contract_client,
    chat_contract_auth_headers,
    seed_chat_contract_session,
):
    session_id = f"contract-history-detail-{uuid4().hex[:8]}"
    seed_chat_contract_session(
        session_id=session_id,
        title="Detail session",
        created_at=_ts(11, 0),
        messages=[
            {
                "role": "user",
                "content": "First durable question",
                "turn_id": "turn-detail-1",
                "created_at": _ts(11, 1),
            },
            {
                "role": "assistant",
                "content": "First durable answer",
                "turn_id": "turn-detail-1",
                "payload_json": {"source": "contract"},
                "trace_id": "trace-detail-1",
                "created_at": _ts(11, 2),
            },
        ],
    )

    first_page = contract_client.get(
        f"/api/chat/history/{session_id}",
        headers=chat_contract_auth_headers,
        params={"message_limit": 1},
    )

    assert first_page.status_code == 200, first_page.text
    first_payload = first_page.json()

    assert set(first_payload) >= {
        "session",
        "active_document",
        "messages",
        "message_limit",
        "next_message_cursor",
    }
    assert first_payload["message_limit"] == 1
    assert first_payload["active_document"] is None
    assert first_payload["next_message_cursor"] is not None

    session = first_payload["session"]
    assert set(session) >= {
        "session_id",
        "title",
        "active_document_id",
        "created_at",
        "updated_at",
        "last_message_at",
        "recent_activity_at",
    }
    assert session["session_id"] == session_id
    assert session["title"] == "Detail session"

    first_message = first_payload["messages"][0]
    assert set(first_message) >= {
        "message_id",
        "session_id",
        "turn_id",
        "role",
        "message_type",
        "content",
        "payload_json",
        "trace_id",
        "created_at",
    }
    UUID(first_message["message_id"])
    assert first_message["session_id"] == session_id
    assert first_message["turn_id"] == "turn-detail-1"
    assert first_message["role"] == "user"
    assert first_message["message_type"] == "text"
    assert first_message["content"] == "First durable question"
    assert first_message["payload_json"] is None
    assert first_message["trace_id"] is None
    assert _parse_iso8601(first_message["created_at"]) == _ts(11, 1)

    second_page = contract_client.get(
        f"/api/chat/history/{session_id}",
        headers=chat_contract_auth_headers,
        params={
            "message_limit": 1,
            "message_cursor": first_payload["next_message_cursor"],
        },
    )

    assert second_page.status_code == 200, second_page.text
    second_payload = second_page.json()
    assert second_payload["next_message_cursor"] is None

    second_message = second_payload["messages"][0]
    UUID(second_message["message_id"])
    assert second_message["session_id"] == session_id
    assert second_message["turn_id"] == "turn-detail-1"
    assert second_message["role"] == "assistant"
    assert second_message["message_type"] == "text"
    assert second_message["content"] == "First durable answer"
    assert second_message["payload_json"] == {"source": "contract"}
    assert second_message["trace_id"] == "trace-detail-1"
    assert _parse_iso8601(second_message["created_at"]) == _ts(11, 2)


def test_history_detail_returns_404_for_other_users_session(
    contract_client,
    chat_contract_auth_headers,
    seed_chat_contract_session,
):
    hidden_session_id = f"contract-history-hidden-{uuid4().hex[:8]}"
    seed_chat_contract_session(
        session_id=hidden_session_id,
        user_auth_sub="contract-chat-other-user",
        title="Other user's session",
        created_at=_ts(12, 0),
        messages=[
            {
                "role": "user",
                "content": "Private prompt",
                "turn_id": "turn-hidden",
                "created_at": _ts(12, 1),
            }
        ],
    )

    response = contract_client.get(
        f"/api/chat/history/{hidden_session_id}",
        headers=chat_contract_auth_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Chat session not found"
