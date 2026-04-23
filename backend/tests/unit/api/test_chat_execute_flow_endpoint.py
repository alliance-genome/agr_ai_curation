"""Unit tests for /api/chat/execute-flow endpoint streaming behavior."""

import asyncio
from datetime import datetime, timedelta, timezone
import importlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import ANY
from uuid import uuid4

from fastapi.responses import StreamingResponse
import pytest

chat = importlib.import_module("src.api.chat")


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


class _DummyCompletionDB:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0
        self.closed = False

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.closed = True


class _FakeChatHistoryRepository:
    def __init__(self) -> None:
        self.sessions: dict[tuple[str, str], chat.ChatSessionRecord] = {}
        self.messages: dict[tuple[str, str], list[chat.ChatMessageRecord]] = {}
        self._counter = 0

    def get_or_create_session(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        title: str | None = None,
        generated_title: str | None = None,
        active_document_id=None,
        created_at: datetime | None = None,
    ) -> chat.ChatSessionRecord:
        key = (user_auth_sub, session_id)
        existing = self.sessions.get(key)
        if existing is not None and existing.chat_kind == chat_kind:
            return existing
        created = created_at or datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc)
        record = chat.ChatSessionRecord(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=chat_kind,
            title=title,
            generated_title=generated_title,
            active_document_id=active_document_id,
            created_at=created,
            updated_at=created,
            last_message_at=None,
            deleted_at=None,
        )
        self.sessions[key] = record
        return record

    def get_session(self, *, session_id: str, user_auth_sub: str):
        return self.sessions.get((user_auth_sub, session_id))

    def append_message(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        role: str,
        content: str,
        message_type: str = "text",
        turn_id: str | None = None,
        payload_json=None,
        trace_id: str | None = None,
        created_at: datetime | None = None,
    ):
        session = self.get_or_create_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=chat_kind,
        )
        if turn_id is not None and role in {"user", "assistant"}:
            existing = self.get_message_by_turn_id(
                session_id=session_id,
                user_auth_sub=user_auth_sub,
                turn_id=turn_id,
                role=role,
            )
            if existing is not None:
                return SimpleNamespace(message=existing, created=False)

        self._counter += 1
        message_created_at = created_at or datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc) + timedelta(seconds=self._counter)
        record = chat.ChatMessageRecord(
            message_id=uuid4(),
            session_id=session_id,
            chat_kind=chat_kind,
            turn_id=turn_id,
            role=role,
            message_type=message_type,
            content=content,
            payload_json=payload_json,
            trace_id=trace_id,
            created_at=message_created_at,
        )
        key = (user_auth_sub, session_id)
        self.messages.setdefault(key, []).append(record)
        self.sessions[key] = chat.ChatSessionRecord(
            session_id=session.session_id,
            user_auth_sub=session.user_auth_sub,
            chat_kind=session.chat_kind,
            title=session.title,
            generated_title=session.generated_title,
            active_document_id=session.active_document_id,
            created_at=session.created_at,
            updated_at=message_created_at,
            last_message_at=message_created_at,
            deleted_at=session.deleted_at,
        )
        return SimpleNamespace(message=record, created=True)

    def set_generated_title(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        generated_title: str,
    ):
        key = (user_auth_sub, session_id)
        session = self.sessions.get(key)
        if session is None or session.chat_kind != chat_kind:
            return None
        if session.title is not None or session.generated_title is not None:
            return session
        updated = chat.ChatSessionRecord(
            session_id=session.session_id,
            user_auth_sub=session.user_auth_sub,
            chat_kind=session.chat_kind,
            title=session.title,
            generated_title=generated_title,
            active_document_id=session.active_document_id,
            created_at=session.created_at,
            updated_at=session.updated_at,
            last_message_at=session.last_message_at,
            deleted_at=session.deleted_at,
        )
        self.sessions[key] = updated
        return updated

    def get_message_by_turn_id(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        turn_id: str,
        role: str,
    ):
        for message in self.messages.get((user_auth_sub, session_id), []):
            if message.turn_id == turn_id and message.role == role:
                return message
        return None

    def list_messages_for_turn(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        turn_id: str,
    ) -> list[chat.ChatMessageRecord]:
        return [
            message
            for message in self.messages.get((user_auth_sub, session_id), [])
            if message.turn_id == turn_id and message.chat_kind == chat_kind
        ]

    def update_message_by_turn_id(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        turn_id: str,
        role: str,
        payload_json=None,
        trace_id=None,
    ):
        key = (user_auth_sub, session_id)
        for index, message in enumerate(self.messages.get(key, [])):
            if message.turn_id != turn_id or message.role != role:
                continue
            updated = chat.ChatMessageRecord(
                message_id=message.message_id,
                session_id=message.session_id,
                chat_kind=message.chat_kind,
                turn_id=message.turn_id,
                role=message.role,
                message_type=message.message_type,
                content=message.content,
                payload_json=payload_json if payload_json is not None else message.payload_json,
                trace_id=trace_id if trace_id is not None else message.trace_id,
                created_at=message.created_at,
            )
            self.messages[key][index] = updated
            return updated
        raise LookupError("Chat message not found")

    def list_messages(
        self,
        *,
        session_id: str,
        user_auth_sub: str,
        chat_kind: str,
        limit: int = 200,
        cursor=None,
    ):
        del cursor
        messages = [
            message
            for message in self.messages.get((user_auth_sub, session_id), [])
            if message.chat_kind == chat_kind
        ]
        return SimpleNamespace(items=messages[:limit], next_cursor=None)


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
    repository = _FakeChatHistoryRepository()
    completion_db = _DummyCompletionDB()

    monkeypatch.setattr(
        chat,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(chat, "_resolve_session_create_active_document", lambda **_kwargs: (None, None))

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
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)
    monkeypatch.setattr(chat, "SessionLocal", lambda: completion_db)

    async def _check_cancel_signal(_session_id: str) -> bool:
        return cancel_requested

    monkeypatch.setattr(chat, "check_cancel_signal", _check_cancel_signal)
    calls["repository"] = repository
    calls["completion_db"] = completion_db
    return calls


