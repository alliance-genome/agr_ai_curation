"""Unit tests for /api/chat/stop ownership enforcement."""

import asyncio

import pytest
from fastapi import HTTPException

from src.api import chat_stream as chat


def _reset_local_state():
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()


def test_stop_chat_rejects_cross_user_cancellation(monkeypatch):
    _reset_local_state()
    chat._LOCAL_SESSION_OWNERS["session-1"] = "owner-sub"

    async def _fake_is_stream_active(_session_id: str) -> bool:
        return True

    monkeypatch.setattr(chat, "is_stream_active", _fake_is_stream_active)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            chat.stop_chat(
                request=chat.StopRequest(session_id="session-1"),
                user={"sub": "intruder-sub"},
            )
        )

    assert exc.value.status_code == 403


def test_stop_chat_rejects_cross_user_cancellation_via_redis_owner(monkeypatch):
    _reset_local_state()

    async def _fake_get_stream_owner(_session_id: str):
        return "owner-sub"

    async def _fake_is_stream_active(_session_id: str) -> bool:
        return True

    monkeypatch.setattr(chat, "get_stream_owner", _fake_get_stream_owner)
    monkeypatch.setattr(chat, "is_stream_active", _fake_is_stream_active)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            chat.stop_chat(
                request=chat.StopRequest(session_id="session-redis"),
                user={"sub": "intruder-sub"},
            )
        )

    assert exc.value.status_code == 403


def test_stop_chat_allows_owner_and_sets_cancel(monkeypatch):
    _reset_local_state()
    session_id = "session-owner"
    chat._LOCAL_SESSION_OWNERS[session_id] = "owner-sub"
    local_event = asyncio.Event()
    chat._LOCAL_CANCEL_EVENTS[session_id] = local_event

    calls = {"set_cancel": 0}

    async def _fake_set_cancel_signal(_session_id: str):
        calls["set_cancel"] += 1
        return True

    async def _fake_is_stream_active(_session_id: str) -> bool:
        return True

    monkeypatch.setattr(chat, "set_cancel_signal", _fake_set_cancel_signal)
    monkeypatch.setattr(chat, "is_stream_active", _fake_is_stream_active)

    result = asyncio.run(
        chat.stop_chat(
            request=chat.StopRequest(session_id=session_id),
            user={"sub": "owner-sub"},
        )
    )

    assert result["status"] == "ok"
    assert "Cancellation requested" in result["message"]
    assert local_event.is_set() is True
    assert calls["set_cancel"] == 1


def test_stop_chat_rejects_when_owner_unknown_but_stream_active(monkeypatch):
    _reset_local_state()

    async def _fake_get_stream_owner(_session_id: str):
        return None

    async def _fake_is_stream_active(_session_id: str) -> bool:
        return True

    monkeypatch.setattr(chat, "get_stream_owner", _fake_get_stream_owner)
    monkeypatch.setattr(chat, "is_stream_active", _fake_is_stream_active)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            chat.stop_chat(
                request=chat.StopRequest(session_id="session-unknown-owner"),
                user={"sub": "owner-sub"},
            )
        )

    assert exc.value.status_code == 403


def test_stop_chat_returns_no_running_when_stream_inactive(monkeypatch):
    _reset_local_state()

    async def _fake_get_stream_owner(_session_id: str):
        return None

    async def _fake_is_stream_active(_session_id: str) -> bool:
        return False

    monkeypatch.setattr(chat, "get_stream_owner", _fake_get_stream_owner)
    monkeypatch.setattr(chat, "is_stream_active", _fake_is_stream_active)

    result = asyncio.run(
        chat.stop_chat(
            request=chat.StopRequest(session_id="session-none"),
            user={"sub": "owner-sub"},
        )
    )

    assert result["status"] == "ok"
    assert "No running chat" in result["message"]


def test_stop_chat_requires_user_identifier():
    _reset_local_state()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            chat.stop_chat(
                request=chat.StopRequest(session_id="session-no-user"),
                user={},
            )
        )

    assert exc.value.status_code == 401
