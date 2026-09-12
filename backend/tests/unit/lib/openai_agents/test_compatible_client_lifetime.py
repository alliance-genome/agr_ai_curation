"""Real, loopback-only HTTP coverage for synchronous validator client ownership."""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from agents import Agent, ModelSettings, RunConfig, Runner
from agents.models.openai_responses import OpenAIResponsesModel, OpenAIResponsesWSModel
from openai import AsyncOpenAI, DefaultAsyncHttpxClient, InternalServerError
from src.lib.openai_agents import provider_model, runner
from src.lib.openai_agents.benchmark_routing import (
    attach_benchmark_route,
    benchmark_route_plan,
)
from src.lib.openai_agents.provider_model import ProviderConfiguredChatCompletionsModel


@pytest.fixture
def completion_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((body, dict(self.headers)))
            # A real pending read binds the pooled connection to its owner loop.
            time.sleep(0.025)
            response = json.dumps({
                "id": "synthetic-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "synthetic-model",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": "complete",
                }}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            }).encode()
            self.send_response(int(self.headers.get("X-Response-Status", "200")))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def make_agent(client, model_type=ProviderConfiguredChatCompletionsModel):
    model = model_type(
        model="synthetic-model", openai_client=client, provider_id="compatible-test",
        request_extra_body={"policy": {"enabled": True}},
        request_headers={"X-Policy": "preserved"}, forbidden_request_fields=(),
        omit_usage_request=False, telemetry_adapter=None, disable_model_retries=True,
    )
    return Agent(name="validator", model=model, model_settings=ModelSettings(temperature=0))


def test_main_loop_and_parallel_sync_validators_do_not_share_transport(completion_server):
    url, requests = completion_server

    async def exercise():
        async with AsyncOpenAI(
            api_key="synthetic", base_url=url, max_retries=0,
            http_client=DefaultAsyncHttpxClient(timeout=2),
            default_headers={"X-Caller": "preserved"},
        ) as client:
            agent = make_agent(client)
            original_model = agent.model
            config = RunConfig(tracing_disabled=True)
            assert (await Runner.run(agent, "warm", run_config=config)).final_output == "complete"

            def validators():
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(
                        copy_context().run, runner.run_agent_sync_with_owned_openai_resources,
                        agent, input="validate", max_turns=1, run_config=config,
                    ) for _ in range(2)]
                    return [future.result() for future in futures]

            results = await asyncio.to_thread(validators)
            assert [result.final_output for result in results] == ["complete", "complete"]
            assert agent.model is original_model
            assert agent.model._client is client
            assert not client.is_closed()
            assert (await Runner.run(agent, "after", run_config=config)).final_output == "complete"

    asyncio.run(exercise())
    assert len(requests) == 4
    for body, headers in requests:
        assert body["model"] == "synthetic-model"
        assert body["temperature"] == 0
        assert body["policy"] == {"enabled": True}
        assert headers["X-Policy"] == "preserved"
        assert headers["X-Caller"] == "preserved"