def _patch_durable_history(monkeypatch):
    repository = _FakeChatHistoryRepository()
    completion_db = _DummyCompletionDB()
    monkeypatch.setattr(chat, "_get_chat_history_repository", lambda _db: repository)
    monkeypatch.setattr(chat, "SessionLocal", lambda: completion_db)
    return repository, completion_db


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
    assert events[1]["type"] == "TEXT_MESSAGE_CONTENT"
    assert events[1]["delta"] == "hello"
    assert events[1]["timestamp"] == "2026-02-26T00:00:00+00:00"
    assert events[1]["details"] == {"note": "ok"}
    assert "session-flow-1" not in chat._LOCAL_CANCEL_EVENTS
    assert calls["register"] == [("session-flow-1", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-flow-1", "auth-sub", ANY)]
    assert calls["clear"] == ["session-flow-1"]


def test_execute_flow_endpoint_background_backfill_uses_final_assistant_aware_title(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(
        flow_id=flow_id,
        session_id="session-flow-title",
        user_query="Summarize TP53 evidence",
    )
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Flow Title",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)
    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=False)
    captured_backfill_calls = []

    monkeypatch.setattr(
        chat,
        "_generate_title_from_turn",
        lambda *, user_message, assistant_message=None: (
            "assistant-aware-flow-title" if assistant_message else "user-only-flow-title"
        ),
    )
    monkeypatch.setattr(
        chat,
        "_backfill_chat_session_generated_title",
        lambda session_id, user_id, preferred_generated_title=None: captured_backfill_calls.append(
            (session_id, user_id, preferred_generated_title)
        ),
    )

    async def _fake_execute_flow(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-flow-title"}}
        yield {
            "type": "CHAT_OUTPUT_READY",
            "details": {"output": "Assistant flow answer"},
        }
        yield {
            "type": "FLOW_FINISHED",
            "data": {"status": "completed", "failure_reason": None},
        }

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)

    response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == [
        "RUN_STARTED",
        "CHAT_OUTPUT_READY",
        "FLOW_FINISHED",
    ]
    assert captured_backfill_calls == [
        ("session-flow-title", "auth-sub", "assistant-aware-flow-title")
    ]
    assert calls["register"] == [("session-flow-title", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-flow-title", "auth-sub", ANY)]
    assert calls["clear"] == ["session-flow-title"]


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


def test_execute_flow_endpoint_preserves_flow_step_evidence_payload(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(flow_id=flow_id, session_id="session-flow-evidence")
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Flow Evidence",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)

    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=False)

    async def _fake_execute_flow(**_kwargs):
        yield {
            "type": "FLOW_STEP_EVIDENCE",
            "timestamp": "2026-02-26T00:00:01+00:00",
            "details": {
                "flow_id": str(flow_id),
                "flow_name": "Flow Evidence",
                "flow_run_id": "flow-run-123",
                "step": 2,
                "tool_name": "ask_gene_specialist",
                "agent_id": "gene",
                "agent_name": "Gene Agent",
                "evidence_preview": [
                    {
                        "entity": "TP53",
                        "verified_quote": "TP53 increased.",
                        "page": 2,
                        "section": "Results",
                        "chunk_id": "chunk-1",
                    }
                ],
                "evidence_records": [
                    {
                        "entity": "TP53",
                        "verified_quote": "TP53 increased.",
                        "page": 2,
                        "section": "Results",
                        "chunk_id": "chunk-1",
                    }
                ],
                "evidence_count": 1,
                "total_evidence_records": 3,
            },
        }

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
    flow_step_event = events[0]
    assert flow_step_event["type"] == "FLOW_STEP_EVIDENCE"
    assert flow_step_event["flow_run_id"] == "flow-run-123"
    assert flow_step_event["step"] == 2
    assert flow_step_event["tool_name"] == "ask_gene_specialist"
    assert flow_step_event["evidence_count"] == 1
    assert flow_step_event["total_evidence_records"] == 3
    assert flow_step_event["evidence_preview"][0]["entity"] == "TP53"
    assert flow_step_event["evidence_records"][0]["entity"] == "TP53"
    assert flow_step_event["session_id"] == "session-flow-evidence"
    assert flow_step_event["details"]["agent_name"] == "Gene Agent"
    assert calls["register"] == [("session-flow-evidence", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-flow-evidence", "auth-sub", ANY)]
    assert calls["clear"] == ["session-flow-evidence"]


def test_execute_flow_endpoint_injects_flow_context_without_leaking_internal_payload(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(
        flow_id=flow_id,
        session_id="session-flow-context",
        user_query="Run gene selection flow",
    )
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Gene Selection Flow",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)

    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=False)

    async def _fake_execute_flow(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-flow-context"}}
        yield {
            "type": "CREW_START",
            "timestamp": "2026-02-26T00:00:00+00:00",
            "details": {"crewName": "Gene Agent", "crewDisplayName": "Gene Agent", "agents": ["Gene Agent"]},
        }
        yield {
            "type": "TOOL_COMPLETE",
            "timestamp": "2026-02-26T00:00:01+00:00",
            "details": {"toolName": "ask_gene_specialist", "friendlyName": "Gene Agent complete", "success": True},
            "internal": {"tool_output": "{\"selected_gene\":\"TP53\"}", "output_length": 24},
        }
        yield {
            "type": "SPECIALIST_SUMMARY",
            "timestamp": "2026-02-26T00:00:02+00:00",
            "details": {"specialist": "Gene Agent", "toolCallCount": 2},
        }
        yield {
            "type": "CHAT_OUTPUT_READY",
            "timestamp": "2026-02-26T00:00:03+00:00",
            "details": {"output": "Selected TP53 for highest evidence confidence."},
        }
        yield {
            "type": "FLOW_FINISHED",
            "data": {"status": "completed", "failure_reason": None},
        }

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)

    response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))

    tool_complete_event = next(event for event in events if event.get("type") == "TOOL_COMPLETE")
    assert "internal" not in tool_complete_event

    stored_turn_messages = list(
        calls["repository"].messages[("auth-sub", "session-flow-context")]
    )
    assert [message.role for message in stored_turn_messages] == ["user", "flow"]
    history_assistant_msg = stored_turn_messages[1].payload_json[
        chat.FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY
    ]
    assert "Flow execution summary for follow-up questions" in history_assistant_msg
    assert "<FLOW_INTERNAL_CONTEXT_JSON>" in history_assistant_msg
    assert "ask_gene_specialist" in history_assistant_msg
    assert "TP53" in history_assistant_msg


def test_execute_flow_endpoint_replays_completed_turn_without_rerunning(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(
        flow_id=flow_id,
        session_id="session-flow-replay",
        turn_id="turn-flow-replay",
        user_query="Run gene selection flow",
    )
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Replayable Flow",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)

    _patch_stream_dependencies(monkeypatch, cancel_requested=False)
    repository, _completion_db = _patch_durable_history(monkeypatch)

    execute_calls = []

    async def _fake_execute_flow(**_kwargs):
        execute_calls.append(_kwargs["session_id"])
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-flow-replay"}}
        yield {
            "type": "CHAT_OUTPUT_READY",
            "timestamp": "2026-02-26T00:00:03+00:00",
            "details": {"output": "Selected TP53 for highest evidence confidence."},
        }
        yield {
            "type": "FLOW_FINISHED",
            "timestamp": "2026-02-26T00:00:04+00:00",
            "data": {
                "flow_id": str(flow_id),
                "flow_name": "Replayable Flow",
                "flow_run_id": "flow-run-replay",
                "document_id": None,
                "origin_session_id": "session-flow-replay",
                "status": "completed",
                "failure_reason": None,
                "total_evidence_records": 0,
                "step_evidence_counts": {},
                "adapter_keys": [],
            },
        }

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)

    first_response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    first_events = asyncio.run(_consume_stream(first_response))
    asyncio.run(first_response.background())

    second_response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    second_events = asyncio.run(_consume_stream(second_response))
    asyncio.run(second_response.background())

    assert execute_calls == ["session-flow-replay"]
    assert flow.execution_count == 1
    assert db.commit_calls == 1
    assert [event["type"] for event in first_events] == ["RUN_STARTED", "CHAT_OUTPUT_READY", "FLOW_FINISHED"]
    assert [event["type"] for event in second_events] == ["RUN_STARTED", "CHAT_OUTPUT_READY", "FLOW_FINISHED"]
    assert {event["turn_id"] for event in second_events} == {"turn-flow-replay"}
    assert second_events[0]["trace_id"] == "trace-flow-replay"
    assert second_events[1]["details"]["output"] == "Selected TP53 for highest evidence confidence."
    stored_turn_messages = repository.list_messages_for_turn(
        session_id="session-flow-replay",
        user_auth_sub="auth-sub",
        chat_kind=chat.ASSISTANT_CHAT_KIND,
        turn_id="turn-flow-replay",
    )
    assert [message.role for message in stored_turn_messages] == ["user", "flow"]
    assert stored_turn_messages[1].message_type == chat.FLOW_SUMMARY_MESSAGE_TYPE
    assert stored_turn_messages[1].payload_json[chat.FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY].startswith(
        "Flow execution summary for follow-up questions"
    )


def test_execute_flow_endpoint_retries_incomplete_turn_without_reincrementing_counter(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(
        flow_id=flow_id,
        session_id="session-flow-retry",
        turn_id="turn-flow-retry",
    )
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Retry Flow",
        execution_count=1,
        last_executed_at=datetime(2026, 2, 26, 0, 0, tzinfo=timezone.utc),
    )
    db = _DummyDB(flow=flow)

    _patch_stream_dependencies(monkeypatch, cancel_requested=False)
    repository, _completion_db = _patch_durable_history(monkeypatch)
    repository.get_or_create_session(
        session_id="session-flow-retry",
        user_auth_sub="auth-sub",
        chat_kind=chat.ASSISTANT_CHAT_KIND,
    )
    repository.append_message(
        session_id="session-flow-retry",
        user_auth_sub="auth-sub",
        chat_kind=chat.ASSISTANT_CHAT_KIND,
        role="user",
        content="Run flow 'Retry Flow'",
        turn_id="turn-flow-retry",
        payload_json=chat._build_execute_flow_runtime_payload(
            None,
            flow_run_id="flow-run-retry",
            trace_id=None,
        ),
        created_at=datetime(2026, 2, 26, 0, 0, tzinfo=timezone.utc),
    )

    execute_calls = []

    async def _fake_execute_flow(**_kwargs):
        execute_calls.append(_kwargs)
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-flow-retry"}}
        yield {
            "type": "CHAT_OUTPUT_READY",
            "timestamp": "2026-02-26T00:01:03+00:00",
            "details": {"output": "Retried flow output."},
        }
        yield {
            "type": "FLOW_FINISHED",
            "timestamp": "2026-02-26T00:01:04+00:00",
            "data": {
                "flow_id": str(flow_id),
                "flow_name": "Retry Flow",
                "flow_run_id": "flow-run-retry",
                "document_id": None,
                "origin_session_id": "session-flow-retry",
                "status": "completed",
                "failure_reason": None,
                "total_evidence_records": 0,
                "step_evidence_counts": {},
                "adapter_keys": [],
            },
        }

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)

    response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert len(execute_calls) == 1
    assert execute_calls[0]["session_id"] == "session-flow-retry"
    assert execute_calls[0]["flow_run_id"] == "flow-run-retry"
    assert execute_calls[0]["trace_context"] is None
    assert flow.execution_count == 1
    assert db.commit_calls == 0
    assert [event["type"] for event in events] == ["RUN_STARTED", "CHAT_OUTPUT_READY", "FLOW_FINISHED"]
    stored_turn_messages = repository.list_messages_for_turn(
        session_id="session-flow-retry",
        user_auth_sub="auth-sub",
        chat_kind=chat.ASSISTANT_CHAT_KIND,
        turn_id="turn-flow-retry",
    )
    assert [message.role for message in stored_turn_messages] == ["user", "flow"]


def test_execute_flow_endpoint_retry_reuses_persisted_trace_context(monkeypatch):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(
        flow_id=flow_id,
        session_id="session-flow-trace-reuse",
        turn_id="turn-flow-trace-reuse",
    )
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Trace Reuse Flow",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)

    _patch_stream_dependencies(monkeypatch, cancel_requested=False)
    repository, _completion_db = _patch_durable_history(monkeypatch)
    execute_calls = []

    async def _fake_execute_flow(**kwargs):
        execute_calls.append(kwargs)
        if len(execute_calls) == 1:
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-flow-first"}}
            raise RuntimeError("socket dropped")

        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-flow-first"}}
        yield {
            "type": "CHAT_OUTPUT_READY",
            "timestamp": "2026-02-26T00:01:03+00:00",
            "details": {"output": "Recovered flow output."},
        }
        yield {
            "type": "FLOW_FINISHED",
            "timestamp": "2026-02-26T00:01:04+00:00",
            "data": {
                "flow_id": str(flow_id),
                "flow_name": "Trace Reuse Flow",
                "flow_run_id": kwargs["flow_run_id"],
                "document_id": None,
                "origin_session_id": "session-flow-trace-reuse",
                "status": "completed",
                "failure_reason": None,
                "total_evidence_records": 0,
                "step_evidence_counts": {},
                "adapter_keys": [],
            },
        }

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)

    first_response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )
    first_events = asyncio.run(_consume_stream(first_response))
    asyncio.run(first_response.background())

    second_response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )
    second_events = asyncio.run(_consume_stream(second_response))
    asyncio.run(second_response.background())

    assert [event["type"] for event in first_events] == ["RUN_STARTED", "SUPERVISOR_ERROR", "RUN_ERROR"]
    assert [event["type"] for event in second_events] == ["RUN_STARTED", "CHAT_OUTPUT_READY", "FLOW_FINISHED"]
    assert len(execute_calls) == 2
    assert execute_calls[0]["flow_run_id"]
    assert execute_calls[1]["flow_run_id"] == execute_calls[0]["flow_run_id"]
    assert execute_calls[0]["trace_context"] is None
    assert execute_calls[1]["trace_context"] == {"trace_id": "trace-flow-first"}
    user_turn = repository.get_message_by_turn_id(
        session_id="session-flow-trace-reuse",
        user_auth_sub="auth-sub",
        turn_id="turn-flow-trace-reuse",
        role="user",
    )
    assert user_turn is not None
    assert user_turn.trace_id == "trace-flow-first"
    flow_run_id, trace_id = chat._extract_execute_flow_runtime_identifiers(user_turn.payload_json)
    assert flow_run_id == execute_calls[0]["flow_run_id"]
    assert trace_id == "trace-flow-first"


