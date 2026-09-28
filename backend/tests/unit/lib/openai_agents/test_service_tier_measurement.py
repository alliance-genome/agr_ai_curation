"""Raw provider evidence survives SDK normalization; no provider calls."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from agents import Agent, ModelSettings, RunConfig, Runner
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI

from src.lib.cost_ledger import runtime_writes
from src.lib.openai_agents import model_request_measurement as measurement


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["responses", "responses.compact", "chat_completions"])
@pytest.mark.parametrize(
    "tier_kwargs, requested",
    [
        ({"service_tier": "flex"}, "flex"),
        ({"service_tier": "flex", "extra_body": {"service_tier": "priority"}}, "priority"),
        ({"extra_body": {"service_tier": "flex"}}, "flex"),
        ({"service_tier": "flex", "extra_body": {}}, "flex"),
        ({"service_tier": "flex", "extra_body": {"service_tier": None}}, None),
        ({"service_tier": " "}, None),
        ({"service_tier": 123}, None),
        ({}, None),
    ],
)
@pytest.mark.parametrize("reported", ["priority", None])
async def test_direct_request_preserves_tiers(monkeypatch, api, tier_kwargs, requested, reported):
    sink = Mock()
    reserved_tiers = []

    def reserve(record):
        reserved_tiers.append(record.get("requested_service_tier"))
        return sink

    monkeypatch.setattr(runtime_writes, "reserve_runtime_request", reserve)
    response = SimpleNamespace(
        id="direct_test",
        usage={"input_tokens": 7, "output_tokens": 8, "total_tokens": 15},
    )
    if reported is not None:
        response.service_tier = reported
    call = AsyncMock(return_value=response)
    input_key = "messages" if api == "chat_completions" else "input"
    kwargs = {"model": "test", input_key: [], **tier_kwargs}

    result = await measurement.call_measured_direct_request(
        surface="tier test", provider="openai", api=api, kwargs=kwargs, call=call,
    )

    assert result is response
    call.assert_awaited_once_with(**kwargs)
    assert reserved_tiers == [requested]
    sink.finish.assert_called_once()
    assert sink.finish.call_args.kwargs["service_tiers"] == {"requested": requested, "effective": reported}
    usage = sink.finish.call_args.kwargs["usage"]
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (7, 8, 15)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("provider failed"), asyncio.CancelledError()])
async def test_direct_request_failure_preserves_requested_tier(monkeypatch, error):
    sink = Mock()
    monkeypatch.setattr(runtime_writes, "reserve_runtime_request", Mock(return_value=sink))
    call = AsyncMock(side_effect=error)
    with pytest.raises(type(error)) as caught:
        await measurement.call_measured_direct_request(
            surface="tier test", provider="openai", api="responses",
            kwargs={"model": "test", "input": [], "service_tier": "flex"}, call=call,
        )
    assert caught.value is error
    call.assert_awaited_once()
    sink.finish.assert_called_once()
    assert sink.finish.call_args.kwargs["service_tiers"] == {"requested": "flex", "effective": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["responses", "chat", "policy_chat"])
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("reported", ["default", None])
@pytest.mark.parametrize("details", [True, False])
async def test_raw_tier_survives_normalization_without_inference(monkeypatch, api, streamed, reported, details):
    measurement.install_model_request_measurement()
    monkeypatch.setenv("LLM_DISABLED_PROVIDERS", "")
    sink = Mock()
    reserve = Mock(return_value=sink)
    monkeypatch.setattr(runtime_writes, "reserve_runtime_request", reserve)
    seen = []
    def respond(request):
        seen.append(json.loads(request.content))
        if api == "responses":
            payload = {
                "id": "resp_test", "object": "response", "created_at": 1,
                "model": "test", "status": "completed", "parallel_tool_calls": False,
                "tool_choice": "auto", "tools": [],
                "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": "done", "annotations": []}]}],
                "usage": {"input_tokens": 7, "output_tokens": 8, "total_tokens": 15,
                          "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
            }
        else:
            payload = {
                "id": "chat_test", "object": "chat.completion.chunk" if streamed else "chat.completion",
                "created": 1, "model": "test",
                "choices": [{"index": 0, "delta" if streamed else "message":
                             {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 8, "total_tokens": 15},
            }
        if reported is not None:
            payload["service_tier"] = reported
        if api == "responses":
            if details:
                payload["usage"]["input_tokens_details"]["cache_write_tokens"] = 3
            else:
                payload["usage"]["input_tokens_details"] = {}
                payload["usage"]["output_tokens_details"] = {}
        elif details:
            payload["usage"]["prompt_tokens_details"] = {"cached_tokens": 0, "cache_write_tokens": 3}
            payload["usage"]["completion_tokens_details"] = {"reasoning_tokens": 0}
        if streamed:
            event = {"type": "response.completed", "sequence_number": 1, "response": payload} if api == "responses" else payload
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json=payload)

    async with AsyncOpenAI(api_key="fixture", http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
        model_type = OpenAIResponsesModel if api == "responses" else OpenAIChatCompletionsModel
        model = model_type(model="test", openai_client=client)
        if api == "policy_chat":
            from src.lib.openai_agents.provider_model import ProviderConfiguredChatCompletionsModel
            model = ProviderConfiguredChatCompletionsModel(
                model="test", openai_client=client, provider_id="openai",
                request_extra_body={"service_tier": "priority"}, request_headers={},
                forbidden_request_fields=(), omit_usage_request=False, telemetry_adapter=None,
                disable_model_retries=False,
            )
        agent = Agent(name="tier test", model=model,
                      model_settings=ModelSettings(extra_args={"service_tier": "flex"}))
        config = RunConfig(tracing_disabled=True)
        if streamed:
            result = Runner.run_streamed(agent, "short", run_config=config)
            async for _ in result.stream_events():
                pass
        else:
            await Runner.run(agent, "short", run_config=config)
    requested = "priority" if api == "policy_chat" else "flex"
    assert seen[0]["service_tier"] == requested
    reserve.assert_called_once()
    assert sum("service_tiers" in call.kwargs for call in sink.finish.call_args_list) == 1
    assert sink.finish.call_args.kwargs["service_tiers"] == {"requested": requested, "effective": reported}
    usage = sink.finish.call_args.kwargs["usage"]
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (7, 8, 15)
    assert usage.cache_read_tokens == (0 if details else None)
    assert usage.cache_write_tokens == (3 if details else None)
    assert usage.reasoning_tokens == (0 if details else None)
    assert measurement._provider_measurement.get() is None


@pytest.mark.asyncio
async def test_raw_streams_isolate_tiers_and_delegate_close():
    closed = []
    async def source(tier):
        try:
            await asyncio.sleep(0)
            yield SimpleNamespace(service_tier=tier)
        finally:
            closed.append(tier)
    async def consume(tier):
        record = {}
        stream = measurement._TierObservedStream(source(tier), record)
        item = await anext(stream)
        assert item.service_tier == tier
        await stream.aclose()
        return record
    rows = await asyncio.gather(consume("flex"), consume("priority"))
    assert rows == [{"effective_service_tier": "flex"}, {"effective_service_tier": "priority"}]
    assert sorted(closed) == ["flex", "priority"]


def test_requested_extra_body_overrides_extra_args():
    settings = ModelSettings(extra_args={"service_tier": "priority"}, extra_body={"service_tier": "flex"})
    assert measurement._requested_tier(settings) == "flex"
    assert measurement._requested_tier(ModelSettings()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_websocket_override_preserves_reported_tier(monkeypatch, streamed):
    from agents.models.openai_responses import OpenAIResponsesWSModel
    from openai.types.responses import Response, ResponseCompletedEvent
    measurement.install_model_request_measurement()
    sink = Mock()
    monkeypatch.setattr(runtime_writes, "reserve_runtime_request", Mock(return_value=sink))
    payload = Response.model_validate({
        "id": "resp_ws", "object": "response", "created_at": 1, "model": "test",
        "status": "completed", "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
        "service_tier": "priority", "output": [],
        "usage": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7,
                  "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
    })
    async def events(_kwargs):
        yield ResponseCompletedEvent(type="response.completed", sequence_number=1, response=payload)
    async with AsyncOpenAI(api_key="fixture") as client:
        model = OpenAIResponsesWSModel(model="test", openai_client=client)
        monkeypatch.setattr(model, "_iter_websocket_response_events", events)
        agent = Agent(name="websocket tier", model=model, model_settings=ModelSettings(extra_args={"service_tier": "fast"}))
        if streamed:
            result = Runner.run_streamed(agent, "short", run_config=RunConfig(tracing_disabled=True))
            async for _ in result.stream_events():
                pass
        else:
            await Runner.run(agent, "short", run_config=RunConfig(tracing_disabled=True))
    assert sink.finish.call_args.kwargs["service_tiers"] == {"requested": "fast", "effective": "priority"}
    usage = sink.finish.call_args.kwargs["usage"]
    assert usage.output_tokens == 0
    assert usage.cache_read_tokens == 0
    assert usage.reasoning_tokens == 0
    assert usage.cache_write_tokens is None
