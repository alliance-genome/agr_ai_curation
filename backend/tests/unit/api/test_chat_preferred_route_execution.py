"""Focused ordinary-chat preferred agent/flow routing regressions."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.api import chat_common
from src.api.chat_models import ResolvedChatRoute
from src.lib.chat_history_repository import (
    ASSISTANT_CHAT_KIND,
    AppendMessageResult,
    ChatMessageRecord,
)
from src.services.chat_route_preference_service import (
    ChatRoutePreferenceState,
    ChatRouteTarget,
)


def _record(*, content: str, turn_id: str, payload_json: dict) -> ChatMessageRecord:
    return ChatMessageRecord(
        message_id=uuid4(),
        session_id="session-1",
        chat_kind=ASSISTANT_CHAT_KIND,
        turn_id=turn_id,
        role="user",
        message_type="text",
        content=content,
        payload_json=payload_json,
        trace_id=None,
        created_at=datetime.now(timezone.utc),
    )


class _TurnRepository:
    def __init__(self) -> None:
        self.user_turn: ChatMessageRecord | None = None
        self.appended_payload: dict | None = None

    def get_or_create_session(self, **_kwargs):
        return None

    def get_message_by_turn_id(self, *, role: str, **_kwargs):
        return self.user_turn if role == "user" else None

    def append_message(self, *, content: str, turn_id: str, payload_json: dict, **_kwargs):
        self.appended_payload = payload_json
        self.user_turn = _record(
            content=content,
            turn_id=turn_id,
            payload_json=payload_json,
        )
        return AppendMessageResult(message=self.user_turn, created=True)


def test_prepare_turn_pins_agent_route_and_reuses_it_after_preference_change(monkeypatch):
    repository = _TurnRepository()
    db = SimpleNamespace(commit=lambda: None)
    target = ChatRouteTarget(
        id="gene_validation",
        kind="agent",
        display_name="Gene Validation",
        description=None,
        category="validation",
        available=True,
    )
    preference_reads = 0

    def _preference(*_args, **_kwargs):
        nonlocal preference_reads
        preference_reads += 1
        return ChatRoutePreferenceState("agent", target.id, None, True, target)

    monkeypatch.setattr(chat_common, "get_chat_route_preference", _preference)
    monkeypatch.setattr(
        chat_common,
        "resolve_chat_route_selection",
        lambda *_args, **_kwargs: ChatRoutePreferenceState(
            "agent", target.id, None, True, target
        ),
    )

    first = chat_common._prepare_chat_stream_turn(
        repository=repository,
        db=db,
        session_id="session-1",
        user_id="auth-sub",
        user_message="For RGD:619738, assess GO:0005515.",
        requested_turn_id="turn-1",
        active_document_id=None,
        db_user_id=7,
        active_groups=["RGD"],
    )
    assert first.route == ResolvedChatRoute(
        mode="agent",
        target_id="gene_validation",
        target_display_name="Gene Validation",
    )
    assert repository.appended_payload == {
        "chat_route": {
            "mode": "agent",
            "target_id": "gene_validation",
            "target_display_name": "Gene Validation",
        }
    }

    monkeypatch.setattr(
        chat_common,
        "get_chat_route_preference",
        lambda *_args, **_kwargs: pytest.fail("retry must not read the newer preference"),
    )
    retried = chat_common._prepare_chat_stream_turn(
        repository=repository,
        db=db,
        session_id="session-1",
        user_id="auth-sub",
        user_message="a changed request must also be ignored on replay",
        requested_turn_id="turn-1",
        active_document_id=None,
        db_user_id=7,
        active_groups=["RGD"],
    )
    assert preference_reads == 1
    assert retried.route == first.route
    assert retried.effective_user_message == "For RGD:619738, assess GO:0005515."


@pytest.mark.parametrize("agent_id", ["gene_validation", f"ca_{uuid4()}"])
@pytest.mark.asyncio
async def test_selected_agent_receives_exact_ordinary_chat_input(monkeypatch, agent_id):
    captured: dict = {}
    runtime_agent = SimpleNamespace(name="Gene Validation")

    def _get_agent(agent_id: str, **kwargs):
        captured["agent_id"] = agent_id
        captured["agent_kwargs"] = kwargs
        return runtime_agent

    async def _runner(**kwargs):
        captured["runner"] = kwargs
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    monkeypatch.setattr(chat_common, "get_agent_by_id", _get_agent)
    monkeypatch.setattr(chat_common, "run_agent_streamed", _runner)
    message = "For RGD:619738, assess GO:0005515 and explain the evidence."
    events = [
        event
        async for event in chat_common._run_resolved_chat_route(
            route=ResolvedChatRoute(
                mode="agent",
                target_id=agent_id,
                target_display_name="Gene Validation",
            ),
            db=SimpleNamespace(),
            db_user_id=7,
            context_messages=[{"role": "user", "content": message}],
            user_id="auth-sub",
            session_id="session-1",
            turn_id="turn-1",
            document_id=None,
            document_name=None,
            active_groups=["RGD"],
            supervisor_model=None,
            specialist_model=None,
            supervisor_temperature=None,
            specialist_temperature=None,
            supervisor_reasoning=None,
            specialist_reasoning=None,
        )
    ]
    assert captured["agent_id"] == agent_id
    assert captured["runner"]["context_messages"] == [
        {"role": "user", "content": message}
    ]
    assert captured["runner"]["agent"] is runtime_agent
    assert events[-1]["data"]["response"] == "done"


@pytest.mark.asyncio
async def test_selected_flow_receives_over_2000_char_message_and_surfaces_result_refs(monkeypatch):
    flow_id = uuid4()
    flow = SimpleNamespace(id=flow_id, name="Paper Review")
    captured: dict = {}
    result_id = str(uuid4())

    async def _execute_flow(**kwargs):
        captured.update(kwargs)
        yield {
            "type": "CHAT_OUTPUT_READY",
            "details": {"output": "flow answer"},
        }
        yield {
            "type": "FLOW_FINISHED",
            "data": {
                "status": "completed",
                "flow_id": str(flow_id),
                "flow_run_id": kwargs["flow_run_id"],
                "extraction_result_refs": [
                    {
                        "extraction_result_id": result_id,
                        "result_ref": f"extraction-result:{result_id}",
                        "agent_key": "gene_validation",
                    }
                ],
            },
        }

    monkeypatch.setattr(chat_common, "execute_flow", _execute_flow)
    message = "For RGD:619738, assess GO:0005515. " + ("x" * 2500)
    events = [
        event
        async for event in chat_common._run_resolved_chat_route(
            route=ResolvedChatRoute(
                mode="flow",
                target_id=str(flow_id),
                target_display_name="Paper Review",
                flow_run_id="flow-run-1",
            ),
            db=SimpleNamespace(get=lambda _model, _id: flow),
            db_user_id=7,
            context_messages=[{"role": "user", "content": message}],
            user_id="auth-sub",
            session_id="session-1",
            turn_id="turn-1",
            document_id=None,
            document_name=None,
            active_groups=["RGD"],
            supervisor_model=None,
            specialist_model=None,
            supervisor_temperature=None,
            specialist_temperature=None,
            supervisor_reasoning=None,
            specialist_reasoning=None,
        )
    ]
    assert len(message) > 2000
    assert captured["user_query"] == message
    assert captured["flow_run_id"] == "flow-run-1"
    internal = next(
        event
        for event in events
        if event["type"] == chat_common.INTERNAL_EXTRACTION_RESULT_EVENT_TYPE
    )
    assert internal["details"]["extraction_result_id"] == result_id
    assert events[-1] == {
        "type": "RUN_FINISHED",
        "data": {
            "response": "flow answer",
            "response_length": len("flow answer"),
            "agents_used": [],
        },
    }


def test_revoked_persisted_target_fails_closed_with_actionable_error(monkeypatch):
    target = ChatRouteTarget(
        id="gene_validation",
        kind="agent",
        display_name="Gene Validation",
        description=None,
        category=None,
        available=False,
    )
    monkeypatch.setattr(
        chat_common,
        "resolve_chat_route_selection",
        lambda *_args, **_kwargs: ChatRoutePreferenceState(
            "agent", target.id, None, False, target
        ),
    )
    with pytest.raises(chat_common.HTTPException) as exc:
        chat_common._authorize_chat_route(
            db=SimpleNamespace(),
            db_user_id=7,
            active_groups=[],
            route=ResolvedChatRoute(
                mode="agent",
                target_id=target.id,
                target_display_name=target.display_name,
            ),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "Your preferred chat agent 'Gene Validation' is no longer available. "
        "Choose another chat route in Tools and retry."
    )


def test_unavailable_new_selection_persists_turn_before_failing_closed(monkeypatch):
    repository = _TurnRepository()
    db = SimpleNamespace(commit=lambda: None)
    target = ChatRouteTarget(
        id="gene_validation",
        kind="agent",
        display_name="Gene Validation",
        description=None,
        category=None,
        available=False,
    )
    unavailable = ChatRoutePreferenceState(
        "agent", target.id, None, False, target
    )
    monkeypatch.setattr(
        chat_common,
        "get_chat_route_preference",
        lambda *_args, **_kwargs: unavailable,
    )
    monkeypatch.setattr(
        chat_common,
        "resolve_chat_route_selection",
        lambda *_args, **_kwargs: unavailable,
    )
    with pytest.raises(chat_common.HTTPException, match="no longer available"):
        chat_common._prepare_chat_stream_turn(
            repository=repository,
            db=db,
            session_id="session-1",
            user_id="auth-sub",
            user_message="Keep this durable request.",
            requested_turn_id="turn-unavailable",
            active_document_id=None,
            db_user_id=7,
            active_groups=[],
        )
    assert repository.user_turn is not None
    assert repository.user_turn.content == "Keep this durable request."
    assert repository.user_turn.payload_json == {
        "chat_route": {
            "mode": "agent",
            "target_id": "gene_validation",
            "target_display_name": "Gene Validation",
        }
    }


def test_durable_route_identity_is_not_a_client_authoritative_history_field():
    record = _record(
        content="request",
        turn_id="turn-1",
        payload_json={
            "chat_route": {
                "mode": "agent",
                "target_id": "gene_validation",
            }
        },
    )
    assert chat_common._serialize_message(record).payload_json == {}