def test_build_flow_memory_message_keeps_hidden_json_parseable_when_compacted():
    assistant_message = chat._build_flow_memory_assistant_message(
        flow_name="Large Flow",
        flow_id="flow-123",
        session_id="session-123",
        status="completed",
        trace_id="trace-123",
        final_user_output="done",
        agents_used=["Agent A"],
        specialist_outputs=[
            {
                "tool": "ask_gene_specialist",
                "output_length": 25000,
                "output": "X" * 25000,
            }
        ],
        specialist_summaries=[{"specialist": "Agent A", "summary": "S" * 6000}],
        domain_warnings=[{"reason": "warning", "message": "W" * 6000}],
        file_outputs=[{"file_id": "f1", "filename": "result.tsv", "metadata": "M" * 6000}],
        failure_reason=None,
    )

    start_marker = "<FLOW_INTERNAL_CONTEXT_JSON>\n"
    end_marker = "\n</FLOW_INTERNAL_CONTEXT_JSON>"
    hidden_json = assistant_message.split(start_marker, 1)[1].split(end_marker, 1)[0]

    assert len(hidden_json) <= chat._FLOW_MEMORY_MAX_HIDDEN_JSON_CHARS
    parsed_hidden = json.loads(hidden_json)
    assert parsed_hidden["flow"]["flow_id"] == "flow-123"


