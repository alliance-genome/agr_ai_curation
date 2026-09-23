"""ALL-1279: effective model-request measurement and known-invalid request blocking."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from types import SimpleNamespace

import pytest
from agents import (
    Agent,
    FunctionTool,
    ModelResponse,
    ModelSettings,
    RunConfig,
    Runner,
    ToolSearchTool,
    Usage,
)
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel, OpenAIResponsesWSModel
from agents.retry import ModelRetrySettings
from agents.usage import InputTokensDetails, OutputTokensDetails
from openai import AsyncOpenAI
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from openai.types.responses.response_usage import (
    InputTokensDetails as ResponseInputTokensDetails,
)
from openai.types.responses.response_usage import (
    OutputTokensDetails as ResponseOutputTokensDetails,
)
from openai.types.responses.response_usage import ResponseUsage
from pydantic import BaseModel

import src.lib.openai_agents.runner  # noqa: F401  (installs measurement)
from src.lib.context import set_current_trace_id
from src.lib.observability import runtime as observability_runtime
from src.lib.observability import sentry as observability_sentry
from src.lib.observability.cost_context import cost_scope
from src.lib.openai_agents import model_request_measurement as measurement_module
from src.lib.openai_agents.model_request_measurement import (
    ModelRequestBlockedError,
    call_measured_direct_request,
    model_request_measurement_installed,
)

DANIELA_INSTRUCTION_CHARS = 1_422_809
OPENAI_LIMIT = 1_048_576


# ---------------------------------------------------------------------------
# Fixtures and fakes
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    measurement_module._reported_blocks.clear()
    for key in (
        "OPENAI_INSTRUCTIONS_MAX_CHARS",
        "MODEL_REQUEST_WARNING_ESTIMATED_TOKENS",
        "MODEL_REQUEST_TOOL_RESULT_WARNING_CHARS",
    ):
        monkeypatch.delenv(key, raising=False)
    published: list[dict] = []
    original_publish = measurement_module._publish

    def capture(measurement):
        original_publish(measurement)
        published.append(
            {k: copy.deepcopy(v) for k, v in measurement.items() if not k.startswith("_")}
        )

    monkeypatch.setattr(measurement_module, "_publish", capture)
    set_current_trace_id("trace-all1279")
    yield published
    set_current_trace_id(None)


@pytest.fixture
def published(_isolate):
    return _isolate


@pytest.fixture
def sentry_calls(monkeypatch):
    calls = {"exceptions": [], "contexts": [], "fingerprints": [], "tags": []}

    class _Scope:
        def set_level(self, level):
            pass

        def set_tag(self, key, value):
            calls["tags"].append((key, value))

        def set_context(self, key, value):
            calls["contexts"].append((key, value))

        def __setattr__(self, key, value):
            if key == "fingerprint":
                calls["fingerprints"].append(value)
            object.__setattr__(self, key, value)

    class _ScopeManager:
        def __enter__(self):
            return _Scope()

        def __exit__(self, *exc):
            return False

    fake_sdk = SimpleNamespace(
        new_scope=lambda: _ScopeManager(),
        capture_exception=lambda exc: calls["exceptions"].append(exc) or "event-id",
    )

    def fake_import(name):
        if name == "sentry_sdk":
            return fake_sdk
        raise ImportError(name)

    monkeypatch.setattr(observability_runtime.importlib, "import_module", fake_import)
    # The patch is process-global; never let a lazily cached importlib lookup
    # (for example sentry._owned_diagnostic_error_types) outlive this test.
    observability_sentry._owned_diagnostic_error_types.cache_clear()
    yield calls
    observability_sentry._owned_diagnostic_error_types.cache_clear()


def _usage(input_tokens=1200, cached=300, output_tokens=80, reasoning=20) -> Usage:
    return Usage(
        requests=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        input_tokens_details=InputTokensDetails(cached_tokens=cached),
        output_tokens_details=OutputTokensDetails(reasoning_tokens=reasoning),
    )


def _message(text: str, *, msg_id: str = "msg_1") -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id=msg_id,
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


class _RecordingMixin:
    """Record each provider-adapter invocation; never touch the network."""

    def _init_recording(self, responses):
        self.calls: list[dict] = []
        self._responses = list(responses)

    def _next(self, kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def get_response(self, system_instructions, input, model_settings, tools,
                           output_schema, handoffs, tracing, **kwargs):
        return self._next(
            {
                "instructions": system_instructions,
                "input": copy.deepcopy(input),
                "tools": tools,
                "output_schema": output_schema,
            }
        )

    async def stream_response(self, system_instructions, input, model_settings, tools,
                              output_schema, handoffs, tracing, **kwargs):
        model_response = self._next(
            {
                "instructions": system_instructions,
                "input": copy.deepcopy(input),
                "tools": tools,
                "output_schema": output_schema,
            }
        )
        response = Response.model_construct(
            id=model_response.response_id or "resp_stream",
            created_at=0,
            model="gpt-test",
            object="response",
            status="completed",
            output=model_response.output,
            usage=ResponseUsage(
                input_tokens=900,
                input_tokens_details=ResponseInputTokensDetails(cached_tokens=100),
                output_tokens=40,
                output_tokens_details=ResponseOutputTokensDetails(reasoning_tokens=5),
                total_tokens=940,
            ),
        )
        yield ResponseCompletedEvent(type="response.completed", sequence_number=0, response=response)


class FakeResponsesHTTPModel(_RecordingMixin, OpenAIResponsesModel):
    def __init__(self, responses=()):
        super().__init__(model="gpt-test", openai_client=AsyncOpenAI(api_key="test-key"))
        self._init_recording(responses)


class FakeResponsesWSModel(_RecordingMixin, OpenAIResponsesWSModel):
    def __init__(self, responses=()):
        super().__init__(model="gpt-test", openai_client=AsyncOpenAI(api_key="test-key"))
        self._init_recording(responses)


class FakeChatCompletionsModel(_RecordingMixin, OpenAIChatCompletionsModel):
    def __init__(self, responses=(), provider_id=None):
        super().__init__(model="gemini-test", openai_client=AsyncOpenAI(api_key="test-key"))
        if provider_id:
            self._agr_provider_id = provider_id
        self._init_recording(responses)


def _final(text="done", response_id="resp_final", usage=None):
    return ModelResponse(output=[_message(text)], usage=usage or _usage(), response_id=response_id)


def _run(agent, prompt="Render the saved table", **kwargs):
    return asyncio.run(
        Runner.run(agent, prompt, run_config=RunConfig(tracing_disabled=True), **kwargs)
    )


# ---------------------------------------------------------------------------
# Installation guard
# ---------------------------------------------------------------------------


def test_sdk_model_resolution_is_measured():
    from agents.run_internal import run_loop, turn_preparation

    assert model_request_measurement_installed()
    assert run_loop.get_model is turn_preparation.get_model
    wrapped = run_loop.get_model(Agent(name="probe", model=FakeResponsesHTTPModel()), RunConfig())
    assert isinstance(wrapped, measurement_module.MeasuredModel)


@pytest.mark.parametrize("order", ["foreign_wrapper_first", "measurement_first"])
def test_install_order_with_dynamic_foreign_wrapper(monkeypatch, order):
    """Sentry's OpenAI Agents integration rebinds run_loop.get_model to a wrapper
    that resolves turn_preparation.get_model at call time. Either install order
    must measure exactly once and never recurse."""
    import functools

    from agents.run_internal import run_loop, turn_preparation

    sdk_get_model = measurement_module._SDK_GET_MODEL
    assert sdk_get_model is not None
    monkeypatch.setattr(turn_preparation, "get_model", sdk_get_model)
    monkeypatch.setattr(run_loop, "get_model", sdk_get_model)
    monkeypatch.setattr(measurement_module, "_SDK_GET_MODEL", None)
    monkeypatch.setattr(measurement_module, "_MEASURED_GET_MODEL", None)

    def install_foreign_wrapper():
        @functools.wraps(turn_preparation.get_model)
        def foreign_get_model(agent, run_config):
            return turn_preparation.get_model(agent, run_config)

        run_loop.get_model = foreign_get_model

    if order == "foreign_wrapper_first":
        install_foreign_wrapper()
        measurement_module.install_model_request_measurement()
    else:
        measurement_module.install_model_request_measurement()
        install_foreign_wrapper()
    measurement_module.install_model_request_measurement()  # idempotent

    resolved = run_loop.get_model(Agent(name="probe", model=FakeResponsesHTTPModel()), RunConfig())
    assert isinstance(resolved, measurement_module.MeasuredModel)
    assert not isinstance(resolved.inner_model, measurement_module.MeasuredModel)
    assert model_request_measurement_installed()


def test_owned_openai_responses_provider_classifies_as_openai(monkeypatch):
    """Agent Studio's owned provider stays native OpenAI even if the default runner changes."""
    from src.lib.openai_agents import runner

    resources = runner.build_owned_openai_responses_resources()
    assert resources.provider._agr_provider_id == "openai"
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_default_runner_provider",
        lambda: SimpleNamespace(provider_id="some_other_default"),
    )
    wrapped = measurement_module.measure_resolved_model(
        FakeResponsesHTTPModel(),
        agent=None,
        run_config=RunConfig(model_provider=resources.provider),
    )
    assert measurement_module.describe_model(
        wrapped.inner_model, provider_hint=wrapped._provider_hint
    ) == ("openai", "responses", "http")
    assert measurement_module.describe_model(wrapped.inner_model) == (
        "some_other_default",
        "responses",
        "http",
    )


