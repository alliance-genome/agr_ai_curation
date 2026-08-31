from types import SimpleNamespace

import pytest

from src.lib.openai_agents.benchmark_routing import (
    BenchmarkTelemetryProvider,
    attach_benchmark_route,
    benchmark_route_kwargs,
    benchmark_route_plan,
    reset_benchmark_invocation_route,
    set_benchmark_invocation_route,
)
from src.lib.openai_agents.provider_usage import capture_provider_usage


def test_route_plan_resolves_each_model_bearing_slot_independently():
    routes = {
        "supervisor": {
            "provider": "openai",
            "model": "supervisor-model",
            "reasoning_effort": "high",
        },
        "agent:extractor": {
            "provider": "openrouter",
            "model": "extractor-model",
            "reasoning_effort": "low",
        },
    }

    with benchmark_route_plan(routes):
        assert benchmark_route_kwargs("supervisor") == {
            "model_id_override": "supervisor-model",
            "model_provider_override": "openai",
            "model_reasoning_override": "high",
            "benchmark_route_slot": "supervisor",
        }
        assert benchmark_route_kwargs("agent:extractor")["model_id_override"] == (
            "extractor-model"
        )
        with pytest.raises(ValueError, match="no slot 'validator:deterministic'"):
            benchmark_route_kwargs("validator:deterministic")

    assert benchmark_route_kwargs("supervisor") == {}


def test_attach_route_preserves_runtime_identity_on_agent_and_model():
    model = SimpleNamespace()
    agent = SimpleNamespace(model=model)
    with benchmark_route_plan(
        {
            "validator:ontology": {
                "provider": "openrouter",
                "model": "validator-model",
                "reasoning_effort": "medium",
            }
        }
    ):
        attach_benchmark_route(agent, "validator:ontology")

    assert agent.benchmark_route_slot == "validator:ontology"
    assert model._benchmark_requested_provider == "openrouter"
    assert model._benchmark_requested_model == "validator-model"
    assert model._benchmark_reasoning_effort == "medium"


def test_attach_route_rejects_unattachable_concrete_model():
    class ImmutableModel:
        __slots__ = ()

    agent = SimpleNamespace(model=ImmutableModel())
    with benchmark_route_plan(
        {
            "agent:extractor": {
                "provider": "openrouter",
                "model": "extractor-model",
                "reasoning_effort": "high",
            }
        }
    ):
        with pytest.raises(AttributeError, match="_benchmark_route_slot"):
            attach_benchmark_route(agent, "agent:extractor")


@pytest.mark.asyncio
async def test_native_provider_proxy_reserves_sequence_at_each_call_boundary():
    class FakeModel:
        async def get_response(self, *args, **kwargs):
            return {
                "model": "native-actual",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }

        async def stream_response(self, *args, **kwargs):
            yield SimpleNamespace(
                response={
                    "model": "native-stream-actual",
                    "usage": {"input_tokens": 4, "output_tokens": 5},
                }
            )

        def get_retry_advice(self, request):
            return None

        async def close(self):
            return None

    class FakeProvider:
        def get_model(self, model_name):
            return model

        async def aclose(self):
            return None

    model = FakeModel()
    provider = BenchmarkTelemetryProvider(FakeProvider())
    agent = SimpleNamespace(model="native-requested")
    routes = {
        "supervisor": {
            "provider": "openai",
            "model": "native-requested",
            "reasoning_effort": "high",
        }
    }
    with benchmark_route_plan(routes), capture_provider_usage(
        max_records=3, max_failure_detail_chars=20
    ) as records:
        attach_benchmark_route(agent, "supervisor")
        token = set_benchmark_invocation_route(agent)
        try:
            proxied_model = provider.get_model("native-requested")
            await proxied_model.get_response()
            await proxied_model.get_response()
            async for _ in proxied_model.stream_response():
                pass
        finally:
            reset_benchmark_invocation_route(token)

    assert [record.sequence for record in records] == [1, 2, 3]
    assert [record.actual_model for record in records] == [
        "native-actual",
        "native-actual",
        "native-stream-actual",
    ]
    assert [record.total_tokens for record in records] == [5, 5, 9]
