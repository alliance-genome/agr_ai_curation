"""Unit tests for /api/chat/stream endpoint lifecycle handling."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import ANY

from fastapi.responses import StreamingResponse
import pytest

from src.api import chat


async def _consume_stream(response: StreamingResponse) -> list[dict]:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)

    payloads = []
    for line in "".join(chunks).splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def test_chat_stream_endpoint_has_idempotent_cleanup_background_task(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()

    calls = {"register": [], "unregister": [], "clear": []}

    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])
    monkeypatch.setattr(chat, "conversation_manager", SimpleNamespace(add_exchange=lambda *_args, **_kwargs: None))

    async def _register_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        calls["register"].append((session_id, user_id, stream_token))
        return True

    async def _unregister_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        calls["unregister"].append((session_id, user_id, stream_token))

    async def _clear_cancel_signal(session_id: str):
        calls["clear"].append(session_id)

    async def _check_cancel_signal(_session_id: str) -> bool:
        return False

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-123"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    monkeypatch.setattr(chat, "register_active_stream", _register_active_stream)
    monkeypatch.setattr(chat, "unregister_active_stream", _unregister_active_stream)
    monkeypatch.setattr(chat, "clear_cancel_signal", _clear_cancel_signal)
    monkeypatch.setattr(chat, "check_cancel_signal", _check_cancel_signal)
    monkeypatch.setattr(chat, "run_agent_streamed", _run_agent_streamed)

    response = asyncio.run(
        chat.chat_stream_endpoint(
            chat_message=chat.ChatMessage(message="hello", session_id="session-chat-stream"),
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    assert isinstance(response, StreamingResponse)
    assert response.background is not None

    events = asyncio.run(_consume_stream(response))
    assert [event["type"] for event in events] == ["RUN_STARTED", "RUN_FINISHED"]

    # Explicitly invoke response background task to verify cleanup remains idempotent.
    asyncio.run(response.background())

    assert calls["register"] == [("session-chat-stream", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-chat-stream", "auth-sub", ANY)]
    assert calls["clear"] == ["session-chat-stream"]
    assert "session-chat-stream" not in chat._LOCAL_CANCEL_EVENTS
    assert "session-chat-stream" not in chat._LOCAL_SESSION_OWNERS


def test_chat_stream_endpoint_rejects_same_user_when_session_already_active(monkeypatch):
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()
    existing_event = asyncio.Event()
    chat._LOCAL_SESSION_OWNERS["session-active-same-user"] = "auth-sub"
    chat._LOCAL_CANCEL_EVENTS["session-active-same-user"] = existing_event

    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: None))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "_get_conversation_history_for_session", lambda _u, _s: [])

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.chat_stream_endpoint(
                chat_message=chat.ChatMessage(message="hello", session_id="session-active-same-user"),
                user={"sub": "auth-sub", "cognito:groups": []},
            )
        )

    assert exc.value.status_code == 409
    assert chat._LOCAL_SESSION_OWNERS["session-active-same-user"] == "auth-sub"
    assert chat._LOCAL_CANCEL_EVENTS["session-active-same-user"] is existing_event