# ---------------------------------------------------------------------------
# Blocking the Daniela instruction overflow
# ---------------------------------------------------------------------------


def test_daniela_1_42m_instructions_blocked_before_provider_nonstreamed(
    published, sentry_calls
):
    model = FakeResponsesHTTPModel([_final()])
    saved_output = {"file_output_id": "74378f97", "rows": 7, "bytes": 2902}
    saved_snapshot = copy.deepcopy(saved_output)
    agent = Agent(
        name="chat_output_specialist",
        instructions="x" * DANIELA_INSTRUCTION_CHARS,
        model=model,
    )

    with cost_scope({"run_id": "flow-run-92c5", "workflow_id": "flow-a781", "job_id": "flow-run-92c5"}):
        with pytest.raises(ModelRequestBlockedError) as raised:
            _run(agent)

    assert model.calls == []  # never reached the provider adapter
    assert saved_output == saved_snapshot
    error = raised.value
    assert error.category == "provider_request_blocked"
    assert error.component == "openai_responses.instructions"
    assert (error.field, error.measured, error.unit) == (
        "instructions",
        DANIELA_INSTRUCTION_CHARS,
        "characters",
    )
    assert error.limit == OPENAI_LIMIT
    assert error.setting == "OPENAI_INSTRUCTIONS_MAX_CHARS"
    message = str(error)
    for fragment in (
        "chat_output_specialist",
        "1,422,809",
        "1,048,576",
        "OPENAI_INSTRUCTIONS_MAX_CHARS",
        "No provider call was made",
        "trace_id=trace-all1279",
        "run_id=flow-run-92c5",
    ):
        assert fragment in message

    assert len(sentry_calls["exceptions"]) == 1
    assert sentry_calls["fingerprints"] == [
        ["payload_contract", "provider_request_blocked", "openai_responses.instructions"]
    ]
    context = dict(sentry_calls["contexts"])["runtime_exception"]
    assert context["measured"] == DANIELA_INSTRUCTION_CHARS
    assert context["limit"] == OPENAI_LIMIT
    assert context["setting"] == "OPENAI_INSTRUCTIONS_MAX_CHARS"
    assert context["field"] == "instructions"
    assert context["agent"] == "chat_output_specialist"
    assert context["run_id"] == "flow-run-92c5"
    assert context["workflow_id"] == "flow-a781"
    assert context["transport"] == "http"
    # Compact diagnosis only: no copy of the instructions in the event.
    assert all(len(str(value)) < 500 for value in context.values())
    tags = dict(sentry_calls["tags"])
    assert tags["failure_category"] == "provider_request_blocked"
    assert "ai_curation.trace.id_hash" in tags
    assert "ai_curation.flow.id_hash" in tags

    [record] = published
    assert record["outcome"] == "blocked_before_send"
    assert record["provider_usage"] == {"status": "not_sent"}
    assert record["outbound"]["instructions"]["chars"] == DANIELA_INSTRUCTION_CHARS


