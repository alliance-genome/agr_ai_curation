"""Tests for safe routed-provider usage normalization."""

from decimal import Decimal

from openai.types.chat import ChatCompletion
import pytest

from src.lib.openai_agents.provider_usage import (
    PendingProviderInvocation,
    ProviderUsageRecord,
    begin_provider_invocation,
    complete_provider_invocation,
    capture_provider_usage,
    emit_provider_usage,
    fail_provider_invocation,
    normalize_openrouter_usage,
    observe_provider_invocations,
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


def test_provider_observer_checkpoints_before_and_after_dispatch(monkeypatch):
    monkeypatch.setattr(
        "src.lib.openai_agents.provider_usage._emit_provider_usage_trace_event",
        lambda _record: None,
    )
    checkpoints = []

    class Observer:
        def started(self, pending):
            checkpoints.append(("started", pending.sequence))

        def completed(self, pending, record):
            checkpoints.append((record.status, pending.sequence))

    with observe_provider_invocations(Observer()), capture_provider_usage(
        max_records=2, max_failure_detail_chars=20
    ) as records:
        pending = begin_provider_invocation(
            requested_provider="openai",
            requested_model="model-a",
            route_slot="agent:a",
            reasoning_effort="high",
            started_at=1.0,
        )
        assert checkpoints == [("started", 1)]
        complete_provider_invocation(
            pending,
            ProviderUsageRecord(
                requested_provider="ignored",
                requested_model="ignored",
                actual_provider="openai",
                actual_model="model-a",
                routing_attempt=0,
                latency_ms=10,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                billed_cost=None,
            ),
        )

    assert checkpoints == [("started", 1), ("completed", 1)]
    assert records[0].route_slot == "agent:a"


@pytest.mark.parametrize("provider_failed", [False, True])
def test_provider_usage_is_emitted_before_observer_settlement(
    monkeypatch, provider_failed: bool
):
    trace_records = []
    monkeypatch.setattr(
        "src.lib.openai_agents.provider_usage._emit_provider_usage_trace_event",
        trace_records.append,
    )

    class Observer:
        def started(self, pending: PendingProviderInvocation) -> None:
            pass

        def completed(
            self,
            pending: PendingProviderInvocation,
            record: ProviderUsageRecord,
        ) -> None:
            raise RuntimeError("durable settlement failed")

    with observe_provider_invocations(Observer()), capture_provider_usage(
        max_records=1, max_failure_detail_chars=20
    ) as records:
        pending = begin_provider_invocation(
            requested_provider="openai",
            requested_model="model-a",
            started_at=1.0,
        )
        with pytest.raises(RuntimeError, match="durable settlement failed"):
            if provider_failed:
                fail_provider_invocation(
                    pending,
                    RuntimeError("provider failed"),
                    latency_ms=10,
                )
            else:
                complete_provider_invocation(
                    pending,
                    ProviderUsageRecord(
                        requested_provider="ignored",
                        requested_model="ignored",
                        actual_provider="openai",
                        actual_model="model-a",
                        routing_attempt=0,
                        latency_ms=10,
                        input_tokens=1,
                        output_tokens=2,
                        total_tokens=3,
                        billed_cost=None,
                    ),
                )

    assert len(records) == 1
    assert trace_records == records
    assert records[0].status == ("failed" if provider_failed else "completed")


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
                    "route_slot": None,
                    "requested_provider": "openrouter",
                    "requested_model": "deepseek/deepseek-v4-pro-0813",
                    "reasoning_effort": None,
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
                    "sequence": None,
                    "status": "completed",
                    "failure_detail": None,
                }
            },
            "trace_context": {"trace_id": "trace-123"},
        }
    ]
    serialized = str(calls)
    assert "summary" not in serialized
    assert "pipeline" not in serialized
    assert "prompt" not in serialized


def test_emit_provider_usage_reports_sanitized_emission_failure_without_raising(
    monkeypatch,
):
    captured = []
    sensitive_detail = "secret prompt and bearer token"

    class _FailingLangfuse:
        def create_event(self, **_kwargs):
            raise RuntimeError(sensitive_detail)

    def _failing_report(exc, **kwargs):
        captured.append((exc, kwargs))
        raise RuntimeError("Sentry unavailable")

    monkeypatch.setattr(
        "src.lib.openai_agents.langfuse_client.get_langfuse",
        lambda: _FailingLangfuse(),
    )
    monkeypatch.setattr("src.lib.context.get_current_trace_id", lambda: "trace-123")
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        _failing_report,
    )
    record = normalize_openrouter_usage(
        {},
        requested_model="model-name",
        latency_ms=10,
    )

    assert emit_provider_usage(record) is None

    assert len(captured) == 1
    reported_exc, kwargs = captured[0]
    assert str(reported_exc) == (
        "Provider usage trace event emission failed (RuntimeError)"
    )
    assert reported_exc.__traceback__ is not None
    assert reported_exc.__context__ is None
    assert reported_exc.__cause__ is None
    assert sensitive_detail not in str(reported_exc)
    assert kwargs == {
        "component": "provider_usage",
        "operation": "trace_event_emission_failed",
        "tags": {"provider": "openrouter"},
    }
