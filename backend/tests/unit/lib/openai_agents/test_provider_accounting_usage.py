"""Canonical token facts must not inherit legacy display defaults."""

from unittest.mock import Mock

from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails
import pytest

from src.lib.cost_ledger.facts import TokenUsage
from src.lib.openai_agents.provider_usage import (
    begin_provider_invocation, capture_provider_usage,
    complete_generic_provider_invocation, fail_provider_invocation,
    normalize_openrouter_usage, observe_provider_invocations, provider_usage_metadata,
)


def capture(monkeypatch, usage):
    monkeypatch.setattr("src.lib.openai_agents.provider_usage._emit_provider_usage_trace_event", lambda record: None)
    observer = Mock()
    with capture_provider_usage() as records, observe_provider_invocations(observer):
        pending = begin_provider_invocation(requested_provider="openai", requested_model="test", started_at=1)
        complete_generic_provider_invocation(pending, {"usage": usage}, latency_ms=1)
    assert observer.completed.call_args.args[1] == records[0]
    return records[0]


@pytest.mark.parametrize("dialect", ["responses", "chat"])
def test_inclusive_details_survive_without_double_counting(monkeypatch, dialect):
    input_key, output_key = ("input_tokens", "output_tokens") if dialect == "responses" else ("prompt_tokens", "completion_tokens")
    record = capture(monkeypatch, {
        input_key: 100, output_key: 40, "total_tokens": 140,
        f"{input_key}_details": {"cached_tokens": 30, "cache_write_tokens": 10, "private": "ignored"},
        f"{output_key}_details": {"reasoning_tokens": 25},
    })
    assert record.accounting_usage == TokenUsage(100, 40, 140, 30, 10, 25)
    assert record.accounting_usage.uncached_input_tokens == 60
    assert record.accounting_usage.nonreasoning_output_tokens == 15
    # The strict immutable benchmark artifact contract is not silently extended.
    from src.lib.benchmarks.models import ProviderUsage
    metadata = provider_usage_metadata(record)
    ProviderUsage.model_validate(metadata)
    assert "accounting_usage" not in metadata
    assert "cache_read_tokens" not in metadata


def test_unknown_is_not_zero_and_total_is_not_synthesized(monkeypatch):
    record = capture(monkeypatch, {"input_tokens": 3, "output_tokens": 2})
    assert record.accounting_usage == TokenUsage(3, 2)
    assert record.total_tokens == 5  # Existing artifact/display behavior only.
    zero = capture(monkeypatch, {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    })
    assert zero.accounting_usage == TokenUsage(0, 0, 0, 0, 0, 0)


def test_invalid_details_remain_unknown_and_inconsistency_is_not_clamped(monkeypatch):
    record = capture(monkeypatch, {
        "input_tokens": 3, "output_tokens": 2, "total_tokens": 5,
        "input_tokens_details": {"cached_tokens": True, "cache_write_tokens": -1},
        "output_tokens_details": {"reasoning_tokens": "2"},
    })
    assert record.accounting_usage == TokenUsage(3, 2, 5)
    inconsistent = capture(monkeypatch, {
        "input_tokens": 3, "input_tokens_details": {"cached_tokens": 4},
    })
    assert inconsistent.accounting_usage.cache_read_tokens == 4
    assert inconsistent.accounting_usage.status == "inconsistent"


def test_sdk_normalization_cannot_turn_unknown_into_recorded_zero(monkeypatch):
    record = capture(monkeypatch, Usage(input_tokens=10, output_tokens=2, total_tokens=12))
    assert record.accounting_usage == TokenUsage(10, 2, 12)
    defaults = capture(monkeypatch, Usage())
    assert defaults.accounting_usage == TokenUsage()
    known = capture(monkeypatch, Usage(
        input_tokens=10, output_tokens=5, total_tokens=15,
        input_tokens_details=InputTokensDetails(cached_tokens=3),
        output_tokens_details=OutputTokensDetails(reasoning_tokens=2),
    ))
    assert known.accounting_usage == TokenUsage(10, 5, 15, 3, None, 2)


def test_native_model_response_preserves_sdk_detail_boundary(monkeypatch):
    monkeypatch.setattr("src.lib.openai_agents.provider_usage._emit_provider_usage_trace_event", lambda record: None)
    response = ModelResponse(output=[], response_id="synthetic", usage=Usage(
        input_tokens=10, output_tokens=4, total_tokens=14,
        input_tokens_details=InputTokensDetails(cached_tokens=5),
        output_tokens_details=OutputTokensDetails(reasoning_tokens=3),
    ))
    with capture_provider_usage() as records:
        pending = begin_provider_invocation(requested_provider="openai", requested_model="test", started_at=1)
        complete_generic_provider_invocation(pending, response, latency_ms=1)
    assert records[0].accounting_usage == TokenUsage(10, 4, 14, 5, None, 3)


def test_openrouter_preserves_details_without_changing_recorded_charge():
    record = normalize_openrouter_usage({"usage": {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        "prompt_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 2}, "cost": "0.000000000000000123",
    }}, requested_model="synthetic", latency_ms=1)
    assert record.accounting_usage == TokenUsage(10, 5, 15, 3, 0, 2)
    assert str(record.billed_cost.amount) == "1.23E-16"


def test_failed_call_does_not_claim_free_work(monkeypatch):
    monkeypatch.setattr("src.lib.openai_agents.provider_usage._emit_provider_usage_trace_event", lambda record: None)
    with capture_provider_usage() as records:
        pending = begin_provider_invocation(requested_provider="openai", requested_model="test", started_at=1)
        fail_provider_invocation(pending, RuntimeError("synthetic"), latency_ms=1)
    assert records[0].accounting_usage == TokenUsage()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_conversion", [False, True])
async def test_streaming_wrapper_distinguishes_raw_response_from_sdk_conversion(monkeypatch, chat_conversion):
    from types import SimpleNamespace
    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
    from agents.models.openai_responses import OpenAIResponsesModel
    from openai.types.responses.response_usage import ResponseUsage
    from src.lib.openai_agents.benchmark_routing import (
        BenchmarkTelemetryModel, attach_benchmark_route, benchmark_route_plan,
        reset_benchmark_invocation_route, set_benchmark_invocation_route,
    )

    monkeypatch.setattr("src.lib.openai_agents.provider_usage._emit_provider_usage_trace_event", lambda record: None)
    # Same shape, different provenance: chatcmpl_stream_handler synthesizes zero
    # details when the upstream chunk omitted them. Responses can report real0.
    usage = ResponseUsage(input_tokens=10, output_tokens=4, total_tokens=14,
                          input_tokens_details=InputTokensDetails(cached_tokens=0),
                          output_tokens_details=OutputTokensDetails(reasoning_tokens=0))
    base = OpenAIChatCompletionsModel if chat_conversion else OpenAIResponsesModel

    class FakeModel(base):
        def __init__(self):
            pass

        async def stream_response(self, *args, **kwargs):
            yield SimpleNamespace(response=SimpleNamespace(usage=usage))

    agent = SimpleNamespace(model="test")
    with benchmark_route_plan({"supervisor": {"provider": "openai", "model": "test"}}), capture_provider_usage() as records:
        attach_benchmark_route(agent, "supervisor")
        token = set_benchmark_invocation_route(agent)
        try:
            async for _ in BenchmarkTelemetryModel(FakeModel()).stream_response():
                pass
        finally:
            reset_benchmark_invocation_route(token)
    detail = None if chat_conversion else 0
    assert records[0].accounting_usage == TokenUsage(10, 4, 14, detail, None, detail)