def test_daniela_instructions_blocked_before_provider_streamed(published, sentry_calls):
    model = FakeResponsesHTTPModel([_final()])
    agent = Agent(name="chat_output_specialist", instructions="y" * DANIELA_INSTRUCTION_CHARS, model=model)

    async def consume():
        result = Runner.run_streamed(agent, "Render", run_config=RunConfig(tracing_disabled=True))
        async for _event in result.stream_events():
            pass

    with pytest.raises(ModelRequestBlockedError):
        asyncio.run(consume())

    assert model.calls == []
    assert len(sentry_calls["exceptions"]) == 1
    assert published[0]["outcome"] == "blocked_before_send"


def test_websocket_transport_blocked_and_labelled(published, sentry_calls):
    model = FakeResponsesWSModel([_final()])
    agent = Agent(name="specialist", instructions="z" * (OPENAI_LIMIT + 1), model=model)

    with pytest.raises(ModelRequestBlockedError) as raised:
        _run(agent)

    assert model.calls == []
    assert raised.value.measured == OPENAI_LIMIT + 1
    assert published[0]["transport"] == "websocket"
    assert published[0]["api"] == "responses"


def test_limit_is_configurable(monkeypatch, published, sentry_calls):
    monkeypatch.setenv("OPENAI_INSTRUCTIONS_MAX_CHARS", "2000000")
    model = FakeResponsesHTTPModel([_final()])
    result = _run(Agent(name="specialist", instructions="x" * DANIELA_INSTRUCTION_CHARS, model=model))

    assert result.final_output == "done"
    assert model.calls[0]["instructions"] == "x" * DANIELA_INSTRUCTION_CHARS
    assert sentry_calls["exceptions"] == []