def test_execute_flow_failure_messages_surface_missing_reason():
    summary_content = chat._build_execute_flow_summary_content(
        status="failed",
        final_user_output=None,
        failure_reason=None,
    )
    assistant_message = chat._build_flow_memory_assistant_message(
        flow_name="Failure Flow",
        flow_id="flow-failure",
        session_id="session-failure",
        status="failed",
        trace_id="trace-failure",
        final_user_output=None,
        agents_used=[],
        specialist_outputs=[],
        specialist_summaries=[],
        domain_warnings=[],
        file_outputs=[],
        failure_reason=None,
    )

    assert summary_content == "Flow failed before producing a final output. Reason: None"
    assert "Reason: None" in assistant_message


@pytest.mark.parametrize(
    ("event_payload", "expected_message_type", "expected_content"),
    [
        (
            {"type": "DOMAIN_WARNING", "details": {}},
            "text",
            "Flow warning event missing message payload.",
        ),
        (
            {"type": "FLOW_STEP_EVIDENCE", "step": "one", "evidence_count": "many"},
            "flow_step_evidence",
            "Flow step evidence event missing integer step/evidence_count metadata.",
        ),
        (
            {"type": "FILE_READY", "details": {}},
            "file_download",
            "Generated file event missing filename metadata.",
        ),
    ],
)
def test_build_execute_flow_transcript_row_from_event_surfaces_missing_metadata(
    event_payload,
    expected_message_type,
    expected_content,
):
    row = chat._build_execute_flow_transcript_row_from_event(event_payload)

    assert row is not None
    assert row.message_type == expected_message_type
    assert row.content == expected_content


