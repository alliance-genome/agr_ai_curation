"""Focused contract tests for the Agent Studio OpenAI Agents SDK adapter."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest
from agents import FunctionTool, ToolSearchTool

from src.lib.agent_studio import openai_runtime as runtime


def _tool_definition(name: str) -> dict:
    return {
        "name": name,
        "description": f"Run {name}",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
    }


def test_model_settings_pin_openai_reasoning_serial_tools_and_shared_retry():
    settings = runtime.build_agent_studio_model_settings(
        max_output_tokens=8192,
        tool_choice="save_flow",
    )

    assert settings.reasoning.effort == "medium"
    assert settings.temperature is None
    assert settings.top_p is None
    assert settings.tool_choice == "save_flow"
    assert settings.parallel_tool_calls is False
    assert settings.truncation == "auto"
    assert settings.max_tokens == 8192
    assert settings.store is True
    assert settings.include_usage is True
    assert settings.retry is not None


def test_tracked_sentry_span_treats_refusal_as_typed_outcome_without_content(monkeypatch):
    span = object()
    statuses = []
    data = []
    monkeypatch.setattr(runtime, "gen_ai_invoke_agent_span", lambda **_kwargs: nullcontext(span))
    monkeypatch.setattr(runtime, "set_sentry_span_status", lambda value, status: statuses.append((value, status)))
    monkeypatch.setattr(
        runtime,
        "set_redacted_ai_span_data",
        lambda value, key, payload: data.append((value, key, payload)),
    )

    with pytest.raises(runtime.ModelRefusalError):
        with runtime._tracked_agent_span(workflow="agent_studio_authoring"):
            raise runtime.ModelRefusalError("sensitive refusal details")

    assert statuses == [(span, "ok")]
    assert data[0] == (span, "ai_curation.agent_studio.outcome", "refusal")
    assert data[1][2] == {
        "phase": "agent_studio_authoring",
        "error_type": "ModelRefusalError",
    }
    assert "sensitive refusal details" not in repr(data)


@pytest.mark.parametrize(
    ("error", "expected_outcome"),
    [
        (
            runtime.ModelBehaviorError(
                "Responses stream ended with terminal event `response.incomplete`."
            ),
            "incomplete",
        ),
        (
            runtime.BadRequestError(
                "Request exceeded the context length token limit",
                response=httpx.Response(
                    400,
                    request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
                ),
                body={},
            ),
            "context_overflow",
        ),
    ],
)
def test_tracked_sentry_span_treats_expected_terminal_outcomes_as_non_crashes(
    monkeypatch,
    error,
    expected_outcome,
):
    span = object()
    statuses = []
    data = []
    monkeypatch.setattr(runtime, "gen_ai_invoke_agent_span", lambda **_kwargs: nullcontext(span))
    monkeypatch.setattr(runtime, "set_sentry_span_status", lambda value, status: statuses.append((value, status)))
    monkeypatch.setattr(
        runtime,
        "set_redacted_ai_span_data",
        lambda value, key, payload: data.append((value, key, payload)),
    )

    with pytest.raises(type(error)):
        with runtime._tracked_agent_span(workflow="agent_studio_authoring"):
            raise error

    assert statuses == [(span, "ok")]
    assert data[0] == (
        span,
        "ai_curation.agent_studio.outcome",
        expected_outcome,
    )
    assert data[1][2]["error_type"] == type(error).__name__
    assert "Request exceeded" not in repr(data)


def test_tools_use_one_hosted_search_surface_and_keep_forced_tool_eager():
    state = runtime.AgentStudioRunState(trace_id="trace-1")

    async def execute(_name, _arguments, _call_id):
        raise AssertionError("tool should not execute while constructing the surface")

    tools, metrics = runtime.build_agent_studio_tools(
        [_tool_definition("save_flow"), _tool_definition("inspect_trace")],
        executor=execute,
        state=state,
        namespace_for_tool=lambda name: (
            "flow_authoring" if name == "save_flow" else "trace_overview",
            "Authorized capabilities",
        ),
        forced_tool_name="save_flow",
    )

    search_tool = next(tool for tool in tools if isinstance(tool, ToolSearchTool))
    assert search_tool.execution == "server"
    assert search_tool.description is None
    function_tools = [tool for tool in tools if isinstance(tool, FunctionTool)]
    assert len(function_tools) == 2
    assert next(tool for tool in function_tools if tool.name == "save_flow").defer_loading is False
    deferred = next(tool for tool in function_tools if tool.name != "save_flow")
    assert deferred.defer_loading is True
    assert "inspect_trace" in deferred.name
    assert metrics == {
        "candidate_count": 2,
        "eager_count": 1,
        "deferred_count": 1,
        "namespace_count": 1,
    }


def test_capability_search_can_be_eager_without_eager_detail_catalog():
    state = runtime.AgentStudioRunState(trace_id="trace-catalog")

    async def execute(_name, _arguments, _call_id):
        raise AssertionError("tool should not execute while constructing the surface")

    tools, metrics = runtime.build_agent_studio_tools(
        [
            _tool_definition("search_studio_capabilities"),
            _tool_definition("get_studio_capability_detail"),
        ],
        executor=execute,
        state=state,
        namespace_for_tool=lambda _name: (
            "studio_capabilities",
            "Authenticated catalog",
        ),
        eager_tool_names=frozenset({"search_studio_capabilities"}),
    )

    function_tools = [tool for tool in tools if isinstance(tool, FunctionTool)]
    assert next(
        tool for tool in function_tools if tool.name == "search_studio_capabilities"
    ).defer_loading is False
    detail = next(
        tool for tool in function_tools if "get_studio_capability_detail" in tool.name
    )
    assert detail.defer_loading is True
    assert metrics["eager_count"] == 1
    assert metrics["deferred_count"] == 1


def test_function_tool_preserves_full_output_but_returns_bounded_provider_output():
    state = runtime.AgentStudioRunState(trace_id="trace-1")

    async def execute(name, arguments, call_id):
        assert (name, arguments, call_id) == ("save_flow", {"value": "draft"}, "call-1")
        return runtime.ToolExecutionResult(
            full_output={"success": True, "large_ui_payload": [1, 2, 3]},
            provider_output='{"success":true}',
        )

    tools, _ = runtime.build_agent_studio_tools(
        [_tool_definition("save_flow")],
        executor=execute,
        state=state,
        namespace_for_tool=lambda _name: ("flow_authoring", "Flow authoring"),
        forced_tool_name="save_flow",
    )
    tool = next(tool for tool in tools if isinstance(tool, FunctionTool))

    provider_output = asyncio.run(
        tool.on_invoke_tool(
            SimpleNamespace(tool_call_id="call-1"),
            '{"value":"draft"}',
        )
    )

    assert provider_output == '{"success":true}'
    assert state.executed_tools[0].output == {
        "success": True,
        "large_ui_payload": [1, 2, 3],
    }


def test_stream_translates_sdk_events_and_records_response_usage(monkeypatch):
    captured = {}
    closed = []
    provider = object()
    resources = SimpleNamespace(provider=provider)
    state = runtime.AgentStudioRunState(
        trace_id="trace-1",
        executed_tools=[
            runtime.ExecutedTool(
                tool_name="save_flow",
                call_id="call-1",
                arguments={"value": "draft"},
                output={"success": True},
            )
        ],
    )

    class _TextDelta:
        def __init__(self, delta):
            self.delta = delta

    events = [
        SimpleNamespace(type="raw_response_event", data=_TextDelta("Done")),
        SimpleNamespace(type="run_item_stream_event", name="tool_search_called", item=None),
        SimpleNamespace(
            type="run_item_stream_event",
            name="tool_search_output_created",
            item=SimpleNamespace(raw_item={"tools": [{"name": "save_flow"}]}),
        ),
        SimpleNamespace(
            type="run_item_stream_event",
            name="tool_called",
            item=SimpleNamespace(
                type="tool_call_item",
                tool_name="flow_authoring.save_flow",
                call_id="call-1",
                raw_item={"arguments": '{"value":"draft"}'},
            ),
        ),
        SimpleNamespace(
            type="run_item_stream_event",
            name="tool_output",
            item=SimpleNamespace(type="tool_call_output_item", call_id="call-1", output="bounded"),
        ),
    ]

    class _Result:
        last_response_id = "resp-1"
        final_output = "Done"
        context_wrapper = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=11,
                output_tokens=7,
                input_tokens_details=SimpleNamespace(cached_tokens=3),
                output_tokens_details=SimpleNamespace(reasoning_tokens=2),
            )
        )

        async def stream_events(self):
            for event in events:
                yield event

    def _run_streamed(agent, *, input, max_turns, run_config):
        captured.update(
            agent=agent,
            input=input,
            max_turns=max_turns,
            run_config=run_config,
        )
        return _Result()

    async def _close(value, **kwargs):
        closed.append((value, kwargs))

    monkeypatch.setattr(runtime, "ResponseTextDeltaEvent", _TextDelta)
    monkeypatch.setattr(runtime, "build_owned_openai_responses_resources", lambda: resources)
    monkeypatch.setattr(runtime, "close_owned_openai_resources", _close)
    monkeypatch.setattr(runtime.Runner, "run_streamed", _run_streamed)
    monkeypatch.setattr(runtime, "gen_ai_conversation_scope", lambda _session_id: nullcontext())
    def _sentry_span(**kwargs):
        captured["sentry"] = kwargs
        return nullcontext()

    monkeypatch.setattr(runtime, "gen_ai_invoke_agent_span", _sentry_span)

    async def collect():
        return [
            event
            async for event in runtime.stream_agent_studio_run(
                instructions="system",
                input_items=[{"role": "user", "content": "help"}],
                tools=[],
                state=state,
                session_id="session-1",
                user_id="user-1",
                max_turns=5,
                model_settings=runtime.build_agent_studio_model_settings(max_output_tokens=1024),
            )
        ]

    translated = asyncio.run(collect())

    assert [event["type"] for event in translated] == [
        "TEXT_DELTA",
        "TOOL_SEARCH",
        "TOOL_SEARCH_RESULT",
        "TOOL_USE",
        "TOOL_RESULT",
    ]
    assert translated[-1]["result"] == {"success": True}
    assert translated[-2]["tool_name"] == "save_flow"
    assert captured["agent"].model == "gpt-6-astra"
    assert captured["max_turns"] == 5
    assert captured["run_config"].model_provider is provider
    assert "input_preview" not in captured["sentry"]
    assert captured["sentry"]["span_data"] == {
        "ai_curation.agent_studio.input_item_count": 1,
        "ai_curation.agent_studio.tool_count": 0,
    }
    assert state.assistant_text == "Done"
    assert state.response_id == "resp-1"
    assert (state.input_tokens, state.output_tokens) == (11, 7)
    assert (state.cached_input_tokens, state.reasoning_tokens) == (3, 2)
    assert (state.tool_search_calls, state.tool_search_outputs, state.tool_search_loaded_tools) == (1, 1, 1)
    assert closed == [(resources, {"trace_id": "trace-1", "user_id": "user-1"})]


def test_stream_closes_owned_resources_when_runtime_construction_fails(monkeypatch):
    resources = SimpleNamespace(provider=object())
    state = runtime.AgentStudioRunState(trace_id="trace-construction")
    closed = []
    monkeypatch.setattr(runtime, "build_owned_openai_responses_resources", lambda: resources)
    monkeypatch.setattr(
        runtime,
        "_run_config",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("construction failed")),
    )

    async def _close(value, **kwargs):
        closed.append((value, kwargs))

    monkeypatch.setattr(runtime, "close_owned_openai_resources", _close)

    async def collect():
        return [
            event
            async for event in runtime.stream_agent_studio_run(
                instructions="system",
                input_items=[],
                tools=[],
                state=state,
                session_id="session-1",
                user_id="user-1",
                max_turns=1,
                model_settings=runtime.build_agent_studio_model_settings(max_output_tokens=100),
            )
        ]

    with pytest.raises(RuntimeError, match="construction failed"):
        asyncio.run(collect())
    assert closed == [
        (resources, {"trace_id": "trace-construction", "user_id": "user-1"})
    ]


def test_forced_tool_run_uses_sdk_runner_and_stops_after_submission(monkeypatch):
    captured = {}
    provider = object()
    resources = SimpleNamespace(provider=provider)
    state = runtime.AgentStudioRunState(trace_id="trace-suggestion")

    async def execute(name, arguments, call_id):
        assert (name, arguments, call_id) == (
            "submit_prompt_suggestion",
            {"value": "feedback"},
            "call-suggestion",
        )
        return runtime.ToolExecutionResult(
            full_output={"success": True, "suggestion_id": "suggestion-1"},
            provider_output='{"success":true}',
        )

    async def _run(agent, *, input, max_turns, run_config):
        captured.update(
            agent=agent,
            input=input,
            max_turns=max_turns,
            run_config=run_config,
        )
        await agent.tools[0].on_invoke_tool(
            SimpleNamespace(tool_call_id="call-suggestion"),
            '{"value":"feedback"}',
        )
        return SimpleNamespace(
            last_response_id="resp-suggestion",
            context_wrapper=SimpleNamespace(usage=None),
        )

    async def _close(_resources, **_kwargs):
        return None

    monkeypatch.setattr(runtime, "build_owned_openai_responses_resources", lambda: resources)
    monkeypatch.setattr(runtime, "close_owned_openai_resources", _close)
    monkeypatch.setattr(runtime.Runner, "run", _run)
    monkeypatch.setattr(runtime, "gen_ai_conversation_scope", lambda _session_id: nullcontext())
    monkeypatch.setattr(runtime, "gen_ai_invoke_agent_span", lambda **_kwargs: nullcontext())

    execution = asyncio.run(
        runtime.run_forced_agent_studio_tool(
            instructions="submit feedback",
            input_items=[{"role": "user", "content": "feedback"}],
            tool_definition=_tool_definition("submit_prompt_suggestion"),
            executor=execute,
            state=state,
            session_id="suggestion-session",
            user_id="user-1",
            max_turns=2,
            max_output_tokens=512,
        )
    )

    assert execution is not None
    assert execution.output == {"success": True, "suggestion_id": "suggestion-1"}
    assert captured["agent"].tool_use_behavior == "stop_on_first_tool"
    assert captured["agent"].model_settings.tool_choice == "submit_prompt_suggestion"
    assert captured["agent"].model_settings.parallel_tool_calls is False
    assert captured["run_config"].model_provider is provider
    assert captured["max_turns"] == 2
    assert state.response_id == "resp-suggestion"


def test_forced_tool_run_closes_owned_resources_when_runtime_construction_fails(
    monkeypatch,
):
    resources = SimpleNamespace(provider=object())
    state = runtime.AgentStudioRunState(trace_id="trace-suggestion-construction")
    closed = []

    async def execute(_name, _arguments, _call_id):
        raise AssertionError("tool must not execute")

    async def _close(value, **kwargs):
        closed.append((value, kwargs))

    monkeypatch.setattr(runtime, "build_owned_openai_responses_resources", lambda: resources)
    monkeypatch.setattr(runtime, "close_owned_openai_resources", _close)
    monkeypatch.setattr(
        runtime,
        "_run_config",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("construction failed")),
    )

    with pytest.raises(RuntimeError, match="construction failed"):
        asyncio.run(
            runtime.run_forced_agent_studio_tool(
                instructions="submit feedback",
                input_items=[],
                tool_definition=_tool_definition("submit_prompt_suggestion"),
                executor=execute,
                state=state,
                session_id="suggestion-session",
                user_id="user-1",
                max_turns=2,
                max_output_tokens=512,
            )
        )
    assert closed == [
        (
            resources,
            {
                "trace_id": "trace-suggestion-construction",
                "user_id": "user-1",
            },
        )
    ]


@pytest.mark.parametrize("tool_name,contract", [
    ("propose_workshop_draft_update", "workshop_authoring_proposal.v1"),
    ("propose_flow_draft_update", "flow_authoring_proposal.v1"),
])
def test_proposal_review_stops_only_after_valid_repair(tool_name, contract):
    state = runtime.AgentStudioRunState(trace_id="review-test")
    output = {"contract_version": contract, "success": False, "valid": False, "pending_user_approval": False}
    async def executor(*_args):
        return runtime.ToolExecutionResult(full_output=dict(output), provider_output="bounded result")
    tool = runtime._build_function_tool(_tool_definition(tool_name), executor=executor, state=state, defer_loading=False)
    behavior = runtime._proposal_review_behavior(state)
    asyncio.run(tool.on_invoke_tool(SimpleNamespace(tool_call_id="invalid"), '{}'))
    assert behavior(None, []).is_final_output is False
    output.update(success=True, valid=True, pending_user_approval=True)
    asyncio.run(tool.on_invoke_tool(SimpleNamespace(tool_call_id="repaired"), '{}'))
    result = behavior(None, [])
    assert result.is_final_output is True
    assert 'Apply changes' in result.final_output
    assert 'Nothing has been saved' in result.final_output
    output.update(pending_user_approval=False)
    asyncio.run(tool.on_invoke_tool(SimpleNamespace(tool_call_id="no_change"), '{}'))
    assert behavior(None, []).is_final_output is False


def test_sdk_accepts_review_on_last_allowed_model_turn_without_another_request():
    from agents import Agent, Model, ModelResponse, RunConfig, Runner, Usage
    from openai.types.responses import ResponseFunctionToolCall

    class ProposalModel(Model):
        calls = 0
        async def get_response(self, *args, **kwargs):
            self.calls += 1
            assert self.calls == 1
            return ModelResponse(output=[ResponseFunctionToolCall(
                id="fc_review", call_id="review", name="propose_workshop_draft_update",
                arguments='{}', type="function_call",
            )], usage=Usage(), response_id="response-review")
        async def stream_response(self, *args, **kwargs):
            raise AssertionError("This regression exercises the SDK turn-limit boundary")
            yield

    state = runtime.AgentStudioRunState(trace_id="last-turn")
    async def executor(*_args):
        return runtime.ToolExecutionResult(full_output={
            "contract_version": "workshop_authoring_proposal.v1", "success": True,
            "valid": True, "pending_user_approval": True,
        }, provider_output='{"valid":true}')
    tool = runtime._build_function_tool(_tool_definition("propose_workshop_draft_update"),
        executor=executor, state=state, defer_loading=False)
    model = ProposalModel()
    result = asyncio.run(Runner.run(Agent(name="Review", model=model, tools=[tool],
        tool_use_behavior=runtime._proposal_review_behavior(state)), "Make a draft",
        max_turns=1, run_config=RunConfig(tracing_disabled=True)))
    assert model.calls == 1
    assert 'Apply changes' in result.final_output


def test_streamed_invalid_then_valid_proposal_finishes_on_last_turn(monkeypatch):
    from agents import Model, RunConfig
    from openai.types.responses import Response, ResponseCompletedEvent, ResponseFunctionToolCall

    class ProposalModel(Model):
        calls = 0
        async def get_response(self, *args, **kwargs):
            raise AssertionError("Must exercise streaming")
        async def stream_response(self, *args, **kwargs):
            self.calls += 1
            assert self.calls <= 2
            response = Response.model_construct(
                id=f"response-{self.calls}", created_at=0, model="gpt-6-astra",
                object="response", status="completed", usage=None,
                output=[ResponseFunctionToolCall(
                    id=f"fc_{self.calls}", call_id=f"call_{self.calls}",
                    name="propose_workshop_draft_update", arguments='{}', type="function_call",
                )],
            )
            yield ResponseCompletedEvent(type="response.completed", sequence_number=0, response=response)

    model = ProposalModel()
    state = runtime.AgentStudioRunState(trace_id="streamed-review")
    async def executor(*_args):
        valid = model.calls == 2
        return runtime.ToolExecutionResult(full_output={
            "contract_version": "workshop_authoring_proposal.v1", "success": valid,
            "valid": valid, "pending_user_approval": valid,
        }, provider_output='{"valid":' + str(valid).lower() + '}')
    tool = runtime._build_function_tool(_tool_definition("propose_workshop_draft_update"),
        executor=executor, state=state, defer_loading=False)
    provider = SimpleNamespace(get_model=lambda _name: model)
    monkeypatch.setattr(runtime, 'build_owned_openai_responses_resources', lambda: SimpleNamespace(provider=provider))
    monkeypatch.setattr(runtime, '_run_config', lambda **_kwargs: RunConfig(model_provider=provider, tracing_disabled=True))
    async def close(*_args, **_kwargs): pass
    monkeypatch.setattr(runtime, 'close_owned_openai_resources', close)
    async def collect():
        return [event async for event in runtime.stream_agent_studio_run(
            instructions="Prepare one change", input_items=[{"role":"user","content":"Stocks and source"}],
            tools=[tool], state=state, session_id="test", user_id="test", max_turns=2,
            model_settings=runtime.build_agent_studio_model_settings(max_output_tokens=100),
        )]
    events = asyncio.run(collect())
    results = [event for event in events if event['type'] == 'TOOL_RESULT']
    assert [event['result']['valid'] for event in results] == [False, True]
    assert events[-1]['type'] == 'TEXT_DELTA'
    assert 'Apply changes' in events[-1]['delta']
    assert model.calls == 2
