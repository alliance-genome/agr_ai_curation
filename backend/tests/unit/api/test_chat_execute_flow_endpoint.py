"""Unit tests for /api/chat/execute-flow endpoint streaming behavior."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import ANY
from uuid import uuid4

from fastapi.responses import StreamingResponse
import pytest

from src.api import chat


@pytest.fixture(autouse=True)
def _reset_stream_state():
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()
    yield
    chat._LOCAL_CANCEL_EVENTS.clear()
    chat._LOCAL_SESSION_OWNERS.clear()


class _DummyFlowQuery:
    def __init__(self, flow):
        self._flow = flow

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._flow


class _DummyDB:
    def __init__(self, flow):
        self._flow = flow
        self.commit_calls = 0
        self.rollback_calls = 0

    def query(self, _model):
        return _DummyFlowQuery(self._flow)

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1


async def _consume_stream(response: StreamingResponse) -> list[dict]:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)

    payloads = []
    for line in "".join(chunks).splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))
    return payloads


def _patch_stream_dependencies(monkeypatch, *, cancel_requested: bool):
    calls = {"register": [], "unregister": [], "clear": []}

    monkeypatch.setattr(
        chat,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)

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

    monkeypatch.setattr(chat, "register_active_stream", _register_active_stream)
    monkeypatch.setattr(chat, "unregister_active_stream", _unregister_active_stream)
    monkeypatch.setattr(chat, "clear_cancel_signal", _clear_cancel_signal)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: {"filename": "paper.pdf"}))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])

    async def _check_cancel_signal(_session_id: str) -> bool:
        return cancel_requested

    monkeypatch.setattr(chat, "check_cancel_signal", _check_cancel_signal)
    return calls


def test_execute_flow_endpoint_streams_flattened_events(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(flow_id=flow_id, session_id="session-flow-1")
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Flow A",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)

    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=False)

    async def _fake_execute_flow(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-123"}}
        yield {
            "type": "TEXT_MESSAGE_CONTENT",
            "data": {"delta": "hello"},
            "timestamp": "2026-02-26T00:00:00+00:00",
            "details": {"note": "ok"},
        }

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)

    response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    assert isinstance(response, StreamingResponse)
    assert response.background is not None
    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert db.commit_calls == 1
    assert flow.execution_count == 1
    assert events[0]["type"] == "RUN_STARTED"
    assert events[0]["trace_id"] == "trace-123"
    assert events[0]["session_id"] == "session-flow-1"
    assert events[0]["sessionId"] == "session-flow-1"
    assert events[1]["type"] == "TEXT_MESSAGE_CONTENT"
    assert events[1]["delta"] == "hello"
    assert events[1]["timestamp"] == "2026-02-26T00:00:00+00:00"
    assert events[1]["details"] == {"note": "ok"}
    assert "session-flow-1" not in chat._LOCAL_CANCEL_EVENTS
    assert calls["register"] == [("session-flow-1", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-flow-1", "auth-sub", ANY)]
    assert calls["clear"] == ["session-flow-1"]


def test_execute_flow_endpoint_cancel_stops_stream(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(flow_id=flow_id, session_id="session-flow-cancel")
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Flow Cancel",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)

    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=True)

    async def _fake_execute_flow(**_kwargs):
        yield {"type": "TEXT_MESSAGE_CONTENT", "data": {"delta": "should-not-stream"}}

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)

    response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))

    assert len(events) == 1
    assert events[0]["type"] == "RUN_ERROR"
    assert "cancelled by user" in events[0]["message"].lower()
    assert events[0]["session_id"] == "session-flow-cancel"
    assert "session-flow-cancel" not in chat._LOCAL_CANCEL_EVENTS
    assert calls["register"] == [("session-flow-cancel", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-flow-cancel", "auth-sub", ANY)]
    assert calls["clear"] == ["session-flow-cancel"]


def test_execute_flow_endpoint_preserves_event_order_and_domain_warning(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(flow_id=flow_id, session_id="session-flow-domain-warning")
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Flow Warning",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)

    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=False)

    async def _fake_execute_flow(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-domain"}}
        yield {
            "type": "DOMAIN_WARNING",
            "timestamp": "2026-02-26T00:00:00+00:00",
            "details": {
                "reason": "flow_step_unavailable",
                "message": "Step 2 unavailable",
                "step": 2,
            },
        }
        yield {"type": "TEXT_MESSAGE_CONTENT", "data": {"delta": "done"}}

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)

    response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    event_types = [event["type"] for event in events]
    assert "RUN_STARTED" in event_types
    assert "DOMAIN_WARNING" in event_types
    assert "TEXT_MESSAGE_CONTENT" in event_types
    assert event_types.index("RUN_STARTED") < event_types.index("DOMAIN_WARNING")
    assert event_types.index("DOMAIN_WARNING") < event_types.index("TEXT_MESSAGE_CONTENT")
    warning_event = next(event for event in events if event.get("type") == "DOMAIN_WARNING")
    assert warning_event["details"]["reason"] == "flow_step_unavailable"
    assert warning_event["details"]["step"] == 2
    assert warning_event["session_id"] == "session-flow-domain-warning"
    assert calls["register"] == [("session-flow-domain-warning", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-flow-domain-warning", "auth-sub", ANY)]
    assert calls["clear"] == ["session-flow-domain-warning"]


def test_execute_flow_endpoint_rejects_session_owned_by_different_user(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(flow_id=flow_id, session_id="session-owned-elsewhere")
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Flow Collision",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)

    monkeypatch.setattr(
        chat,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: {"filename": "paper.pdf"}))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])

    async def _deny_register(
        _session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        del stream_token
        return False

    monkeypatch.setattr(chat, "register_active_stream", _deny_register)

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.execute_flow_endpoint(
                request=request,
                db=db,
                user={"sub": "auth-sub", "cognito:groups": []},
            )
        )

    assert exc.value.status_code == 403
    assert db.commit_calls == 0
    assert flow.execution_count == 0


def test_execute_flow_endpoint_rejects_local_session_collision_before_register(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(flow_id=flow_id, session_id="session-preowned")
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Flow Collision",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)
    chat._LOCAL_SESSION_OWNERS["session-preowned"] = "different-user"

    register_calls = []

    monkeypatch.setattr(
        chat,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: {"filename": "paper.pdf"}))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])

    async def _register_active_stream(
        _session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        del stream_token
        register_calls.append(user_id)
        return True

    monkeypatch.setattr(chat, "register_active_stream", _register_active_stream)

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.execute_flow_endpoint(
                request=request,
                db=db,
                user={"sub": "auth-sub", "cognito:groups": []},
            )
        )

    assert exc.value.status_code == 403
    assert register_calls == []
    assert "session-preowned" not in chat._LOCAL_CANCEL_EVENTS


def test_execute_flow_endpoint_rejects_same_user_when_session_already_active(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(flow_id=flow_id, session_id="session-active-same-user")
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Flow Already Active",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)
    existing_event = asyncio.Event()
    chat._LOCAL_SESSION_OWNERS["session-active-same-user"] = "auth-sub"
    chat._LOCAL_CANCEL_EVENTS["session-active-same-user"] = existing_event

    monkeypatch.setattr(
        chat,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(chat, "document_state", SimpleNamespace(get_document=lambda _uid: {"filename": "paper.pdf"}))
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.execute_flow_endpoint(
                request=request,
                db=db,
                user={"sub": "auth-sub", "cognito:groups": []},
            )
        )

    assert exc.value.status_code == 409
    assert chat._LOCAL_SESSION_OWNERS["session-active-same-user"] == "auth-sub"
    assert chat._LOCAL_CANCEL_EVENTS["session-active-same-user"] is existing_event


def test_execute_flow_endpoint_streams_error_events_on_executor_exception(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(flow_id=flow_id, session_id="session-flow-error")
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Flow Error",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)
    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=False)

    async def _fake_execute_flow(**_kwargs):
        if False:
            yield {"type": "RUN_STARTED"}
        raise RuntimeError("executor boom")

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)

    response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    assert [event["type"] for event in events] == ["SUPERVISOR_ERROR", "RUN_ERROR"]
    assert events[1]["error_type"] == "RuntimeError"
    assert events[1]["session_id"] == "session-flow-error"
    assert calls["unregister"] == [("session-flow-error", "auth-sub", ANY)]
    assert calls["clear"] == ["session-flow-error"]


def test_execute_flow_endpoint_returns_404_when_flow_missing(monkeypatch):
    request = chat.ExecuteFlowRequest(flow_id=uuid4(), session_id="session-missing-flow")
    db = _DummyDB(flow=None)

    monkeypatch.setattr(
        chat,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7),
    )

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.execute_flow_endpoint(
                request=request,
                db=db,
                user={"sub": "auth-sub", "cognito:groups": []},
            )
        )

    assert exc.value.status_code == 404


def test_execute_flow_endpoint_returns_403_for_cross_user_flow(monkeypatch):
    flow = SimpleNamespace(
        id=uuid4(),
        user_id=1234,
        name="Other User Flow",
        execution_count=0,
        last_executed_at=None,
    )
    request = chat.ExecuteFlowRequest(flow_id=flow.id, session_id="session-cross-user-flow")
    db = _DummyDB(flow=flow)

    monkeypatch.setattr(
        chat,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7),
    )

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.execute_flow_endpoint(
                request=request,
                db=db,
                user={"sub": "auth-sub", "cognito:groups": []},
            )
        )

    assert exc.value.status_code == 403


def test_execute_flow_endpoint_requires_user_sub(monkeypatch):
    flow = SimpleNamespace(
        id=uuid4(),
        user_id=7,
        name="Valid Flow",
        execution_count=0,
        last_executed_at=None,
    )
    request = chat.ExecuteFlowRequest(flow_id=flow.id, session_id="session-no-user-sub")
    db = _DummyDB(flow=flow)

    monkeypatch.setattr(
        chat,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.execute_flow_endpoint(
                request=request,
                db=db,
                user={"cognito:groups": []},
            )
        )

    assert exc.value.status_code == 401


def test_execute_flow_endpoint_cleans_up_when_commit_fails(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(flow_id=flow_id, session_id="session-commit-failure")
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Flow Commit Failure",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)
    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=False)

    def _failing_commit():
        db.commit_calls += 1
        raise RuntimeError("db down")

    db.commit = _failing_commit

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.execute_flow_endpoint(
                request=request,
                db=db,
                user={"sub": "auth-sub", "cognito:groups": []},
            )
        )

    assert exc.value.status_code == 500
    assert "Failed to start flow execution" in str(exc.value.detail)
    assert db.commit_calls == 1
    assert db.rollback_calls == 1
    assert calls["register"] == [("session-commit-failure", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-commit-failure", "auth-sub", ANY)]
    assert calls["clear"] == ["session-commit-failure"]
    assert "session-commit-failure" not in chat._LOCAL_CANCEL_EVENTS
    assert "session-commit-failure" not in chat._LOCAL_SESSION_OWNERS