def test_execute_flow_endpoint_surfaces_trace_checkpoint_persistence_failure(monkeypatch, caplog):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(
        flow_id=flow_id,
        session_id="session-trace-checkpoint-failure",
        turn_id="turn-trace-checkpoint-failure",
    )
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Trace Checkpoint Failure Flow",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)
    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=False)
    _patch_durable_history(monkeypatch)

    async def _fake_execute_flow(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-checkpoint-failure"}}
        yield {
            "type": "CHAT_OUTPUT_READY",
            "details": {"output": "This output should never be reached."},
        }

    def _raise_checkpoint_failure(**_kwargs):
        raise RuntimeError("checkpoint write failed")

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)
    monkeypatch.setattr(chat, "_persist_execute_flow_runtime_state", _raise_checkpoint_failure)
    caplog.set_level(logging.ERROR, logger=chat.logger.name)

    response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == ["SUPERVISOR_ERROR", "RUN_ERROR"]
    assert events[0]["details"]["context"] == "RuntimeError"
    assert events[0]["details"]["error"] == "Flow execution failed unexpectedly."
    assert events[1]["message"] == "Flow execution failed unexpectedly."
    assert "checkpoint write failed" not in json.dumps(events)
    assert "checkpoint write failed" in caplog.text
    assert calls["unregister"] == [("session-trace-checkpoint-failure", "auth-sub", ANY)]
    assert calls["clear"] == ["session-trace-checkpoint-failure"]


