from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents import Agent, ModelSettings, function_tool
from src.lib.observability.cost_context import (
    attach_agent_cost_identity,
    get_agent_cost_identity,
)
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_history.store import HistoryScope


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
    apply_tool_surface(supervisor, runtime="chat_supervisor")
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.resolve_tools",
        lambda *args: [read_section],
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
    assert "read_section" in names and "record_validation_review" in names
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

    monkeypatch.setattr("src.lib.redis_client.check_cancel_signal", check)

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
    monkeypatch.setattr("src.lib.redis_client.check_cancel_signal", AsyncMock(return_value=True))
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