def test_blocked_request_is_not_retried_by_sdk_retry_policy(published, sentry_calls):
    model = FakeResponsesHTTPModel([_final()])
    agent = Agent(
        name="specialist",
        instructions="x" * DANIELA_INSTRUCTION_CHARS,
        model=model,
        model_settings=ModelSettings(
            retry=ModelRetrySettings(max_retries=3, policy=lambda _context: True)
        ),
    )

    with pytest.raises(ModelRequestBlockedError):
        _run(agent)

    assert model.calls == []
    assert [record["attempt"] for record in published] == [1]
    assert len(sentry_calls["exceptions"]) == 1


# ---------------------------------------------------------------------------
# Under-limit scientific input passes unchanged; usage correlation
# ---------------------------------------------------------------------------


def test_under_limit_request_passes_unchanged_with_usage(published, sentry_calls):
    instructions = "Curate expression statements. " * 30_000  # ~900k chars, valid
    model = FakeResponsesHTTPModel([_final(response_id="resp_ok")])

    result = _run(Agent(name="gene_expression", instructions=instructions, model=model))

    assert result.final_output == "done"
    assert model.calls[0]["instructions"] is instructions or model.calls[0]["instructions"] == instructions
    assert sentry_calls["exceptions"] == []
    [record] = published
    assert record["outcome"] == "completed"
    assert record["provider_response_id"] == "resp_ok"
    assert record["provider_usage"] == {
        "status": "reported",
        "source": "provider_response",
        "input_tokens": 1200,
        "cached_input_tokens": 300,
        "output_tokens": 80,
        "reasoning_output_tokens": 20,
        "total_tokens": 1280,
    }
    assert record["outbound"]["instructions"]["chars"] == len(instructions)
    assert record["model_visible"]["estimate_basis"] == "characters_div_4"
    assert record["warnings"] == []


def test_missing_provider_usage_is_not_reported_as_zero(published):
    model = FakeResponsesHTTPModel([ModelResponse(output=[_message("ok")], usage=Usage(), response_id="r")])

    _run(Agent(name="helper", instructions="short", model=model))

    assert published[0]["provider_usage"] == {"status": "not_reported"}


def test_streamed_usage_comes_from_terminal_response(published):
    model = FakeResponsesHTTPModel([_final(response_id="resp_stream_1")])

    async def consume():
        result = Runner.run_streamed(
            Agent(name="specialist", instructions="short", model=model),
            "hi",
            run_config=RunConfig(tracing_disabled=True),
        )
        async for _event in result.stream_events():
            pass

    asyncio.run(consume())

    [record] = published
    assert record["outcome"] == "completed"
    assert record["provider_response_id"] == "resp_stream_1"
    assert record["provider_usage"]["input_tokens"] == 900
    assert record["provider_usage"]["cached_input_tokens"] == 100


# ---------------------------------------------------------------------------
# Later tool-loop turns, retries, tool results
# ---------------------------------------------------------------------------


def _tool_call(name, call_id, arguments="{}"):
    return ModelResponse(
        output=[
            ResponseFunctionToolCall(
                id=f"fc_{call_id}", call_id=call_id, name=name, arguments=arguments, type="function_call"
            )
        ],
        usage=_usage(),
        response_id=f"resp_{call_id}",
    )


def _function_tool(name, output):
    async def invoke(_ctx, _args):
        return output

    return FunctionTool(
        name=name,
        description=f"{name} tool",
        params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
        on_invoke_tool=invoke,
    )


