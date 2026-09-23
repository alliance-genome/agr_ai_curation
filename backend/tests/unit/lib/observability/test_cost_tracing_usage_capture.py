"""ALL-1288: every model turn's observation carries model and usage, or says why not.

Runs the pinned Agents SDK model adapter (fake transport, no network) through the
production CostTracingProcessor + OpenInference tracer configuration into an
in-memory OpenTelemetry exporter that applies the default span attribute limit.
"""

from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest
from agents import (
    Agent,
    ModelSettings,
    RunConfig,
    Runner,
    generation_span,
    set_trace_processors,
    trace,
)
from agents.models.openai_responses import OpenAIResponsesModel
from agents.retry import ModelRetrySettings
from openai import APIConnectionError, AsyncOpenAI
from openai.types.responses import Response, ResponseCompletedEvent
from openinference.instrumentation import OITracer
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import src.lib.openai_agents.runner  # noqa: F401  (installs request measurement)
from src.lib.observability.cost_tracing import CostTracingProcessor, openinference_trace_config
from src.lib.openai_agents import model_request_measurement as measurement_module
from src.lib.openai_agents import streaming_tools

USAGE = {
    "input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200,
    "input_tokens_details": {"cached_tokens": 600},
    "output_tokens_details": {"reasoning_tokens": 150},
}
EXCLUSIVE = {"input": 400, "input_cached_tokens": 600, "output": 50, "output_reasoning_tokens": 150}


class _SessionAttributes(SpanProcessor):
    """Stand-in for trace identity the Langfuse exporter adds at span start."""

    def on_start(self, span, parent_context=None):
        span.set_attribute("session.id", "session-A")


@pytest.fixture
def exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(_SessionAttributes())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = OITracer(provider.get_tracer("cost-test"), config=openinference_trace_config())
    set_trace_processors([CostTracingProcessor(tracer)])
    try:
        yield exporter
    finally:
        set_trace_processors([])
        provider.shutdown()


@pytest.fixture
def published(monkeypatch):
    records: list[dict] = []
    original = measurement_module._publish

    def capture(measurement):
        original(measurement)
        records.append({k: copy.deepcopy(v) for k, v in measurement.items() if not k.startswith("_")})

    monkeypatch.setattr(measurement_module, "_publish", capture)
    return records


def _response(*, response_id="resp_1", usage=USAGE, tools=(), output_messages=1):
    return Response.model_validate({
        "id": response_id, "created_at": 1, "object": "response", "model": "gpt-test-2026-09",
        "status": "completed", "parallel_tool_calls": False, "tool_choice": "auto",
        "tools": [
            {"type": "function", "name": name, "parameters": {"type": "object", "properties": {}},
             "strict": False}
            for name in tools
        ],
        "output": [
            {
                "id": f"msg_{index}", "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "done", "annotations": []}],
            }
            for index in range(1, output_messages + 1)
        ],
        "usage": usage,
    })


async def _events(response):
    yield ResponseCompletedEvent(type="response.completed", sequence_number=0, response=response)


class _FakeResponsesModel(OpenAIResponsesModel):
    """The real SDK Responses adapter over a scripted transport."""

    def __init__(self, script):
        super().__init__(model="gpt-test", openai_client=AsyncOpenAI(api_key="test-key"))
        self._script = list(script)

    async def _fetch_response(self, *args, stream=False, **kwargs):
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step()
        return _events(step) if stream else step


def _agent(model, **settings):
    return Agent(name="Extractor", instructions="Extract", model=model,
                 model_settings=ModelSettings(**settings))


def _run(agent, prompt="Extract", **config):
    return asyncio.run(Runner.run(agent, prompt, run_config=RunConfig(tracing_disabled=False, **config)))


def _run_streamed(agent, prompt="Extract", **config):
    async def consume():
        result = Runner.run_streamed(agent, prompt, run_config=RunConfig(tracing_disabled=False, **config))
        async for _event in result.stream_events():
            pass

    asyncio.run(consume())


def _generations(exporter):
    return [span for span in exporter.get_finished_spans() if span.name in {"response", "generation"}]


def _cost(span):
    return json.loads(span.attributes["metadata"])["cost_context"]


def _assert_recorded(span, record):
    assert span.attributes["llm.model_name"] == "gpt-test-2026-09"
    assert json.loads(span.attributes["langfuse.observation.usage_details"]) == EXCLUSIVE
    cost = _cost(span)
    assert cost["usage_status"] == "recorded"
    assert cost["attempt_outcome"] == "success"
    assert cost["model_request_id"] == record["measurement_id"]
    assert cost["provider_response_id"] == record["provider_response_id"]
    assert record["provider_usage"]["input_tokens"] == 1000


def _assert_no_usage(span):
    assert "langfuse.observation.usage_details" not in span.attributes
    assert not [key for key in span.attributes if key.startswith(("llm.token_count", "gen_ai.usage"))]


def test_long_streamed_turn_keeps_model_usage_and_cost_context(exporter, published):
    # Production: 695 of 712 usage-less generations were completed turns with
    # 31+ history items whose flattened messages evicted everything else.
    history = [{"role": "user", "content": f"history item {index}"} for index in range(80)]
    _run_streamed(_agent(_FakeResponsesModel([_response()])), history)

    [span] = _generations(exporter)
    _assert_recorded(span, published[0])
    assert span.attributes["session.id"] == "session-A"
    assert len(json.loads(span.attributes["input.value"])) == 80
    assert not [key for key in span.attributes if key.startswith("llm.input_messages")]


