from tests.cancellation_test_support import install_cancel_reader
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from agents import Agent, ModelSettings, function_tool
from src.lib.observability.cost_context import (
    attach_agent_cost_identity,
    get_agent_cost_identity,
)
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_history.store import HistoryConflict, HistoryScope


def test_named_phase_keeps_model_policy_and_has_no_dispatch_or_validator_paper_tools(
    monkeypatch,
):
    from src.lib.validation_review.reviewer import create_supervisor_phase
    from src.lib.validation_review.protocol import REVIEW_PROTOCOL
    from src.lib.openai_agents.tool_surface import apply_tool_surface

    @function_tool
    def ask_unconfigured_agent(query: str) -> str:
        return "must never be reachable"

    @function_tool
    def read_section(section: str) -> str:
        return "bound paper"

    supervisor = Agent(
        name="Named Supervisor",
        model="gpt-6-astra",
        instructions="Curator policy",
        model_settings=ModelSettings(temperature=0.2, reasoning={"effort": "high"}),
        tools=[ask_unconfigured_agent],
    )
    attach_agent_cost_identity(
        supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"}
    )
    from src.lib.prompts.assembly import _make_layer

    supervisor.validation_review_policy_layers = (
        _make_layer(
            layer_id="policy",
            kind="curator_overlay",
            title="Policy",
            content="Curator policy",
            provenance="test",
            editable=True,
            locked=False,
            source_ref="test",
        ),
    )
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    monkeypatch.setenv("VALIDATION_REVIEW_MODEL", "")
    apply_tool_surface(supervisor, runtime="chat_supervisor")
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.resolve_tools",
        lambda *args: pytest.fail("Review supervisor must not resolve paper readers"),
    )
    configurations = []
    monkeypatch.setattr(
        "src.lib.openai_agents.langfuse_client.log_agent_config",
        lambda **kwargs: configurations.append(kwargs),
    )
    context = ReviewContext(
        HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
        "episode",
        "supervisor.validation_review",
    )
    phase = create_supervisor_phase(
        context,
        supervisor=supervisor,
        checkpoint_provider=lambda: None,
        authenticated_groups=[],
        extractor_receipt={"agent_key": "saved", "contract": {"instructions": "Keep used reagents."}},
    )
    apply_tool_surface(phase, runtime="validation_reviewer")
    assert phase.model == supervisor.model
    assert phase.model_settings == supervisor.model_settings
    assert get_agent_cost_identity(phase)["agent_id"] == context.actor_id
    assert (
        get_agent_cost_identity(phase)["agent_role"]
        == get_agent_cost_identity(supervisor)["agent_role"]
    )
    assert configurations[0]["agent_name"] == phase.name
    assert configurations[0]["instructions"] == phase.instructions
    assert configurations[0]["tools"] == [tool.name for tool in phase.tools]
    assert (
        configurations[0]["metadata"]["layer_manifest"]["layers"][-1]["locked"] is True
    )
    assert phase.name == supervisor.name + " Validation Review" and phase.handoffs == []
    assert "inspect_results" not in phase.instructions
    assert "ask_unconfigured_agent" not in phase.instructions
    assert (
        "Curator policy" in phase.instructions
        and REVIEW_PROTOCOL.strip() in phase.instructions
    )
    names = {tool.name for tool in phase.tools}
    assert "record_validation_review" in names
    assert not names & {"search_document", "read_section", "read_subsection", "read_chunk"}
    assert "ask_unconfigured_agent" not in names
    assert {tool.name for tool in supervisor.tools} == {"ask_unconfigured_agent"}


@pytest.mark.asyncio
async def test_pending_terminal_is_authoritative_even_if_model_claims_complete(
    monkeypatch,
):
    from src.lib.openai_agents import runner
    from src.lib.validation_review.chat import ACTIVE_CHAT_REVIEW

    seen = []

    async def stream(**kwargs):
        seen.append(ACTIVE_CHAT_REVIEW.get())
        yield {"type": "RUN_FINISHED", "data": {"response": "Everything is validated!"}}

    monkeypatch.setattr(runner, "_run_agent_with_groq_retry", stream)
    context = SimpleNamespace(
        resume=AsyncMock(),
        status=lambda: {"pending_count": 1, "results": []},
        supervisor_handoff=lambda: {"review_outcomes": []},
        render_review_notes=lambda **kwargs: "",
    )
    events = [
        event
        async for event in runner._run_agent_with_chat_review(review_context=context)
    ]
    assert events[-1]["data"]["validation_review"]["pending_count"] == 1
    assert "not ready" in events[-1]["data"]["response"]
    assert events[0]["type"] == "TEXT_MESSAGE_CONTENT"
    assert seen == [context] and ACTIVE_CHAT_REVIEW.get() is None
    assert context.resume.await_count == 2