def test_every_tool_loop_turn_is_measured_with_single_large_tool_result(published, sentry_calls, caplog):
    large_result = "r" * 250_000
    model = FakeResponsesHTTPModel([_tool_call("inspect_rows", "call_1"), _final()])
    agent = Agent(
        name="formatter",
        instructions="Format rows",
        model=model,
        tools=[_function_tool("inspect_rows", large_result)],
    )

    with caplog.at_level(logging.WARNING, logger=measurement_module.__name__):
        _run(agent)

    assert len(model.calls) == 2
    first, second = published
    assert first["outbound"]["input"]["tool_results"]["count"] == 0
    tool_results = second["outbound"]["input"]["tool_results"]
    assert tool_results["count"] == 1
    assert tool_results["largest"]["tool_name"] == "inspect_rows"
    assert tool_results["largest"]["chars"] >= 250_000
    assert second["warnings"] == [
        {
            "component": "tool_result",
            "tool_name": "inspect_rows",
            "measured": tool_results["largest"]["chars"],
            "unit": "characters",
            "threshold": 200_000,
            "setting": "MODEL_REQUEST_TOOL_RESULT_WARNING_CHARS",
        }
    ]
    # The large result was sent unchanged (warning only) and nothing was reported to Sentry.
    sent_outputs = [item for item in model.calls[1]["input"] if item.get("type") == "function_call_output"]
    assert sent_outputs[0]["output"] == large_result
    assert sentry_calls["exceptions"] == []
    assert "model request size warning" in caplog.text
    assert large_result not in caplog.text


def test_accumulated_small_tool_results_counted_and_warned(monkeypatch, published):
    monkeypatch.setenv("MODEL_REQUEST_WARNING_ESTIMATED_TOKENS", "50000")
    responses = [_tool_call("inspect_rows", f"call_{index}") for index in range(25)] + [_final()]
    model = FakeResponsesHTTPModel(responses)
    agent = Agent(
        name="formatter",
        instructions="Format rows",
        model=model,
        tools=[_function_tool("inspect_rows", "s" * 10_000)],
    )

    _run(agent, max_turns=30)

    last = published[-1]
    tool_results = last["outbound"]["input"]["tool_results"]
    assert tool_results["count"] == 25
    assert tool_results["chars"] >= 250_000
    assert tool_results["largest"]["chars"] < 200_000
    assert [warning["component"] for warning in last["warnings"]] == ["model_visible_request"]
    assert len(published) == 26


def test_sdk_retry_attempts_are_each_measured(published):
    from openai import APIConnectionError
    import httpx

    failure = APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    model = FakeResponsesHTTPModel([failure, _final()])
    agent = Agent(
        name="validator",
        instructions="Validate",
        model=model,
        model_settings=ModelSettings(
            retry=ModelRetrySettings(max_retries=2, policy=lambda _context: True)
        ),
    )

    _run(agent)

    assert len(model.calls) == 2
    assert [(record["attempt"], record["outcome"]) for record in published] == [
        (1, "provider_error"),
        (2, "completed"),
    ]
    assert published[0]["provider_usage"] == {"status": "not_reported"}


# ---------------------------------------------------------------------------
# Output schema, tool catalog, deferred versus loaded definitions
# ---------------------------------------------------------------------------


class _Row(BaseModel):
    gene: str
    anatomy_terms: list[str]


def test_output_schema_visible_and_deferred_tool_definitions_are_separate(published):
    visible = _function_tool("search_studio_capabilities", "{}")
    deferred = FunctionTool(
        name="get_trace",
        description="Deferred trace tool " + "d" * 5000,
        params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
        on_invoke_tool=lambda *_: "{}",
        defer_loading=True,
    )
    model = FakeResponsesHTTPModel(
        [ModelResponse(output=[_message('{"gene":"unc-54","anatomy_terms":["muscle"]}')], usage=_usage(), response_id="r")]
    )
    agent = Agent(
        name="studio",
        instructions="Help",
        model=model,
        tools=[ToolSearchTool(execution="server"), visible, deferred],
        output_type=_Row,
    )
    loaded = {
        "type": "tool_search_output",
        "id": "tso_1",
        "call_id": "ts_1",
        "status": "completed",
        "execution": "server",
        "tools": [
            {
                "type": "namespace",
                "name": "trace_tools",
                "description": "Trace inspection",
                "tools": [
                    {"type": "function", "name": "get_trace", "parameters": {}},
                    {"type": "function", "name": "get_trace_summary", "parameters": {}},
                ],
            },
            {
                "type": "namespace",
                "name": "flow_tools",
                "description": "Flow inspection",
                "tools": [{"type": "function", "name": "list_flows", "parameters": {}}],
            },
            {"type": "function", "name": "top_level_loaded", "parameters": {}},
        ],
    }

    _run(agent, prompt=[{"role": "user", "content": "trace?"}, loaded])

    [record] = published
    tools = record["outbound"]["tools"]
    assert tools["initially_visible"]["count"] == 2  # tool_search + visible function
    assert tools["deferred"]["count"] == 1
    assert tools["deferred"]["chars"] > 5000
    assert "ToolSearchTool" in tools["hosted_tool_types"]
    assert record["outbound"]["output_schema"]["chars"] > 0
    visible_chars = record["model_visible"]["json_chars"]
    assert record["outbound"]["json_chars"] == visible_chars + tools["deferred"]["chars"]
    assert record["deferred_tools"]["loaded_status"] == "provider_managed"
    # Three namespace members plus one top-level definition, from two namespaces.
    assert record["deferred_tools"]["loaded_definitions_in_input"] == 4
    loaded_measure = record["outbound"]["input"]["loaded_deferred_tool_definitions"]
    assert (loaded_measure["count"], loaded_measure["namespaces"], loaded_measure["items"]) == (4, 2, 1)
    assert 0 < loaded_measure["definition_chars"] < loaded_measure["chars"]
    assert {"component": "hosted_tool_search_loaded_definitions_this_response", "status": "provider_managed"} in record["provider_managed"]