def test_execute_flow_endpoint_surfaces_completion_persistence_failure(monkeypatch, caplog):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(
        flow_id=flow_id,
        session_id="session-completion-persistence-failure",
        turn_id="turn-completion-persistence-failure",
    )
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Completion Persistence Failure Flow",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)
    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=False)

    async def _fake_execute_flow(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-completion-failure"}}
        yield {
            "type": "CHAT_OUTPUT_READY",
            "details": {"output": "This output should be discarded when persistence fails."},
        }
        yield {
            "type": "FLOW_FINISHED",
            "data": {
                "status": "success",
                "flow_run_id": "flow-run-completion-failure",
            },
        }

    def _raise_completion_failure(**_kwargs):
        raise RuntimeError("completion transcript write failed")

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)
    monkeypatch.setattr(chat, "_persist_completed_execute_flow_turn", _raise_completion_failure)
    caplog.set_level(logging.ERROR, logger=chat.logger.name)

    response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == [
        "RUN_STARTED",
        "CHAT_OUTPUT_READY",
        "SUPERVISOR_ERROR",
        "RUN_ERROR",
    ]
    assert all(event["type"] != "FLOW_FINISHED" for event in events)
    assert events[2]["details"]["context"] == "RuntimeError"
    assert events[2]["details"]["error"] == "Flow execution failed unexpectedly."
    assert events[3]["message"] == "Flow execution failed unexpectedly."
    assert "completion transcript write failed" not in json.dumps(events)
    assert "completion transcript write failed" in caplog.text
    turn_messages = calls["repository"].list_messages_for_turn(
        session_id="session-completion-persistence-failure",
        user_auth_sub="auth-sub",
        chat_kind=chat.ASSISTANT_CHAT_KIND,
        turn_id="turn-completion-persistence-failure",
    )
    assert [message.role for message in turn_messages] == ["user"]
    assert calls["unregister"] == [
        ("session-completion-persistence-failure", "auth-sub", ANY)
    ]
    assert calls["clear"] == ["session-completion-persistence-failure"]


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