@pytest.mark.asyncio
async def test_nonchat_stream_does_not_activate_review(monkeypatch):
    from src.lib.openai_agents import runner
    from src.lib.validation_review.chat import ACTIVE_CHAT_REVIEW

    async def stream(**kwargs):
        assert ACTIVE_CHAT_REVIEW.get() is None
        yield {"type": "RUN_FINISHED", "data": {"response": "flow result"}}

    monkeypatch.setattr(runner, "_run_agent_with_groq_retry", stream)
    events = [
        event async for event in runner._run_agent_with_chat_review(review_context=None)
    ]
    assert events == [{"type": "RUN_FINISHED", "data": {"response": "flow result"}}]


@pytest.mark.asyncio
async def test_direct_terminal_uses_current_selection_not_original_response(
    monkeypatch,
):
    from src.lib.openai_agents import runner
    from src.lib.validation_review import chat_dispatch

    async def stream(**kwargs):
        yield {"type": "TEXT_MESSAGE_CONTENT", "data": {"delta": "Original eco:1"}}
        yield {"type": "STRUCTURED_RESULT", "data": {"result": "eco:1"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "Original eco:1"}}

    monkeypatch.setattr(runner, "is_builder_materializer_agent", lambda agent: True)
    monkeypatch.setattr(runner, "_run_agent_with_groq_retry", stream)
    monkeypatch.setattr(
        chat_dispatch, "claim", lambda *args: {"claimed": True, "id": "dispatch"}
    )
    saved = []
    monkeypatch.setattr(chat_dispatch, "complete", lambda *args: saved.append(args[-1]))
    context = SimpleNamespace(
        resume=AsyncMock(),
        direct_action=AsyncMock(return_value=SimpleNamespace(action="new_extraction")),
        status=lambda: {"pending_count": 0, "results": []},
        direct_delivery=lambda **kwargs: (
            "Accepted ECO:selected",
            {"pending_count": 0, "results": []},
        ),
    )
    agent = Agent(name="extractor")
    attach_agent_cost_identity(agent, {"agent_id": "extractor"})
    events = [
        event
        async for event in runner._run_agent_with_chat_review(
            review_context=context,
            direct_chat_context=object(),
            agent=agent,
            user_message="extract",
            trace_id="trace",
        )
    ]
    assert saved[0]["data"]["response"] == "Original eco:1"
    assert events[-1]["data"]["response"] == "Accepted ECO:selected"
    assert events[0]["data"]["observer_reconcile"] is True
    assert not any("eco:1" in str(event) for event in events)


@pytest.mark.asyncio
async def test_direct_saved_review_turn_never_calls_extractor(monkeypatch):
    from src.lib.openai_agents import runner
    from src.lib.validation_review.chat import DirectChatAction

    extractor = Agent(name="chosen extractor")

    async def forbidden(**kwargs):
        raise AssertionError("Review-only continuation must not invoke extractor")
        yield

    monkeypatch.setattr(runner, "is_builder_materializer_agent", lambda agent: True)
    monkeypatch.setattr(runner, "_run_agent_with_groq_retry", forbidden)
    context = SimpleNamespace(
        resume=AsyncMock(),
        direct_action=AsyncMock(
            return_value=DirectChatAction(
                action="continue_review", explanation="Continuing the saved review."
            )
        ),
        status=lambda: {
            "pending_count": 1,
            "results": [
                {
                    "result_ref": "saved-original",
                    "episode_id": "same-episode",
                    "ready": False,
                }
            ],
        },
        direct_delivery=lambda **kwargs: (
            "Saved values",
            {
                "pending_count": 1,
                "results": [
                    {
                        "result_ref": "saved-original",
                        "episode_id": "same-episode",
                        "ready": False,
                    }
                ],
            },
        ),
    )
    events = [
        event
        async for event in runner._run_agent_with_chat_review(
            review_context=context,
            direct_chat_context=object(),
            agent=extractor,
            user_message="Continue reviewing",
            trace_id="new-turn",
            input_items=[],
        )
    ]
    context.resume.assert_awaited_once()
    context.direct_action.assert_awaited_once_with(extractor, [])
    assert (
        events[-1]["data"]["validation_review"]["results"][0]["episode_id"]
        == "same-episode"
    )


