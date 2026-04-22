"""Unit tests for shared chat/flow stream lifecycle bookkeeping."""

import asyncio
import importlib
from unittest.mock import ANY

import pytest

chat = importlib.import_module("src.api.chat")


@pytest.fixture(autouse=True)
def _reset_stream_state():
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()
    yield
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()


def test_claim_active_stream_lifecycle_rolls_back_local_state_when_register_denied(monkeypatch):
    register_calls = []

    async def _register_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ) -> bool:
        register_calls.append((session_id, user_id, stream_token))
        return False

    monkeypatch.setattr(chat, "register_active_stream", _register_active_stream)

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat._claim_active_stream_lifecycle(
                session_id="session-register-denied",
                user_id="auth-sub",
            )
        )

    assert exc.value.status_code == 403
    assert register_calls == [("session-register-denied", "auth-sub", ANY)]
    assert "session-register-denied" not in chat._LOCAL_CANCEL_EVENTS
    assert "session-register-denied" not in chat._LOCAL_SESSION_OWNERS


def test_active_stream_lifecycle_cleanup_is_idempotent_and_finalize_backfills_latest_title(monkeypatch):
    calls = {"register": [], "unregister": [], "clear": [], "backfill": []}

    async def _register_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ) -> bool:
        calls["register"].append((session_id, user_id, stream_token))
        return True

    async def _unregister_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ) -> None:
        calls["unregister"].append((session_id, user_id, stream_token))

    async def _clear_cancel_signal(session_id: str) -> None:
        calls["clear"].append(session_id)

    monkeypatch.setattr(chat, "register_active_stream", _register_active_stream)
    monkeypatch.setattr(chat, "unregister_active_stream", _unregister_active_stream)
    monkeypatch.setattr(chat, "clear_cancel_signal", _clear_cancel_signal)
    monkeypatch.setattr(
        chat,
        "_backfill_chat_session_generated_title",
        lambda session_id, user_id, title: calls["backfill"].append((session_id, user_id, title)),
    )

    lifecycle = asyncio.run(
        chat._claim_active_stream_lifecycle(
            session_id="session-shared-cleanup",
            user_id="auth-sub",
        )
    )
    title_state = {"value": "initial title"}

    assert chat._LOCAL_SESSION_OWNERS["session-shared-cleanup"] == "auth-sub"
    assert chat._LOCAL_CANCEL_EVENTS["session-shared-cleanup"] is lifecycle.cancel_event

    asyncio.run(lifecycle.cleanup())
    title_state["value"] = "final title"
    asyncio.run(lifecycle.background_task(lambda: title_state["value"])())

    assert calls["register"] == [("session-shared-cleanup", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-shared-cleanup", "auth-sub", ANY)]
    assert calls["clear"] == ["session-shared-cleanup"]
    assert calls["backfill"] == [("session-shared-cleanup", "auth-sub", "final title")]
    assert "session-shared-cleanup" not in chat._LOCAL_CANCEL_EVENTS
    assert "session-shared-cleanup" not in chat._LOCAL_SESSION_OWNERS