def test_execute_flow_endpoint_streams_error_events_on_executor_exception(monkeypatch, caplog):
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
    caplog.set_level(logging.ERROR, logger=chat.logger.name)

    response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    assert [event["type"] for event in events] == ["SUPERVISOR_ERROR", "RUN_ERROR"]
    assert events[0]["details"]["error"] == "Flow execution failed unexpectedly."
    assert events[1]["message"] == "Flow execution failed unexpectedly."
    assert "executor boom" not in json.dumps(events)
    assert "executor boom" in caplog.text
    assert events[1]["error_type"] == "RuntimeError"
    assert events[1]["session_id"] == "session-flow-error"
    assert calls["unregister"] == [("session-flow-error", "auth-sub", ANY)]
    assert calls["clear"] == ["session-flow-error"]


def test_execute_flow_endpoint_sanitizes_runner_run_error_event(monkeypatch, caplog):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(flow_id=flow_id, session_id="session-flow-run-error")
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Runner Error Flow",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)
    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=False)

    async def _fake_execute_flow(**_kwargs):
        yield {
            "type": "RUN_ERROR",
            "timestamp": "2026-02-26T00:01:03+00:00",
            "data": {"message": "runner exploded", "error_type": "RuntimeError"},
            "details": {"error": "runner exploded"},
        }

    monkeypatch.setattr(chat, "execute_flow", _fake_execute_flow)
    caplog.set_level(logging.ERROR, logger=chat.logger.name)

    response = asyncio.run(
        chat.execute_flow_endpoint(
            request=request,
            db=db,
            user={"sub": "auth-sub", "cognito:groups": []},
        )
    )

    events = asyncio.run(_consume_stream(response))
    asyncio.run(response.background())

    assert [event["type"] for event in events] == ["RUN_ERROR"]
    assert events[0]["message"] == "Flow execution failed unexpectedly."
    assert events[0]["details"]["error"] == "Flow execution failed unexpectedly."
    assert "runner exploded" not in json.dumps(events)
    assert "runner exploded" in caplog.text
    assert calls["unregister"] == [("session-flow-run-error", "auth-sub", ANY)]
    assert calls["clear"] == ["session-flow-run-error"]


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