@pytest.mark.asyncio
async def test_stop_interrupts_review_without_waiting_for_next_stream_event(
    monkeypatch,
):
    import asyncio
    from src.lib.validation_review.chat import ChatReview

    stopped = asyncio.Event()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def check(session):
        return stopped.is_set()

    install_cancel_reader(monkeypatch, check)

    async def slow_review():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    context = ChatReview("owner", "doc", "session", "turn", None, (), "review")
    task = asyncio.create_task(context.until_stop(slow_review()))
    await started.wait()
    stopped.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_direct_lookup_preserves_own_answer_and_never_claims_extraction(
    monkeypatch,
):
    from src.lib.openai_agents import runner
    from src.lib.validation_review import chat_dispatch

    async def stream(**kwargs):
        yield {"type": "TEXT_MESSAGE_CONTENT", "data": {"delta": "Lookup answer"}}
        yield {"type": "RUN_FINISHED", "data": {"response": "Lookup answer"}}

    monkeypatch.setattr(runner, "is_builder_materializer_agent", lambda agent: False)
    monkeypatch.setattr(runner, "_run_agent_with_groq_retry", stream)

    def forbidden(*args):
        raise AssertionError("Non-extractor must not be fenced")

    monkeypatch.setattr(chat_dispatch, "claim", forbidden)
    context = SimpleNamespace(
        resume=AsyncMock(),
        status=lambda: {"pending_count": 0, "results": []},
        supervisor_handoff=lambda: {"review_outcomes": []},
        render_review_notes=lambda **kwargs: "",
    )
    events = [
        event
        async for event in runner._run_agent_with_chat_review(
            review_context=context,
            direct_chat_context=object(),
            agent=Agent(name="lookup"),
        )
    ]
    assert events[-1]["data"]["response"] == "Lookup answer"
    assert events[0]["data"]["delta"] == "Lookup answer"


def test_direct_supervisor_resolves_requested_settings_without_routing_agent(
    monkeypatch,
):
    from src.lib.validation_review.reviewer import create_direct_supervisor
    from src.lib.openai_agents import config
    from src.lib.openai_agents.agents import supervisor_agent

    calls = []
    monkeypatch.setattr(
        config,
        "get_agent_config",
        lambda _: SimpleNamespace(model="default", temperature=0.5, reasoning="low"),
    )
    monkeypatch.setattr(config, "resolve_model_provider", lambda _: "openai")
    monkeypatch.setattr(config, "get_model_for_agent", lambda model, **kwargs: model)
    monkeypatch.setattr(
        "src.lib.prompts.assembly.build_agent_prompt_layers",
        lambda *a, **kw: SimpleNamespace(layers=(), hash="hash"),
    )

    def settings(model, **kwargs):
        calls.append((model, kwargs))
        return ModelSettings(reasoning={"effort": kwargs["reasoning_effort"]})

    monkeypatch.setattr(supervisor_agent, "_build_model_settings", settings)
    agent = create_direct_supervisor(
        active_groups=["TEST_GROUP"],
        model_override="gpt-6-astra",
        temperature_override=0.2,
        reasoning_override="high",
    )
    assert agent.model == "gpt-6-astra" and agent.tools == []
    assert calls[0][0] == "gpt-6-astra"
    assert (
        calls[0][1]["temperature"] == 0.2 and calls[0][1]["reasoning_effort"] == "high"
    )
    assert get_agent_cost_identity(agent)["agent_id"] == "supervisor.direct_chat"


