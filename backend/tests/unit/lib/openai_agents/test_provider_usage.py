"""Tests for safe routed-provider usage normalization."""

from decimal import Decimal

from openai.types.chat import ChatCompletion

from src.lib.openai_agents.provider_usage import (
    emit_provider_usage,
    normalize_openrouter_usage,
)


def test_normalize_openrouter_usage_uses_selected_route_and_exact_billed_cost():
    record = normalize_openrouter_usage(
        {
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "cost": "0.0012300",
                "cost_details": {"upstream_inference_cost": 99},
            },
            "openrouter_metadata": {
                "attempt": 1,
                "summary": "must not be captured",
                "pipeline": [{"data": {"action": "redacted"}}],
                "endpoints": {
                    "available": [
                        {"provider": "Other", "model": "other/model", "selected": False},
                        {
                            "provider": "DeepInfra",
                            "model": "deepseek/deepseek-v4-pro-0813",
                            "selected": True,
                            "unknown": "ignored",
                        },
                    ]
                },
                "future_field": {"ignored": True},
            },
        },
        requested_model="deepseek/deepseek-v4-pro-0813",
        latency_ms=1234,
    )

    assert record.requested_provider == "openrouter"
    assert record.actual_provider == "DeepInfra"
    assert record.actual_model == "deepseek/deepseek-v4-pro-0813"
    assert record.routing_attempt == 1
    assert record.input_tokens == 10
    assert record.output_tokens == 20
    assert record.total_tokens == 30
    assert record.billed_cost is not None
    assert record.billed_cost.amount == Decimal("0.0012300")
    assert record.billed_cost.unit == "credits"
    assert record.billed_cost.source == "openrouter_usage"
    assert not hasattr(record, "summary")
    assert not hasattr(record, "pipeline")


def test_normalize_openrouter_usage_does_not_guess_route_or_cost():
    record = normalize_openrouter_usage(
        {
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 5,
                "total_tokens": 9,
                "cost_details": {"upstream_inference_cost": 0.123},
            },
            "openrouter_metadata": {
                "attempt": 1,
                "endpoints": {
                    "available": [
                        {
                            "provider": "Candidate",
                            "model": "deepseek/deepseek-v4-pro-0813",
                            "selected": False,
                        }
                    ]
                },
            },
        },
        requested_model="deepseek/deepseek-v4-pro-0813",
        latency_ms=20,
    )

    assert record.actual_provider is None
    assert record.actual_model is None
    assert record.billed_cost is None


def test_openai_sdk_model_preserves_additive_openrouter_fields():
    response = ChatCompletion.model_validate(
        {
            "id": "redacted",
            "object": "chat.completion",
            "created": 1,
            "model": "deepseek/deepseek-v4-pro-0813",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": None,
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3,
                "cost": "0.01",
            },
            "openrouter_metadata": {
                "attempt": 1,
                "endpoints": {
                    "available": [
                        {
                            "provider": "Test Provider",
                            "model": "deepseek/deepseek-v4-pro-0813",
                            "selected": True,
                        }
                    ]
                },
            },
        }
    )

    record = normalize_openrouter_usage(
        response,
        requested_model="deepseek/deepseek-v4-pro-0813",
        latency_ms=1,
    )

    assert record.actual_provider == "Test Provider"
    assert record.billed_cost is not None
    assert record.billed_cost.amount == Decimal("0.01")


def test_emit_provider_usage_publishes_only_bounded_trace_metadata(monkeypatch):
    calls = []
    fake_langfuse = type(
        "FakeLangfuse",
        (),
        {"create_event": lambda _self, **kwargs: calls.append(kwargs)},
    )()
    monkeypatch.setattr(
        "src.lib.openai_agents.langfuse_client.get_langfuse",
        lambda: fake_langfuse,
    )
    monkeypatch.setattr("src.lib.context.get_current_trace_id", lambda: "trace-123")
    record = normalize_openrouter_usage(
        {
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "cost": "0.0012300",
            },
            "openrouter_metadata": {
                "attempt": 1,
                "summary": "must not reach the trace",
                "pipeline": [{"data": {"prompt": "must not reach the trace"}}],
                "endpoints": {
                    "available": [
                        {
                            "provider": "DeepInfra",
                            "model": "deepseek/deepseek-v4-pro-0813",
                            "selected": True,
                        }
                    ]
                },
            },
        },
        requested_model="deepseek/deepseek-v4-pro-0813",
        latency_ms=1234,
    )

    emit_provider_usage(record)

    assert calls == [
        {
            "name": "provider_usage",
            "metadata": {
                "provider_usage": {
                    "requested_provider": "openrouter",
                    "requested_model": "deepseek/deepseek-v4-pro-0813",
                    "actual_provider": "DeepInfra",
                    "actual_model": "deepseek/deepseek-v4-pro-0813",
                    "routing_attempt": 1,
                    "latency_ms": 1234,
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                    "billed_cost": {
                        "amount": "0.0012300",
                        "unit": "credits",
                        "source": "openrouter_usage",
                    },
                }
            },
            "trace_context": {"trace_id": "trace-123"},
        }
    ]
    serialized = str(calls)
    assert "summary" not in serialized
    assert "pipeline" not in serialized
    assert "prompt" not in serialized