def test_previous_response_history_marked_unobservable():
    record = measurement_module.build_measurement(
        runtime="agents_sdk",
        provider="openai",
        api="responses",
        transport="websocket",
        model="gpt-test",
        instructions="i",
        input_value=[{"role": "user", "content": "next"}],
        previous_response_id="resp_prev",
    )
    assert {"component": "previous_response_history", "status": "unobservable"} in record["provider_managed"]
    assert record["transport_payload_observed"] is False
    assert record["provider_usage"] == {"status": "pending"}


# ---------------------------------------------------------------------------
# Provider-aware limits
# ---------------------------------------------------------------------------


def test_compatible_provider_is_measured_not_blocked_by_openai_field_limit(published, sentry_calls):
    model = FakeChatCompletionsModel([_final()], provider_id="gemini")

    _run(Agent(name="specialist", instructions="g" * DANIELA_INSTRUCTION_CHARS, model=model))

    assert len(model.calls) == 1
    [record] = published
    assert (record["provider"], record["api"]) == ("gemini", "chat_completions")
    assert record["outbound"]["instructions"]["chars"] == DANIELA_INSTRUCTION_CHARS
    assert sentry_calls["exceptions"] == []


# ---------------------------------------------------------------------------
# Direct clients
# ---------------------------------------------------------------------------


def test_direct_responses_request_blocked_before_call(published, sentry_calls):
    calls = []

    async def call(**kwargs):
        calls.append(kwargs)

    with pytest.raises(ModelRequestBlockedError):
        asyncio.run(
            call_measured_direct_request(
                surface="one_shot_helper",
                provider="openai",
                api="responses",
                kwargs={"model": "gpt-test", "instructions": "x" * DANIELA_INSTRUCTION_CHARS, "input": "hi"},
                call=call,
            )
        )

    assert calls == []
    assert published[0]["runtime"] == "direct_client"
    assert published[0]["transport_payload_observed"] is True
    assert len(sentry_calls["exceptions"]) == 1


def test_direct_chat_completion_measured_with_usage(published):
    async def call(**kwargs):
        return SimpleNamespace(
            id="chatcmpl_1",
            usage={"prompt_tokens": 50, "completion_tokens": 7, "total_tokens": 57,
                   "prompt_tokens_details": {"cached_tokens": 0}},
        )

    asyncio.run(
        call_measured_direct_request(
            surface="abstract_extraction",
            provider="openai",
            api="chat_completions",
            kwargs={"model": "gpt-test", "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u" * 4000}]},
            call=call,
        )
    )

    [record] = published
    assert record["outbound"]["input"]["messages_by_role"]["user"]["count"] == 1
    assert record["provider_usage"]["input_tokens"] == 50
    assert record["provider_usage"]["cached_input_tokens"] == 0


def test_safe_client_compaction_request_is_measured(monkeypatch, published):
    from openai.resources.responses import AsyncResponses

    from src.lib.openai_agents.runner import SafeAsyncOpenAI

    sent = []

    async def fake_compact(self, **kwargs):
        sent.append(kwargs)
        return SimpleNamespace(id="cmp_1", output=[], usage=None)

    monkeypatch.setattr(AsyncResponses, "compact", fake_compact, raising=False)
    client = SafeAsyncOpenAI(api_key="test-key")
    asyncio.run(client.responses.compact(model="gpt-test", input=[{"role": "user", "content": "history"}]))

    assert len(sent) == 1
    [record] = published
    assert record["operation"] == "responses.compact"
    assert record["agent_name"] == "standard_chat_compaction"
    assert record["provider_usage"] == {"status": "not_reported"}