def test_cost_attributes_survive_when_other_payload_overflows_the_span(exporter, published):
    tools = [f"tool_{index}" for index in range(150)]
    _run_streamed(_agent(_FakeResponsesModel([_response(tools=tools)])))

    [span] = _generations(exporter)
    assert span.dropped_attributes > 0
    _assert_recorded(span, published[0])
    assert span.attributes["session.id"] == "session-A"


def test_many_output_items_keep_output_value_and_cost(exporter, published):
    # Flattened output copies (several attributes per item) must not evict
    # output.value; the full response is already exported there.
    _run_streamed(_agent(_FakeResponsesModel([_response(output_messages=60)])))

    [span] = _generations(exporter)
    _assert_recorded(span, published[0])
    assert "output.value" in span.attributes
    assert not [key for key in span.attributes if key.startswith("llm.output_messages")]


def test_non_streamed_turn_records_model_and_usage(exporter, published):
    _run(_agent(_FakeResponsesModel([_response()])))

    [span] = _generations(exporter)
    _assert_recorded(span, published[0])


def test_payload_free_trace_records_usage_from_the_sdk_span(exporter, published):
    _run_streamed(_agent(_FakeResponsesModel([_response()])), trace_include_sensitive_data=False)

    [span] = _generations(exporter)
    cost = _cost(span)
    assert cost["usage_status"] == "recorded"
    assert cost["model_request_id"] == published[0]["measurement_id"]
    assert span.attributes["llm.model_name"] == "gpt-test"
    assert json.loads(span.attributes["langfuse.observation.usage_details"]) == EXCLUSIVE


def test_cancelled_stream_records_explicit_cancelled_status(exporter, published):
    started = asyncio.Event()

    async def hanging():
        started.set()
        await asyncio.Event().wait()
        yield  # pragma: no cover

    async def consume():
        result = Runner.run_streamed(
            _agent(_FakeResponsesModel([hanging])), "Extract",
            run_config=RunConfig(tracing_disabled=False),
        )

        async def drain():
            async for _event in result.stream_events():
                pass

        drainer = asyncio.create_task(drain())
        await asyncio.wait_for(started.wait(), 5)
        result.cancel()
        await asyncio.wait_for(drainer, 5)

    asyncio.run(consume())

    [span] = _generations(exporter)
    cost = _cost(span)
    assert cost["usage_status"] == "cancelled"
    assert cost["attempt_outcome"] == "cancelled"
    assert cost["requested_model"] == "gpt-test"
    [record] = published
    assert record["outcome"] == "cancelled"
    assert cost["model_request_id"] == record["measurement_id"]
    assert "llm.model_name" not in span.attributes
    _assert_no_usage(span)


def test_retried_turn_records_failed_attempt_and_usage_once(exporter, published):
    failure = APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    agent = _agent(
        _FakeResponsesModel([failure, _response()]),
        retry=ModelRetrySettings(max_retries=2, policy=lambda _context: True),
    )
    _run_streamed(agent)

    failed, completed = sorted(_generations(exporter), key=lambda span: span.start_time)
    assert [(record["attempt"], record["outcome"]) for record in published] == [
        (1, "provider_error"), (2, "completed"),
    ]
    failed_cost = _cost(failed)
    assert failed_cost["usage_status"] == "failed"
    assert failed_cost["attempt_outcome"] == "error"
    assert failed_cost["attempt"] == 1
    assert failed_cost["model_request_id"] == published[0]["measurement_id"]
    _assert_no_usage(failed)
    _assert_recorded(completed, published[1])
    assert _cost(completed)["attempt"] == 2
    assert failed_cost["attempt_id"] != _cost(completed)["attempt_id"]


def test_provider_omitted_usage_is_explicit_not_zero(exporter, published):
    _run(_agent(_FakeResponsesModel([_response(usage=None)])))

    [span] = _generations(exporter)
    cost = _cost(span)
    assert cost["usage_status"] == "provider_omitted"
    assert cost["attempt_outcome"] == "success"
    assert published[0]["provider_usage"] == {"status": "not_reported"}
    _assert_no_usage(span)


def test_chat_completions_zero_usage_substitute_is_provider_omitted(exporter):
    # The SDK stores Usage() zeros (requests=0) when a Chat Completions provider omits usage.
    zeros = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
             "input_tokens_details": {"cached_tokens": 0},
             "output_tokens_details": {"reasoning_tokens": 0}}
    with trace("chat-completions"):
        with generation_span(model="compatible-model", output=[{"role": "assistant"}], usage=zeros):
            pass

    [span] = _generations(exporter)
    assert _cost(span)["usage_status"] == "provider_omitted"
    _assert_no_usage(span)


@pytest.mark.parametrize("enabled", [True, False])
def test_specialist_without_parent_config_follows_tracing_state(monkeypatch, enabled):
    monkeypatch.setattr(streaming_tools, "is_openai_agents_tracing_enabled", lambda: enabled)

    config = streaming_tools._run_config_with_full_trace_payloads(None)

    assert config.tracing_disabled is (not enabled)
    assert config.trace_include_sensitive_data is True