def test_execute_flow_endpoint_sanitizes_validation_error(monkeypatch, caplog):
    flow_id = uuid4()
    request = chat.ExecuteFlowRequest(flow_id=flow_id, session_id="session-invalid-flow")
    flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Flow Validation",
        execution_count=0,
        last_executed_at=None,
    )
    db = _DummyDB(flow=flow)
    calls = _patch_stream_dependencies(monkeypatch, cancel_requested=False)

    def _raise_prepare(**_kwargs):
        raise ValueError("flow request contains hidden validation detail")

    monkeypatch.setattr(chat, "_prepare_execute_flow_turn", _raise_prepare)
    caplog.set_level(logging.WARNING, logger=chat.logger.name)

    with pytest.raises(chat.HTTPException) as exc:
        asyncio.run(
            chat.execute_flow_endpoint(
                request=request,
                db=db,
                user={"sub": "auth-sub", "cognito:groups": []},
            )
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid flow execution request"
    assert "flow request contains hidden validation detail" in caplog.text
    assert db.rollback_calls == 1
    assert calls["register"] == [("session-invalid-flow", "auth-sub", ANY)]
    assert calls["unregister"] == [("session-invalid-flow", "auth-sub", ANY)]
    assert calls["clear"] == ["session-invalid-flow"]


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