@pytest.mark.asyncio
async def test_named_resume_without_extractor_delivers_actual_review_notes(monkeypatch):
    from src.lib.openai_agents import runner

    note = "A required observation is missing; ask the curator before fresh extraction."

    async def stream(**kwargs):
        assert note in kwargs["input_items"][-1]["content"]
        yield {"type": "RUN_FINISHED", "data": {"response": "Saved review resumed."}}

    monkeypatch.setattr(runner, "_run_agent_with_groq_retry", stream)
    context = SimpleNamespace(
        resume=AsyncMock(),
        status=lambda: {"pending_count": 1, "results": []},
        turn_id="current-turn",
        supervisor_handoff=lambda: {"review_outcomes": [{"turn_id": "current-turn", "outcome": {"output": note}}]},
        render_review_notes=lambda **kwargs: "\n\nReviewer's notes:\n" + note,
    )
    events = [
        event
        async for event in runner._run_agent_with_chat_review(
            review_context=context, input_items=[]
        )
    ]
    assert note in events[-1]["data"]["response"]
    assert any(note in event.get("data", {}).get("delta", "") for event in events)


@pytest.mark.asyncio
async def test_later_unrelated_turn_does_not_repeat_previous_review_notes(monkeypatch):
    from src.lib.openai_agents import runner
    from src.lib.validation_review.chat import ChatReview

    note = "Earlier review concern."
    context = SimpleNamespace(
        turn_id="second-turn", resume=AsyncMock(),
        status=lambda: {"pending_count": 0, "results": []},
        supervisor_handoff=lambda: {"review_outcomes": [
            {"turn_id": "first-turn", "outcome": {"output": note}}
        ]},
        review_notes=lambda **kwargs: [] if kwargs.get("current_turn_only") else [
            {"turn_id": "first-turn", "outcome": {"output": note}}
        ],
    )
    context.render_review_notes = lambda **kwargs: ChatReview.render_review_notes(context, **kwargs)

    async def stream(**kwargs):
        reference = kwargs["input_items"][-1]["content"]
        assert "older outcomes are context only" in reference
        assert "Current turn: second-turn" in reference
        yield {"type": "RUN_FINISHED", "data": {"response": "Unrelated answer."}}

    monkeypatch.setattr(runner, "_run_agent_with_groq_retry", stream)
    events = [event async for event in runner._run_agent_with_chat_review(
        review_context=context, input_items=[]
    )]
    assert events[-1]["data"]["response"] == "Unrelated answer."
    assert note not in str(events)


@pytest.mark.asyncio
async def test_stop_inside_real_sdk_tool_survives_sdk_cancellation_handling(monkeypatch):
    import asyncio
    from agents import Model, RunConfig, Runner
    from openai.types.responses import Response, ResponseCompletedEvent, ResponseFunctionToolCall
    from src.lib.openai_agents import runner
    from src.lib.validation_review.chat import ChatReview
    from src.lib.validation_review.stops import ReviewInterrupted

    context = ChatReview("owner", "doc", "session", "turn", None, (), "Extract")
    context.resume = AsyncMock()
    context.supervisor_handoff = lambda: {"review_outcomes": []}
    install_cancel_reader(monkeypatch, AsyncMock(return_value=True))
    called = []

    @function_tool
    async def extractor_tool() -> str:
        called.append(True)
        await context.until_stop(asyncio.Event().wait())
        return "unreachable"

    class ToolModel(Model):
        async def get_response(self, *args, **kwargs):
            raise AssertionError("Expected streamed SDK")

        async def stream_response(self, *args, **kwargs):
            response = Response.model_construct(
                id="response-1", created_at=0, model="fixture", object="response",
                status="completed", usage=None,
                output=[ResponseFunctionToolCall(
                    id="fc1", call_id="call1", name="extractor_tool",
                    arguments="{}", type="function_call",
                )],
            )
            yield ResponseCompletedEvent(type="response.completed", sequence_number=0, response=response)

    async def sdk_stream(**kwargs):
        result = Runner.run_streamed(
            Agent(name="Supervisor", model=ToolModel(), tools=[extractor_tool]),
            "Extract", max_turns=1, run_config=RunConfig(tracing_disabled=True),
        )
        async for event in result.stream_events():
            yield {"type": "SDK_EVENT", "data": {}}
        # Some SDK versions consume tool cancellation and finish normally.
        yield {"type": "RUN_FINISHED", "data": {"response": "Must not complete"}}

    monkeypatch.setattr(runner, "_run_agent_with_groq_retry", sdk_stream)

    async def collect():
        return [event async for event in runner._run_agent_with_chat_review(
            review_context=context, input_items=[]
        )]

    with pytest.raises(ReviewInterrupted):
        await asyncio.wait_for(collect(), timeout=5)
    assert called == [True] and context.interrupted