@pytest.mark.parametrize("synchronous", [False, True])
@pytest.mark.parametrize("status", [200, 503])
def test_owned_client_closes_on_its_run_loop_and_preserves_policy(
    monkeypatch, completion_server, synchronous, status,
):
    url, requests = completion_server
    lifetimes = []
    models = []
    telemetry = []

    class TrackingClient(AsyncOpenAI):
        def copy(self, **kwargs):
            clone = super().copy(**kwargs)
            assert type(clone) is TrackingClient
            assert clone.max_retries == self.max_retries == 0
            assert clone.timeout == self.timeout
            assert clone.base_url == self.base_url
            assert clone.api_key == self.api_key
            assert clone.default_query == self.default_query
            lifetimes.append(("create", clone, asyncio.get_running_loop()))
            return clone

        async def close(self):
            lifetimes.append(("close", self, asyncio.get_running_loop()))
            await super().close()

    class TrackingModel(ProviderConfiguredChatCompletionsModel):
        async def _fetch_response(self, *args, **kwargs):
            models.append((self, asyncio.get_running_loop()))
            return await super()._fetch_response(*args, **kwargs)

    # Use the real configured-model behavior, including invocation recording.
    monkeypatch.setattr(provider_model, "begin_provider_invocation", lambda **kw: telemetry.append(kw))
    monkeypatch.setattr(provider_model, "complete_provider_invocation", lambda *_a, **_kw: telemetry.append("complete"))
    monkeypatch.setattr(provider_model, "fail_provider_invocation", lambda *_a, **_kw: telemetry.append("failed"))
    client = TrackingClient(
        api_key="synthetic", base_url=url, max_retries=0, timeout=2,
        default_headers={"X-Response-Status": str(status)},
        default_query={"caller": "preserved"},
    )
    # A real subclass with added behavior must not be rebuilt as an SDK base.
    agent = make_agent(client, TrackingModel)
    original_model = agent.model
    slot = "validator:synthetic"
    with benchmark_route_plan({slot: {
        "provider": "compatible-test", "model": "synthetic-model", "reasoning_effort": None,
    }}):
        attach_benchmark_route(agent, slot)

    async def invoke():
        return await runner.run_agent_with_owned_openai_resources(
            agent, "validate", max_turns=1, run_config=RunConfig(tracing_disabled=True),
        )

    def run():
        if synchronous:
            return runner.run_agent_sync_with_owned_openai_resources(
                agent, input="validate", max_turns=1, run_config=RunConfig(tracing_disabled=True),
            )
        return asyncio.run(invoke())

    try:
        if status == 503:
            with pytest.raises(InternalServerError):
                run()
        else:
            assert run().final_output == "complete"
        assert len(requests) == 1  # No transport or model retry was introduced.
        assert [item[0] for item in lifetimes] == ["create", "close"]
        assert lifetimes[0][1] is lifetimes[1][1]
        assert lifetimes[0][2] is lifetimes[1][2] is models[0][1]
        assert lifetimes[0][1].is_closed()
        assert type(models[0][0]) is TrackingModel
        assert models[0][0] is not original_model
        assert models[0][0]._benchmark_route_slot == slot
        assert models[0][0]._disable_model_retries is True
        assert agent.model is original_model and original_model._client is client
        assert not client.is_closed()
        assert telemetry[0]["route_slot"] == slot
        assert telemetry[0]["requested_model"] == "synthetic-model"
        assert telemetry[0]["requested_provider"] == "compatible-test"
        assert telemetry[0]["reasoning_effort"] is None
        assert telemetry[1:] == ["failed" if status == 503 else "complete"]
    finally:
        asyncio.run(client.close())


@pytest.mark.asyncio
async def test_cancellation_closes_only_owned_client_and_keeps_agent_attributes(monkeypatch):
    client = AsyncOpenAI(api_key="synthetic", max_retries=0)
    agent = make_agent(client)
    agent.benchmark_route_slot = "validator:synthetic"
    agent.benchmark_requested_provider = "compatible-test"
    agent.benchmark_requested_model = "synthetic-model"
    captured = []

    async def cancelled(owned, *_args, **_kwargs):
        assert owned is not agent and owned.model is not agent.model
        assert owned.model_settings is agent.model_settings
        assert owned.benchmark_route_slot == agent.benchmark_route_slot
        assert owned.benchmark_requested_provider == agent.benchmark_requested_provider
        captured.append(owned.model._client)
        raise asyncio.CancelledError()

    monkeypatch.setattr(runner.Runner, "run", cancelled)
    try:
        with pytest.raises(asyncio.CancelledError):
            await runner.run_agent_with_owned_openai_resources(agent, "input", max_turns=1)
        assert captured[0].is_closed()
        assert not client.is_closed()
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("model_type", [OpenAIResponsesModel, OpenAIResponsesWSModel])
async def test_http_responses_owned_but_websocket_lifecycle_untouched(monkeypatch, model_type):
    async with AsyncOpenAI(api_key="synthetic") as client:
        agent = Agent(name="validator", model=model_type("synthetic-model", client))
        captured = []

        async def run(owned, *_args, **_kwargs):
            captured.append(owned)
            return "complete"

        monkeypatch.setattr(runner.Runner, "run", run)
        assert await runner.run_agent_with_owned_openai_resources(agent, "input", max_turns=1) == "complete"
        assert not client.is_closed()
        if model_type is OpenAIResponsesModel:
            assert captured[0] is not agent
            assert type(captured[0].model) is model_type
            assert captured[0].model._client.is_closed()
            assert captured[0].model._client is not client
        else:
            assert captured[0] is agent