# ---------------------------------------------------------------------------
# Sentry: once per failure, wrappers, unavailability
# ---------------------------------------------------------------------------


def test_repeated_block_in_same_trace_and_wrapper_reports_once(published, sentry_calls):
    for _ in range(2):
        model = FakeResponsesHTTPModel([_final()])
        with pytest.raises(ModelRequestBlockedError) as raised:
            _run(Agent(name="chat_output_specialist", instructions="x" * DANIELA_INSTRUCTION_CHARS, model=model))

    # A caller wrapping and re-reporting the violation does not add an event.
    try:
        raise RuntimeError("flow step failed") from raised.value
    except RuntimeError as wrapper:
        observability_runtime.report_runtime_exception(
            wrapper, component="flow_executor", operation="step_failed"
        )

    assert len(sentry_calls["exceptions"]) == 1
    assert [record["outcome"] for record in published] == ["blocked_before_send"] * 2


def test_before_send_drops_log_events_that_repeat_a_captured_failure():
    # No importlib patch here: before_send runs its real enrichment path.
    model = FakeResponsesHTTPModel([_final()])
    with pytest.raises(ModelRequestBlockedError) as raised:
        _run(Agent(name="spec", instructions="x" * DANIELA_INSTRUCTION_CHARS, model=model))
    assert getattr(raised.value, "_ai_curation_sentry_captured", False)

    record = logging.LogRecord(
        "src.lib.openai_agents.streaming_tools", logging.ERROR, __file__, 1,
        "%s stream error: %s", ("spec", raised.value), None,
    )
    assert observability_sentry.before_send({"message": "x"}, {"log_record": record}) is None
    wrapped = RuntimeError("wrapped")
    wrapped.__cause__ = raised.value
    assert observability_sentry.before_send(
        {"message": "x"}, {"exc_info": (RuntimeError, wrapped, None)}
    ) is None
    unrelated = RuntimeError("other failure")
    assert observability_sentry.before_send(
        {"message": "x"}, {"exc_info": (RuntimeError, unrelated, None)}
    ) is not None

    # A distinct error logged with its own exc_info is kept even when the
    # captured violation is also mentioned as a message argument.
    distinct = ValueError("different failure")
    mixed = logging.LogRecord(
        "src.lib.flows.executor", logging.ERROR, __file__, 1,
        "cleanup failed after %s", (raised.value,), (ValueError, distinct, None),
    )
    assert observability_sentry.before_send({"message": "x"}, {"log_record": mixed}) is not None


def test_sentry_unavailable_keeps_original_failure_and_structured_log(monkeypatch, caplog):
    def unavailable(name):
        raise ImportError(name)

    monkeypatch.setattr(observability_runtime.importlib, "import_module", unavailable)
    model = FakeResponsesHTTPModel([_final()])

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ModelRequestBlockedError) as raised:
            _run(Agent(name="spec", instructions="x" * DANIELA_INSTRUCTION_CHARS, model=model))

    assert raised.value.measured == DANIELA_INSTRUCTION_CHARS
    assert "payload contract violation category=provider_request_blocked" in caplog.text
    assert "measured=1422809" in caplog.text
    assert "setting=OPENAI_INSTRUCTIONS_MAX_CHARS" in caplog.text


def test_normal_requests_produce_no_sentry_event(published, sentry_calls):
    model = FakeResponsesHTTPModel([_tool_call("next_page", "c1"), _final()])
    agent = Agent(
        name="formatter",
        instructions="Page through rows",
        model=model,
        tools=[_function_tool("next_page", json.dumps({"rows": [1, 2], "next_cursor": "c2"}))],
    )

    _run(agent)

    assert sentry_calls["exceptions"] == []
    assert all(record["warnings"] == [] for record in published)


# ---------------------------------------------------------------------------
# Runtime entry paths
# ---------------------------------------------------------------------------


def test_one_shot_helper_runner_entry_is_measured(published):
    """hierarchy_resolution, figure_locator_resolution and guardrails use this entry."""

    from src.lib.openai_agents.runner import run_agent_with_owned_openai_resources

    model = FakeResponsesHTTPModel([_final(response_id="resp_hierarchy")])
    agent = Agent(name="hierarchy_resolver", instructions="Resolve headings", model=model)

    result = asyncio.run(run_agent_with_owned_openai_resources(agent, "sections", max_turns=2))

    assert result.final_output == "done"
    [record] = published
    assert record["agent_name"] == "hierarchy_resolver"
    assert record["provider_response_id"] == "resp_hierarchy"
    assert record["activity"] == "background"  # costed_call boundary is correlated