def _phase_supervisor():
    from agents import Agent, ModelSettings

    return Agent(name="Flow Supervisor", instructions="Route.", model="gpt-6-astra",
                 model_settings=ModelSettings(parallel_tool_calls=False))


def test_review_uses_its_configured_model_and_keeps_tool_call_policy(monkeypatch):
    from unittest.mock import Mock
    from src.lib.openai_agents import config
    from src.lib.validation_review.reviewer import _review_model_overrides

    monkeypatch.setenv("VALIDATION_REVIEW_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("VALIDATION_REVIEW_REASONING", "medium")
    sentinel = Mock()
    monkeypatch.setattr(config, "get_model_for_agent", lambda model, provider_override=None: (sentinel, model)[0])
    settings = Mock()
    build = Mock(return_value=settings)
    monkeypatch.setattr(config, "build_model_settings", build)
    overrides = _review_model_overrides(_phase_supervisor(), "Review instructions")
    assert overrides == {"model": sentinel, "model_settings": settings}
    arguments = build.call_args.kwargs
    assert arguments["model"] == "gpt-6.1-sol" and arguments["reasoning_effort"] == "medium"
    assert arguments["parallel_tool_calls"] is False
    assert arguments["prompt_cache"].agent_key == "supervisor.validation_review"


def test_empty_review_model_keeps_the_supervisor_model(monkeypatch):
    from src.lib.validation_review.reviewer import _review_model_overrides

    monkeypatch.setenv("VALIDATION_REVIEW_MODEL", "")
    assert _review_model_overrides(_phase_supervisor(), "Review instructions") == {}


def test_benchmark_routes_keep_their_pinned_review_model(monkeypatch):
    from src.lib.openai_agents.benchmark_routing import benchmark_route_plan
    from src.lib.validation_review.reviewer import _review_model_overrides

    monkeypatch.setenv("VALIDATION_REVIEW_MODEL", "gpt-6.1-sol")
    route = {"provider": "openai", "model": "gpt-6-astra", "reasoning_effort": "low"}
    with benchmark_route_plan({"supervisor": route}):
        assert _review_model_overrides(_phase_supervisor(), "Review instructions") == {}


def test_review_model_defaults_to_sol(monkeypatch):
    from src.lib.openai_agents.config import get_validation_review_model, get_validation_review_reasoning

    monkeypatch.delenv("VALIDATION_REVIEW_MODEL", raising=False)
    monkeypatch.delenv("VALIDATION_REVIEW_REASONING", raising=False)
    assert get_validation_review_model() == "gpt-6.1-sol"
    assert get_validation_review_reasoning() == "medium"


def test_startup_validation_rejects_a_bad_review_model_or_reasoning():
    from src.lib.config.model_env_validation import model_env_errors

    assert any("VALIDATION_REVIEW_MODEL" in error for error in model_env_errors({"VALIDATION_REVIEW_MODEL": "not-a-model"}))
    assert any("VALIDATION_REVIEW_REASONING" in error for error in model_env_errors(
        {"VALIDATION_REVIEW_MODEL": "gpt-6.1-sol", "VALIDATION_REVIEW_REASONING": "not-a-level"}))
    assert not [error for error in model_env_errors(
        {"VALIDATION_REVIEW_MODEL": "gpt-6.1-sol", "VALIDATION_REVIEW_REASONING": "medium"}) if "VALIDATION_REVIEW" in error]


def test_compose_lets_an_empty_review_model_keep_the_supervisor_model():
    from pathlib import Path

    root = Path(__file__).resolve().parents[5]
    composes = [path for path in (root / name for name in ("docker-compose.yml", "docker-compose.production.yml")) if path.is_file()]
    if not composes:
        pytest.skip("Compose files are not mounted in this test image")
    for path in composes:
        assert "${VALIDATION_REVIEW_MODEL-gpt-6.1-sol}" in path.read_text()


def test_finished_review_names_pending_records_instead_of_a_bare_stop_code():
    from src.lib.validation_review.chat import ChatReview

    # Rendering reads only the saved outcome: no records, checkpoint or labels
    # are looked up, so a record that later leaves the result keeps its name.
    pending = [{"record_target_id": "crb-entry", "why": "not_accepted", "recheck_left": False,
                "label": "crb p13A"}]
    context = SimpleNamespace(review_notes=lambda **kwargs: [
        {"result_ref": "extraction-result:result-1", "outcome": {
            "output": "The crb p13A record is out of scope.", "reason": "reviewer_left_pending",
            "causes": [{"target_id": "crb-unit", "code": "reviewer_left_pending", "decision": "reject"},
                       {"target_id": "crb-entry", "code": "extraction_attempts_exhausted"}],
            "pending": pending}},
        {"result_ref": "extraction-result:result-1", "outcome": {"reason": "cancelled", "pending": []}},
    ])
    notes = ChatReview.render_review_notes(context)
    assert notes == (
        "\n\nReviewer's notes:\nThe crb p13A record is out of scope.\n\n"
        "Review finished with 1 record still pending: crb p13A (the reviewer did not accept it, "
        "and no re-check is left). Saved results and history are retained.\n\n"
        "Review paused: cancelled. Saved results and history are retained.")
    assert "extraction attempts exhausted" not in notes


def test_a_system_error_stop_reads_as_plain_words_not_its_code():
    from src.lib.validation_review.chat import ChatReview

    context = SimpleNamespace(review_notes=lambda **kwargs: [
        {"outcome": {"state": "needs_curator_review", "reason": "source_projection_invalid", "resource": None}}])
    notes = ChatReview.render_review_notes(context)
    assert notes == ("\n\nReviewer's notes:\nReview paused: a system error stopped a save before it changed "
                     "anything, and it was reported. Saved results and history are retained.")
    assert "source projection" not in notes


def test_a_stop_inside_a_packet_save_keeps_its_reason():
    """A packet save names record conflicts by handle; a review stop is not a record conflict."""
    from src.lib.validation_review.admission import ReviewStopped
    from src.lib.validation_review.decisions import _named_errors

    entries = [{"target_id": "record-target", "decision_unit_id": "record-target", "action": "retire"}]
    with pytest.raises(ReviewStopped) as stopped:
        with _named_errors(["D1"], entries):
            raise ReviewStopped("source_projection_invalid")
    assert stopped.value.reason == "source_projection_invalid"
    with pytest.raises(HistoryConflict) as conflict:
        with _named_errors(["D1"], entries):
            raise HistoryConflict("record-target: Curator authority prevents supervisor retirement")
    assert str(conflict.value) == "D1: Curator authority prevents supervisor retirement"
    assert not isinstance(conflict.value, ReviewStopped)


CRB_REASON = "Crb-GFP is named only in the Discussion imaging legend; its use in this study is not established."
# The line quotes the saved reason whole, without its one final period.
CRB_QUOTED = CRB_REASON.removesuffix(".")
CRB_RETIRED = {"record_target_id": "r41", "label": "Crb-GFP", "reason": CRB_REASON, "retired_by": "review",
               "new_this_turn": True}


def _retaining_review(monkeypatch, retired, blockers=(), listed=()):
    """A chat review whose history reads are stubbed; retain_review_outcome is real.

    listed: the records the review's previous saved summary named as retired.
    """
    saved, lookups = [], []
    monkeypatch.setattr("src.lib.validation_review.audit.append_fact",
                        lambda db, aid, **kwargs: saved.append(kwargs["body"]["outcome"]))
    monkeypatch.setattr("src.lib.validation_history.store.SessionLocal", MagicMock())
    monkeypatch.setattr("src.lib.validation_history.store._artifact", lambda *args: None)
    monkeypatch.setattr("src.lib.validation_review.record_retirement.retired_in_review",
                        lambda db, aid, episode_id: retired)
    monkeypatch.setattr("src.lib.validation_review.chat.listed_removals", lambda db, aid, episode_id: set(listed))
    monkeypatch.setattr("src.lib.validation_history.selections.manifest_in_transaction",
                        lambda db, scope, aid, episode_id: {"blockers": list(blockers), "ready": not blockers})
    # 42 records extracted; an active record is pending when a blocker names it.
    monkeypatch.setattr("src.lib.validation_history.record_lifecycle.counts",
                        lambda db, aid, blockers: {"active": 42 - len(retired), "retired": len(retired),
                                                   "pending": len(blockers)})
    context = SimpleNamespace(
        context=lambda record: ReviewContext(HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
                                             "episode", "supervisor"),
        reference=lambda record: {"artifact_id": "artifact"},
        review_summary_operation=lambda context: "summary-op", turn_id="turn",
        saved_review_outcome=lambda record: saved[-1] if saved else None,
        record_labels=lambda record, ids, retired_entries=(): lookups.append((ids, retired_entries)) or {
            "crb-entry": "crb p13A", "r41": "Crb-GFP", "r3": "white"},
    )
    return context, saved, lookups


def _retired(target, reason, retired_by="review"):
    return {"record_target_id": target, "reason": reason, "retired_by": retired_by,
            "entry": {"object_type": "generic_object", "metadata": {"validation_history": {"target_id": target}}}}


def test_retained_outcome_saves_each_pending_label_once(monkeypatch):
    from src.lib.validation_review.chat import ChatReview

    context, saved, lookups = _retaining_review(monkeypatch, [], [{"target_id": "crb-unit", "code": "unreviewed"}])
    record = SimpleNamespace(extraction_result_id="result-1")
    outcome = {"reason": "reviewer_left_pending",
               "pending": [{"record_target_id": "crb-entry", "why": "not_accepted", "recheck_left": False}]}
    ChatReview.retain_review_outcome(context, record, outcome)
    assert saved[-1]["pending"][0]["label"] == "crb p13A" and lookups == [({"crb-entry"}, [])]
    assert saved[-1]["review_counts"] == {"accepted": 41, "pending": 1, "retired_in_this_review": 0}
    # A reconnect reuses the saved outcome of its turn, never relabelling or recounting it.
    ChatReview.retain_review_outcome(context, record, saved[-1])
    assert len(saved) == 1 and len(lookups) == 1


def test_retained_outcome_saves_final_counts_and_each_record_review_removed(monkeypatch):
    """Trace 57c57c62: 42 extracted, the reviewer retired Crb-GFP itself, 41 accepted."""
    from src.lib.validation_review.chat import ChatReview

    crb = _retired("r41", CRB_REASON)
    context, saved, lookups = _retaining_review(monkeypatch, [crb])
    ChatReview.retain_review_outcome(context, SimpleNamespace(extraction_result_id="result-1"),
                                     {"output": "Reviewed all records.", "review": {"ready": True, "uncovered": []}})
    assert saved == [{"output": "Reviewed all records.", "review": {"ready": True, "uncovered": []},
                      "review_counts": {"accepted": 41, "pending": 0, "retired_in_this_review": 1},
                      "retired": [CRB_RETIRED]}]
    assert lookups == [(set(), [crb["entry"]])], "the removed record is named from the entry saved with its retirement"


def test_a_removal_the_previous_summary_listed_is_not_new_this_turn(monkeypatch):
    from src.lib.validation_review.chat import ChatReview

    context, saved, _ = _retaining_review(monkeypatch, [
        _retired("r41", CRB_REASON), _retired("r3", "A marker, not a transgenic stock.", "extractor_recommendation")],
        listed={"r41"})
    ChatReview.retain_review_outcome(context, SimpleNamespace(extraction_result_id="result-1"),
                                     {"output": "Reviewed all records.", "review": {"ready": True, "uncovered": []}})
    assert saved[0]["review_counts"] == {"accepted": 40, "pending": 0, "retired_in_this_review": 2}
    assert [(item["label"], item["retired_by"], item["new_this_turn"]) for item in saved[0]["retired"]] == [
        ("Crb-GFP", "review", False), ("white", "extractor_recommendation", True)]


def test_retained_outcome_without_removals_adds_only_its_counts(monkeypatch):
    from src.lib.validation_review.chat import ChatReview

    context, saved, _ = _retaining_review(monkeypatch, [])
    ChatReview.retain_review_outcome(context, SimpleNamespace(extraction_result_id="result-1"),
                                     {"output": "Reviewed all records.", "review": {"ready": True, "uncovered": []}})
    assert saved == [{"output": "Reviewed all records.", "review": {"ready": True, "uncovered": []},
                      "review_counts": {"accepted": 42, "pending": 0, "retired_in_this_review": 0}}]


def test_finished_review_names_records_removed_this_turn_beside_pending_ones():
    from src.lib.validation_review.chat import ChatReview

    pending = [{"record_target_id": "crb-entry", "why": "not_accepted", "recheck_left": False, "label": "crb p13A"}]
    white = {"record_target_id": "r3", "label": "white", "reason": "A marker, not a transgenic stock.",
             "retired_by": "extractor_recommendation", "new_this_turn": True}
    earlier = {**CRB_RETIRED, "new_this_turn": False}
    context = SimpleNamespace(review_notes=lambda **kwargs: [
        {"outcome": {"output": "Reviewed all records.", "review": {"ready": True},
                     "review_counts": {"accepted": 41, "pending": 0, "retired_in_this_review": 1},
                     "retired": [CRB_RETIRED]}},
        {"outcome": {"output": "One record is left for the curator.", "reason": "reviewer_left_pending",
                     "pending": pending, "review_counts": {"accepted": 38, "pending": 1, "retired_in_this_review": 2},
                     "retired": [CRB_RETIRED, white]}},
        # Crb-GFP was announced on an earlier turn; this turn removed only white.
        {"outcome": {"reason": "cancelled", "pending": [],
                     "review_counts": {"accepted": 2, "pending": 38, "retired_in_this_review": 2},
                     "retired": [earlier, white]}},
        # Nothing new this turn: no removal line at all.
        {"outcome": {"reason": "superseded", "pending": [],
                     "review_counts": {"accepted": 41, "pending": 0, "retired_in_this_review": 1},
                     "retired": [earlier]}},
    ])
    assert ChatReview.render_review_notes(context) == (
        "\n\nReviewer's notes:\nReviewed all records.\n\n"
        "Review removed 1 record that doesn't answer the question: Crb-GFP (" + CRB_QUOTED + "). "
        "41 records are current.\n\n"
        "One record is left for the curator.\n\n"
        "Review removed 2 records that don't answer the question: Crb-GFP (" + CRB_QUOTED + "); "
        "white (A marker, not a transgenic stock). 39 records are current.\n\n"
        "Review finished with 1 record still pending: crb p13A (the reviewer did not accept it, "
        "and no re-check is left). Saved results and history are retained.\n\n"
        "Review removed 1 record that doesn't answer the question: white (A marker, not a transgenic stock). "
        "40 records are current.\n\n"
        "Review paused: cancelled. Saved results and history are retained.\n\n"
        "Review paused: superseded. Saved results and history are retained.")
    assert ".)." not in ChatReview.render_review_notes(context)


@pytest.mark.asyncio
async def test_final_answer_gets_the_review_facts_and_is_followed_by_the_removal_line(monkeypatch):
    """The supervisor reads the counts and removals before answering; code prints the line after it."""
    from src.lib.openai_agents import runner
    from src.lib.validation_review.chat import ChatReview

    outcome = {"output": "Reviewed all records.", "review": {"ready": True, "state": "complete"},
               "review_counts": {"accepted": 41, "pending": 0, "retired_in_this_review": 1},
               "retired": [CRB_RETIRED]}
    result = SimpleNamespace(extraction_result_id="result-1")
    context = SimpleNamespace(
        turn_id="current-turn", resume=AsyncMock(),
        status=lambda: {"pending_count": 0, "results": []},
        records=lambda: [result],
        review_request=lambda record: "What transgenic flies are used?" if record is result else None,
        review_notes=lambda **kwargs: [{"result_ref": "extraction-result:result-1", "turn_id": "current-turn",
                                        "outcome": outcome}],
    )
    context.supervisor_handoff = lambda: ChatReview.supervisor_handoff(context)
    context.render_review_notes = lambda **kwargs: ChatReview.render_review_notes(context, **kwargs)
    seen = []

    async def stream(**kwargs):
        seen.append(kwargs["input_items"][-1]["content"])
        yield {"type": "RUN_FINISHED", "data": {"response": "The publication uses 41 transgenic fly stocks."}}

    monkeypatch.setattr(runner, "_run_agent_with_groq_retry", stream)
    events = [event async for event in runner._run_agent_with_chat_review(review_context=context, input_items=[])]
    assert '"review_counts": {"accepted": 41, "pending": 0, "retired_in_this_review": 1}' in seen[0]
    assert '"label": "Crb-GFP"' in seen[0] and '"retired_by": "review"' in seen[0]
    assert '"question": "What transgenic flies are used?"' in seen[0], "each outcome names its own question"
    line = ("Review removed 1 record that doesn't answer the question: Crb-GFP (" + CRB_QUOTED
            + "). 41 records are current.")
    response = events[-1]["data"]["response"]
    assert response.startswith("The publication uses 41 transgenic fly stocks.\n\nReviewer's notes:")
    assert response.endswith(line)
    assert any(line in event.get("data", {}).get("delta", "") for event in events)
