"""Focused ordinary-chat preferred agent/flow routing regressions."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.api import chat_common
from src.api.chat_models import ResolvedChatRoute
from src.lib.flows.outcome import FlowRunOutcome
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


def _assistant_record(*, turn_id: str, terminal_events: list[dict]) -> ChatMessageRecord:
    return ChatMessageRecord(
        message_id=uuid4(),
        session_id="session-1",
        chat_kind=ASSISTANT_CHAT_KIND,
        turn_id=turn_id,
        role="assistant",
        message_type="text",
        content="flow answer",
        payload_json={
            chat_common._FLOW_TRANSCRIPT_REPLAY_TERMINAL_EVENTS_KEY: terminal_events
        },
        trace_id="trace-1",
        created_at=datetime.now(timezone.utc),
    )


def _persisted_flow_terminal_events(**terminal_data) -> list[dict]:
    outcome = FlowRunOutcome()
    outcome.observe({"type": "FLOW_FINISHED", **terminal_data})
    return outcome.events_for_persistence()


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


def test_completed_legacy_turn_replays_before_strict_route_parsing(monkeypatch):
    user_turn = _record(content="legacy request", turn_id="turn-legacy", payload_json={})
    assistant_turn = ChatMessageRecord(
        message_id=uuid4(),
        session_id="session-1",
        chat_kind=ASSISTANT_CHAT_KIND,
        turn_id="turn-legacy",
        role="assistant",
        message_type="text",
        content="legacy response",
        payload_json=None,
        trace_id="trace-legacy",
        created_at=datetime.now(timezone.utc),
    )

    class _LegacyRepository:
        def get_or_create_session(self, **_kwargs):
            return None

        def get_message_by_turn_id(self, *, role: str, **_kwargs):
            return user_turn if role == "user" else assistant_turn

    monkeypatch.setattr(
        chat_common,
        "resolve_chat_route_selection",
        lambda *_args, **_kwargs: pytest.fail("completed replay must not reauthorize"),
    )

    prepared = chat_common._prepare_chat_stream_turn(
        repository=_LegacyRepository(),
        db=SimpleNamespace(commit=lambda: None),
        session_id="session-1",
        user_id="auth-sub",
        user_message="ignored retry content",
        requested_turn_id="turn-legacy",
        active_document_id=None,
        db_user_id=7,
        active_groups=[],
    )

    assert prepared.replay_assistant_turn == assistant_turn
    assert prepared.effective_user_message == "legacy request"
    assert prepared.route is None


def test_incomplete_turn_without_pinned_route_remains_invalid():
    user_turn = _record(content="incomplete request", turn_id="turn-incomplete", payload_json={})

    class _IncompleteRepository:
        def get_or_create_session(self, **_kwargs):
            return None

        def get_message_by_turn_id(self, *, role: str, **_kwargs):
            return user_turn if role == "user" else None

    with pytest.raises(ValueError, match="missing its resolved route identity"):
        chat_common._prepare_chat_stream_turn(
            repository=_IncompleteRepository(),
            db=SimpleNamespace(commit=lambda: None),
            session_id="session-1",
            user_id="auth-sub",
            user_message="retry",
            requested_turn_id="turn-incomplete",
            active_document_id=None,
            db_user_id=7,
            active_groups=[],
        )


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
        user_message="For MOD:619738, assess GO:0005515.",
        requested_turn_id="turn-1",
        active_document_id=None,
        db_user_id=7,
        active_groups=["MOD"],
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
        active_groups=["MOD"],
    )
    assert preference_reads == 1
    assert retried.route == first.route
    assert retried.effective_user_message == "For MOD:619738, assess GO:0005515."


@pytest.mark.parametrize(
    "agent_id",
    ["gene_validation", "ca_00000000-0000-4000-8000-000000000001"],
)
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
    message = "For MOD:619738, assess GO:0005515 and explain the evidence."
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
            active_groups=["MOD"],
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
async def test_selected_rgd_flow_receives_current_message_and_surfaces_distinct_result_refs(
    monkeypatch,
):
    flow_id = uuid4()
    flow = SimpleNamespace(id=flow_id, name="RGD GO and Disease Paper Review")
    captured: dict = {}
    go_result_id = str(uuid4())
    disease_result_id = str(uuid4())

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
                        "extraction_result_id": go_result_id,
                        "result_ref": f"extraction-result:{go_result_id}",
                        "agent_key": "rgd_go_paper_curator",
                    },
                    {
                        "extraction_result_id": disease_result_id,
                        "result_ref": f"extraction-result:{disease_result_id}",
                        "agent_key": "disease_extractor",
                    },
                ],
            },
        }

    monkeypatch.setattr(chat_common, "execute_flow", _execute_flow)
    message = "For MOD:619738, assess GO:0005515. " + ("x" * 2500)
    events = [
        event
        async for event in chat_common._run_resolved_chat_route(
            route=ResolvedChatRoute(
                mode="flow",
                target_id=str(flow_id),
                target_display_name="RGD GO and Disease Paper Review",
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
    assert captured["inspection_context"] is None
    internal_refs = [
        event["details"]
        for event in events
        if event["type"] == chat_common.INTERNAL_EXTRACTION_RESULT_EVENT_TYPE
    ]
    assert internal_refs == [
        {
            "extraction_result_id": go_result_id,
            "result_ref": f"extraction-result:{go_result_id}",
            "agent_key": "rgd_go_paper_curator",
        },
        {
            "extraction_result_id": disease_result_id,
            "result_ref": f"extraction-result:{disease_result_id}",
            "agent_key": "disease_extractor",
        },
    ]
    terminal = next(
        event
        for event in events
        if event["type"] == chat_common._PREFERRED_FLOW_TERMINAL_EVENTS_EVENT_TYPE
    )
    assert [event["type"] for event in terminal["internal"]["events"]] == [
        "CHAT_OUTPUT_READY",
        "FLOW_FINISHED",
    ]
    assert events[-1] == {
        "type": "RUN_FINISHED",
        "data": {
            "response": "flow answer",
            "response_length": len("flow answer"),
            "agents_used": [],
        },
    }


@pytest.mark.asyncio
async def test_failed_preferred_flow_reports_one_sanitized_terminal_failure(monkeypatch):
    flow_id = uuid4()
    flow = SimpleNamespace(id=flow_id, name="RGD GO and Disease Paper Review")
    captures: list[dict] = []

    async def _execute_flow(**_kwargs):
        yield {
            "type": "RUN_ERROR",
            "data": {
                "message": "provider payload that must not enter Sentry",
                "error_type": "InvalidStatus",
                "phase": "provider_call",
                "provider": "openai",
            },
        }
        yield {
            "type": "FLOW_FINISHED",
            "data": {
                "status": "failed",
                "failure_reason": "provider payload that must not enter Sentry",
            },
        }

    monkeypatch.setattr(chat_common, "execute_flow", _execute_flow)
    monkeypatch.setattr(
        chat_common,
        "report_runtime_exception",
        lambda exc, **kwargs: captures.append({"exception": exc, **kwargs}),
    )

    events = [
        event
        async for event in chat_common._run_resolved_chat_route(
            route=ResolvedChatRoute(
                mode="flow",
                target_id=str(flow_id),
                target_display_name=flow.name,
                flow_run_id="flow-run-failed",
            ),
            db=SimpleNamespace(get=lambda _model, _id: flow),
            db_user_id=7,
            context_messages=[{"role": "user", "content": "review this"}],
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

    assert len(captures) == 1
    capture = captures[0]
    assert str(capture["exception"]) == "InvalidStatus during provider_call"
    assert capture["component"] == "preferred_flow_chat_stream"
    assert capture["operation"] == "terminal_outcome_failed"
    assert capture["tags"] == {
        "ai_curation.flow.id_hash": chat_common.hash_sentry_identifier(flow_id),
        "flow_failure_type": "InvalidStatus",
        "phase": "provider_call",
        "provider": "openai",
        "tool_name": None,
    }
    assert [event["type"] for event in events] == ["RUN_ERROR"]
    assert events[0]["data"]["message"] == (
        "provider payload that must not enter Sentry"
    )


@pytest.mark.asyncio
async def test_preferred_flow_does_not_recapture_upstream_persistence_failure(monkeypatch):
    flow_id = uuid4()
    flow = SimpleNamespace(id=flow_id, name="RGD GO and Disease Paper Review")
    captures: list[dict] = []

    async def _execute_flow(**_kwargs):
        yield {
            "type": "FLOW_ERROR",
            "details": {
                "reason": "extraction_persistence_failed",
                "message": "Extraction persistence failed.",
            },
        }
        yield {
            "type": "FLOW_FINISHED",
            "data": {
                "status": "failed",
                "failure_reason": "Extraction persistence failed.",
            },
        }

    monkeypatch.setattr(chat_common, "execute_flow", _execute_flow)
    monkeypatch.setattr(
        chat_common,
        "report_runtime_exception",
        lambda exc, **kwargs: captures.append({"exception": exc, **kwargs}),
    )

    events = [
        event
        async for event in chat_common._run_resolved_chat_route(
            route=ResolvedChatRoute(
                mode="flow",
                target_id=str(flow_id),
                target_display_name=flow.name,
                flow_run_id="flow-run-persistence-failed",
            ),
            db=SimpleNamespace(get=lambda _model, _id: flow),
            db_user_id=7,
            context_messages=[{"role": "user", "content": "review this"}],
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

    assert captures == []
    assert [event["type"] for event in events] == ["FLOW_ERROR", "RUN_ERROR"]


@pytest.mark.asyncio
async def test_selected_rgd_flow_followup_inspects_prior_refs_without_redispatch(
    monkeypatch,
):
    flow_id = uuid4()
    flow = SimpleNamespace(id=flow_id, name="RGD GO and Disease Paper Review")
    go_result_id = str(uuid4())
    disease_result_id = str(uuid4())
    calls: list[dict] = []
    dispatched_tools: list[str] = []

    async def _execute_flow(**kwargs):
        calls.append(kwargs)
        if kwargs["inspection_context"] is None:
            dispatched_tools.extend(
                ["rgd_go_paper_curator", "disease_extractor", "chat_output"]
            )
            yield {
                "type": "CHAT_OUTPUT_READY",
                "details": {"output": "separate GO and disease review"},
            }
            yield {
                "type": "FLOW_FINISHED",
                "data": {
                    "status": "completed",
                    "flow_id": str(flow_id),
                    "flow_run_id": kwargs["flow_run_id"],
                    "document_id": "doc-1",
                    "extraction_result_refs": [
                        {
                            "extraction_result_id": go_result_id,
                            "result_ref": f"extraction-result:{go_result_id}",
                            "agent_key": "rgd_go_paper_curator",
                        },
                        {
                            "extraction_result_id": disease_result_id,
                            "result_ref": f"extraction-result:{disease_result_id}",
                            "agent_key": "disease_extractor",
                        },
                    ],
                },
            }
            return

        dispatched_tools.append("inspect_results")
        yield {
            "type": "TOOL_COMPLETE",
            "details": {"toolName": "inspect_results"},
        }
        yield {
            "type": "CHAT_OUTPUT_READY",
            "details": {
                "output": "GO:0005515 is supported by the prior Cttn evidence.",
                "inspection_only": True,
            },
        }
        yield {
            "type": "FLOW_FINISHED",
            "data": {
                "status": "completed",
                "completion_mode": "inspection_only",
                "flow_id": str(flow_id),
                "flow_run_id": kwargs["flow_run_id"],
                "document_id": "doc-1",
                "extraction_result_refs": [],
            },
        }

    async def _run_turn(*, message: str, run_id: str, inspection_context=None):
        return [
            event
            async for event in chat_common._run_resolved_chat_route(
                route=ResolvedChatRoute(
                    mode="flow",
                    target_id=str(flow_id),
                    target_display_name=flow.name,
                    flow_run_id=run_id,
                ),
                db=SimpleNamespace(get=lambda _model, _id: flow),
                db_user_id=7,
                context_messages=[{"role": "user", "content": message}],
                user_id="auth-sub",
                session_id="session-1",
                turn_id=run_id,
                document_id="doc-1",
                document_name="paper.pdf",
                active_groups=["RGD"],
                supervisor_model=None,
                specialist_model=None,
                supervisor_temperature=None,
                specialist_temperature=None,
                supervisor_reasoning=None,
                specialist_reasoning=None,
                inspection_context=inspection_context,
            )
        ]

    monkeypatch.setattr(chat_common, "execute_flow", _execute_flow)
    first_events = await _run_turn(
        message="Review Cttn and MicroRNA-124-3p for GO and disease.",
        run_id="flow-run-1",
    )
    first_terminal = next(
        event
        for event in first_events
        if event["type"] == chat_common._PREFERRED_FLOW_TERMINAL_EVENTS_EVENT_TYPE
    )

    class _Repository:
        def list_recent_messages(self, **_kwargs):
            return [
                _assistant_record(
                    turn_id="turn-1",
                    terminal_events=first_terminal["internal"]["events"],
                )
            ]

    inspection_context = chat_common._preferred_flow_inspection_context(
        repository=_Repository(),  # type: ignore[arg-type]
        session_id="session-1",
        user_id="auth-sub",
        flow_id=str(flow_id),
        document_id="doc-1",
    )
    followup = "What about GO:0005515 for Cttn in the prior review?"
    second_events = await _run_turn(
        message=followup,
        run_id="flow-run-2",
        inspection_context=inspection_context,
    )

    assert inspection_context == chat_common.PreferredFlowInspectionContext(
        flow_id=str(flow_id),
        flow_run_id="flow-run-1",
        document_id="doc-1",
        result_refs=(
            f"extraction-result:{go_result_id}",
            f"extraction-result:{disease_result_id}",
        ),
    )
    assert calls[1]["flow"] is flow
    assert calls[1]["user_query"] == followup
    assert calls[1]["inspection_context"] == inspection_context
    assert dispatched_tools == [
        "rgd_go_paper_curator",
        "disease_extractor",
        "chat_output",
        "inspect_results",
    ]
    assert not any(
        event["type"] == chat_common.INTERNAL_EXTRACTION_RESULT_EVENT_TYPE
        for event in second_events
    )
    second_terminal = next(
        event
        for event in second_events
        if event["type"] == chat_common._PREFERRED_FLOW_TERMINAL_EVENTS_EVENT_TYPE
    )
    assert second_terminal["internal"]["events"][-1]["completion_mode"] == (
        "inspection_only"
    )


def test_preferred_flow_file_output_is_persisted_for_durable_replay(monkeypatch):
    file_event = {
        "type": "FILE_READY",
        "details": {
            "file_id": "file-123",
            "filename": "review.tsv",
            "format": "tsv",
        },
    }
    flow_finished = {
        "type": "FLOW_FINISHED",
        "status": "completed",
        "flow_run_id": "flow-run-1",
    }
    appended: list[dict] = []

    class _CompletionRepository:
        def get_session(self, **_kwargs):
            return SimpleNamespace(session_id="session-1")

        def get_message_by_turn_id(self, **_kwargs):
            return None

        def append_message(self, **kwargs):
            appended.append(kwargs)
            record = ChatMessageRecord(
                message_id=uuid4(),
                session_id=kwargs["session_id"],
                chat_kind=kwargs["chat_kind"],
                turn_id=kwargs["turn_id"],
                role=kwargs["role"],
                message_type=kwargs.get("message_type", "text"),
                content=kwargs["content"],
                payload_json=kwargs.get("payload_json"),
                trace_id=kwargs.get("trace_id"),
                created_at=datetime.now(timezone.utc),
            )
            return AppendMessageResult(message=record, created=True)

    completion_db = SimpleNamespace(commit=lambda: None, rollback=lambda: None, close=lambda: None)
    monkeypatch.setattr(chat_common, "SessionLocal", lambda: completion_db)
    monkeypatch.setattr(
        chat_common, "_get_chat_history_repository", lambda _db: _CompletionRepository()
    )
    monkeypatch.setattr(chat_common, "_persist_extraction_candidates", lambda **_kwargs: None)

    assistant = chat_common._persist_completed_chat_stream_turn(
        session_id="session-1",
        user_id="auth-sub",
        turn_id="turn-1",
        user_message="create a review file",
        assistant_message="Flow completed. Review the generated results above.",
        trace_id="trace-1",
        extraction_candidates=[],
        document_id=None,
        flow_terminal_events=[file_event, flow_finished],
    )

    assert [(row["role"], row.get("message_type", "text")) for row in appended] == [
        ("flow", "file_download"),
        ("assistant", "text"),
    ]
    assert appended[0]["payload_json"] == file_event
    assert chat_common._preferred_flow_replay_events(assistant) == [
        file_event,
        flow_finished,
    ]


def test_preferred_flow_two_turn_context_uses_persisted_same_flow_document_refs():
    flow_id = str(uuid4())
    other_flow_id = str(uuid4())
    result_id = str(uuid4())
    messages = [
        _assistant_record(
            turn_id="turn-other-flow",
            terminal_events=_persisted_flow_terminal_events(
                status="completed",
                flow_id=other_flow_id,
                flow_run_id="other-run",
                document_id="doc-1",
                extraction_result_refs=[
                    {"result_ref": f"extraction-result:{uuid4()}"}
                ],
            ),
        ),
        _assistant_record(
            turn_id="turn-matching",
            terminal_events=_persisted_flow_terminal_events(
                status="completed",
                flow_id=flow_id,
                flow_run_id="matching-run",
                document_id="doc-1",
                extraction_result_refs=[
                    {"result_ref": f"extraction-result:{result_id}"},
                    {"result_ref": "client-result:arbitrary"},
                ],
            ),
        ),
        _assistant_record(
            turn_id="turn-inspection-only",
            terminal_events=_persisted_flow_terminal_events(
                status="completed",
                completion_mode="inspection_only",
                flow_id=flow_id,
                flow_run_id="inspection-run",
                document_id="doc-1",
                extraction_result_refs=[],
            ),
        ),
    ]
    calls: list[dict] = []

    class _Repository:
        def list_recent_messages(self, **kwargs):
            calls.append(kwargs)
            return messages

    context = chat_common._preferred_flow_inspection_context(
        repository=_Repository(),
        session_id="session-1",
        user_id="auth-sub",
        flow_id=flow_id,
        document_id="doc-1",
    )

    assert calls == [
        {
            "session_id": "session-1",
            "user_auth_sub": "auth-sub",
            "chat_kind": ASSISTANT_CHAT_KIND,
        }
    ]
    assert context == chat_common.PreferredFlowInspectionContext(
        flow_id=flow_id,
        flow_run_id="matching-run",
        document_id="doc-1",
        result_refs=(f"extraction-result:{result_id}",),
    )


@pytest.mark.parametrize(
    ("flow_matches", "document_id"),
    [(False, "doc-1"), (True, "doc-2")],
)
def test_preferred_flow_followup_context_rejects_mismatched_flow_or_document(
    flow_matches, document_id
):
    flow_id = str(uuid4())
    event_flow_id = flow_id if flow_matches else str(uuid4())
    result_id = str(uuid4())
    assistant = _assistant_record(
        turn_id="turn-1",
        terminal_events=_persisted_flow_terminal_events(
            status="completed",
            flow_id=event_flow_id,
            flow_run_id="flow-run-1",
            document_id="doc-1",
            extraction_result_refs=[
                {"result_ref": f"extraction-result:{result_id}"}
            ],
        ),
    )

    class _Repository:
        def list_recent_messages(self, **_kwargs):
            return [assistant]

    assert (
        chat_common._preferred_flow_inspection_context(
            repository=_Repository(),
            session_id="session-1",
            user_id="auth-sub",
            flow_id=flow_id,
            document_id=document_id,
        )
        is None
    )


def test_newest_same_flow_changed_document_blocks_older_matching_refs():
    flow_id = str(uuid4())
    older_result_id = str(uuid4())
    messages = [
        _assistant_record(
            turn_id="turn-older",
            terminal_events=_persisted_flow_terminal_events(
                status="completed",
                flow_id=flow_id,
                flow_run_id="older-run",
                document_id="doc-1",
                extraction_result_refs=[
                    {"result_ref": f"extraction-result:{older_result_id}"}
                ],
            ),
        ),
        _assistant_record(
            turn_id="turn-newer",
            terminal_events=_persisted_flow_terminal_events(
                status="completed",
                flow_id=flow_id,
                flow_run_id="newer-run",
                document_id="doc-2",
                extraction_result_refs=[
                    {"result_ref": f"extraction-result:{uuid4()}"}
                ],
            ),
        ),
    ]

    class _Repository:
        def list_recent_messages(self, **_kwargs):
            return messages

    assert (
        chat_common._preferred_flow_inspection_context(
            repository=_Repository(),
            session_id="session-1",
            user_id="auth-sub",
            flow_id=flow_id,
            document_id="doc-1",
        )
        is None
    )


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


def test_revoked_persisted_flow_fails_before_followup_context_can_run(monkeypatch):
    flow_id = str(uuid4())
    target = ChatRouteTarget(
        id=flow_id,
        kind="flow",
        display_name="Paper Review",
        description=None,
        category=None,
        available=False,
    )
    monkeypatch.setattr(
        chat_common,
        "resolve_chat_route_selection",
        lambda *_args, **_kwargs: ChatRoutePreferenceState(
            "flow", target.id, None, False, target
        ),
    )

    with pytest.raises(chat_common.HTTPException) as exc:
        chat_common._authorize_chat_route(
            db=SimpleNamespace(),
            db_user_id=7,
            active_groups=[],
            route=ResolvedChatRoute(
                mode="flow",
                target_id=flow_id,
                target_display_name=target.display_name,
                flow_run_id="flow-run-2",
            ),
        )

    assert exc.value.status_code == 409
    assert "preferred chat flow 'Paper Review' is no longer available" in exc.value.detail


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