def test_agent_studio_run_measures_visible_and_deferred_tools(monkeypatch, published):
    from src.lib.agent_studio import openai_runtime as studio

    model = FakeResponsesHTTPModel([_final(text="Here is the plan", response_id="resp_studio")])
    provider = SimpleNamespace(get_model=lambda _name: model)
    monkeypatch.setattr(
        studio,
        "build_owned_openai_responses_resources",
        lambda: SimpleNamespace(provider=provider),
    )
    monkeypatch.setattr(
        studio,
        "_run_config",
        lambda **_kwargs: RunConfig(model_provider=provider, tracing_disabled=True),
    )

    async def close(*_args, **_kwargs):
        return None

    monkeypatch.setattr(studio, "close_owned_openai_resources", close)
    definitions = [
        {"name": "search_studio_capabilities", "description": "Search", "input_schema": {"type": "object", "properties": {}}},
        {"name": "get_trace_summary", "description": "Trace " + "t" * 3000, "input_schema": {"type": "object", "properties": {}}},
        {"name": "list_flows", "description": "Flows " + "f" * 3000, "input_schema": {"type": "object", "properties": {}}},
    ]

    async def executor(*_args):
        raise AssertionError("no tool call expected")

    state = studio.AgentStudioRunState(trace_id="studio-trace")
    tools, counts = studio.build_agent_studio_tools(
        definitions,
        executor=executor,
        state=state,
        namespace_for_tool=lambda name: ("studio_tools", "Studio tools"),
        eager_tool_names=frozenset({"search_studio_capabilities"}),
    )

    async def collect():
        return [
            event
            async for event in studio.stream_agent_studio_run(
                instructions="Help the curator",
                input_items=[{"role": "user", "content": "What changed?"}],
                tools=tools,
                state=state,
                session_id="studio-session",
                user_id="curator",
                max_turns=2,
                model_settings=studio.build_agent_studio_model_settings(max_output_tokens=100),
            )
        ]

    asyncio.run(collect())

    assert counts["deferred_count"] == 2
    [record] = published
    measured_tools = record["outbound"]["tools"]
    assert measured_tools["deferred"]["count"] == 2
    assert measured_tools["initially_visible"]["count"] == 2  # tool_search + eager search tool
    assert measured_tools["deferred"]["chars"] > 6000
    assert record["model_visible"]["json_chars"] < record["outbound"]["json_chars"]
    assert record["agent_id"] == "agent_studio_authoring"
    assert record["activity"] == "authoring"


def test_measurement_event_and_log_carry_sizes_not_content(monkeypatch, caplog):
    from src.lib.openai_agents import extraction_trace_events

    events = []
    monkeypatch.setattr(
        extraction_trace_events,
        "write_extraction_trace_event",
        lambda **kwargs: events.append(kwargs),
    )
    secret_instructions = "UNIQUE-INSTRUCTION-TEXT " * 100
    secret_input = "UNIQUE-EVIDENCE-QUOTE " * 100
    model = FakeResponsesHTTPModel([_final()])

    with caplog.at_level(logging.INFO, logger=measurement_module.__name__):
        _run(Agent(name="specialist", instructions=secret_instructions, model=model), prompt=secret_input)

    [event] = events
    assert event["event_type"] == "runtime.model_request_measurement"
    assert event["trace_id"] == "trace-all1279"
    serialized = json.dumps(event, default=str) + caplog.text + json.dumps(
        [getattr(record, "model_request_measurement", None) for record in caplog.records],
        default=str,
    )
    assert "UNIQUE-INSTRUCTION-TEXT" not in serialized
    assert "UNIQUE-EVIDENCE-QUOTE" not in serialized
    assert event["input_summary"]["outbound"]["instructions"]["chars"] == len(secret_instructions)


def test_flatten_loaded_tool_definitions_counts_namespace_members():
    flatten = measurement_module.flatten_loaded_tool_definitions
    assert flatten(None) == []
    assert [tool["name"] for tool in flatten([
        {"type": "namespace", "name": "a", "description": "A", "tools": [
            {"type": "function", "name": "one"}, {"type": "custom", "name": "two"},
        ]},
        {"type": "namespace", "name": "empty", "description": "E", "tools": []},
        {"type": "function", "name": "three"},
    ])] == ["one", "two", "three"]
