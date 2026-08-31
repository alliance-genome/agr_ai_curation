import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.lib.benchmarks.loader import BenchmarkCatalogError
from src.lib.benchmarks.loader import BenchmarkCatalog
from src.lib.benchmarks.adjudication import (
    AdjudicationDecision,
    RawAdjudicationResponse,
    SupplementalAdjudicator,
)
from src.lib.benchmarks.models import (
    BenchmarkRoute,
    BenchmarkSelection,
    ExecutionResult,
    ProviderUsage,
)
from src.lib.benchmarks.service import BenchmarkService
from src.lib.openai_agents.provider_usage import (
    BilledCost as CapturedBilledCost,
    ProviderUsageRecord,
    emit_provider_usage,
)


def _service(
    catalog,
    executor,
    *,
    adjudicator: SupplementalAdjudicator | None = None,
    adjudication_case_limit: int = 1,
    **overrides,
):
    values = {
        "max_concurrency": 2,
        "matrix_limit": 20,
        "case_limit": 20,
        "result_limit": 20,
        "timeout_seconds": 1,
        "retries": 0,
        "preview_max_chars": 8,
        "inline_max_bytes": 32,
    }
    values.update(overrides)
    return BenchmarkService(
        catalog,
        agent_executor=executor,
        flow_executor=executor,
        adjudicator=adjudicator,
        adjudication_case_limit=adjudication_case_limit,
        **values,
    )


async def test_dry_run_is_stable_targetable_and_makes_no_executor_call(
    benchmark_catalog,
):
    calls = []

    async def executor(*args):
        calls.append(args)
        return ExecutionResult(output={})

    service = _service(benchmark_catalog, executor)
    selection = BenchmarkSelection(
        profile_ids=["profile-1"],
        case_ids=["case-1"],
        route=BenchmarkRoute(
            provider="openrouter", model="deepseek/deepseek-v4-pro-0813"
        ),
    )
    first = service.plan(selection)
    second = service.plan(selection)

    assert first == second
    assert first.runs[0].requested_route.provider == "openrouter"
    assert calls == []


async def test_execute_returns_canonical_bounded_record(benchmark_catalog):
    async def executor(target_id, case_input, route, run_id):
        assert target_id == "gene"
        assert case_input["messages"]
        assert route.provider == "openai"
        assert run_id.startswith("benchmark-")
        return ExecutionResult(output={"long": "x" * 100})

    response = await _service(benchmark_catalog, executor).execute(BenchmarkSelection())
    run = response.runs[0]

    assert run.status == "succeeded"
    assert run.failure is None
    assert run.output is not None
    assert run.output.kind == "preview"
    assert run.output.truncated is True
    assert run.fixture_digest.startswith("sha256:")
    assert run.started_at <= run.completed_at
    assert len(run.scoring) == 1
    assert run.scoring[0].deterministic.outcome == "fail"
    assert response.aggregates[0].profile_id == "profile-1"
    assert response.aggregates[0].fail_count == 1


async def test_provider_usage_slot_carries_shared_normalized_shape(benchmark_catalog):
    fixture_path = Path(__file__).parent / "fixtures" / "provider_usage.json"
    usage = ProviderUsage.model_validate(
        json.loads(fixture_path.read_text(encoding="utf-8"))
    )

    async def executor(*_args):
        billed_cost = usage.billed_cost
        emit_provider_usage(
            ProviderUsageRecord(
                requested_provider=usage.requested_provider,
                requested_model=usage.requested_model,
                actual_provider=usage.actual_provider,
                actual_model=usage.actual_model,
                routing_attempt=usage.routing_attempt,
                latency_ms=usage.latency_ms,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                billed_cost=(
                    CapturedBilledCost(
                        amount=billed_cost.amount,
                        unit=billed_cost.unit,
                        source=billed_cost.source,
                    )
                    if billed_cost is not None
                    else None
                ),
            )
        )
        return ExecutionResult(output={"ok": True})

    run = (
        await _service(benchmark_catalog, executor).execute(BenchmarkSelection())
    ).runs[0]
    assert run.provider_usage == usage
    assert run.provider_usage is not None
    assert run.provider_usage.actual_provider == "deepseek"
    assert run.provider_usage.routing_attempt == 1
    assert run.provider_usage.total_tokens == 150
    assert run.provider_usage.billed_cost is not None
    assert run.provider_usage.billed_cost.amount == Decimal("0.001500")
    assert run.provider_usage.billed_cost.source == "openrouter_usage"
    serialized = run.model_dump(mode="json")["provider_usage"]
    assert serialized["billed_cost"]["amount"] == "0.001500"


async def test_provider_usage_slot_uses_final_request_record(benchmark_catalog):
    async def executor(*_args):
        for attempt in (1, 2):
            emit_provider_usage(
                ProviderUsageRecord(
                    requested_provider="openrouter",
                    requested_model="deepseek/deepseek-v4-pro-0813",
                    actual_provider=f"provider-{attempt}",
                    actual_model="deepseek/deepseek-v4-pro-0813",
                    routing_attempt=attempt,
                    latency_ms=attempt,
                    input_tokens=attempt,
                    output_tokens=attempt,
                    total_tokens=attempt * 2,
                    billed_cost=None,
                )
            )
        return ExecutionResult(output={"ok": True})

    run = (
        await _service(benchmark_catalog, executor).execute(BenchmarkSelection())
    ).runs[0]

    assert run.provider_usage is not None
    assert run.provider_usage.routing_attempt == 2
    assert run.provider_usage.actual_provider == "provider-2"


async def test_failure_is_normalized_without_raw_exception(benchmark_catalog):
    async def executor(*_args):
        raise RuntimeError("Authorization: Bearer super-secret")

    response = await _service(benchmark_catalog, executor).execute(BenchmarkSelection())
    run = response.runs[0]

    assert run.status == "failed"
    assert run.failure is not None
    assert run.failure.category == "runtime_error"
    assert "secret" not in run.failure.message
    assert run.output is None


async def test_unexpected_failure_reports_sanitized_exception(
    benchmark_catalog, monkeypatch
):
    secret = "Authorization: Bearer super-secret"
    reported = []

    async def executor(*_args):
        raise OSError(secret)

    def capture(exc, **kwargs):
        reported.append((exc, kwargs))
        return True

    monkeypatch.setattr("src.lib.benchmarks.service.report_runtime_exception", capture)

    run = (
        await _service(benchmark_catalog, executor).execute(BenchmarkSelection())
    ).runs[0]

    assert run.status == "failed"
    assert run.failure is not None
    assert run.failure.category == "internal_error"
    assert len(reported) == 1
    reported_exception, metadata = reported[0]
    serialized_report = json.dumps(
        {"exception": str(reported_exception), "metadata": metadata}, sort_keys=True
    )
    assert "secret" not in serialized_report
    assert "Authorization" not in serialized_report
    assert reported_exception.__cause__ is None
    assert reported_exception.__context__ is None
    assert metadata["component"] == "benchmarks"
    assert metadata["operation"] == "execute_case"
    assert metadata["tags"] == {"run_kind": "agent"}
    assert metadata["context"]["run_id_hash"]


async def test_success_output_redacts_restricted_fields_and_auth_values(
    benchmark_catalog,
):
    async def executor(*_args):
        return ExecutionResult(
            output={
                "authorization": "Bearer top-secret",
                "nested": {"api_key": "key-value", "message": "used Basic abc123"},
                "input_tokens": 12,
            }
        )

    run = (
        await _service(benchmark_catalog, executor, inline_max_bytes=1000).execute(
            BenchmarkSelection()
        )
    ).runs[0]
    assert run.output is not None
    assert run.output.value == {
        "authorization": "[REDACTED]",
        "nested": {"api_key": "[REDACTED]", "message": "used [REDACTED]"},
        "input_tokens": 12,
    }


def test_matrix_case_and_result_limits_are_enforced(benchmark_catalog):
    async def executor(*_args):
        return ExecutionResult(output={})

    with pytest.raises(BenchmarkCatalogError, match="Matrix contains"):
        _service(benchmark_catalog, executor, matrix_limit=0).plan(BenchmarkSelection())
    with pytest.raises(BenchmarkCatalogError, match="cases; limit"):
        _service(benchmark_catalog, executor, case_limit=0).plan(BenchmarkSelection())
    with pytest.raises(BenchmarkCatalogError, match="Result count"):
        _service(benchmark_catalog, executor, result_limit=0).plan(BenchmarkSelection())


async def test_service_enforces_adjudication_case_cap(benchmark_root):
    profile_path = benchmark_root / "profiles" / "profile.yaml"
    content = profile_path.read_text(encoding="utf-8")
    content = content.replace(
        "scorers:\n  - id: exact-json",
        "  - case_id: case-2\n"
        "    fixture: cases/case-1/input.json\n"
        "    expected: cases/case-1/gold.json\n"
        "scorers:\n"
        "  - id: deterministic-v1\n"
        "    configuration:\n"
        "      scoring_version: 1\n"
        "      fields:\n"
        "        - comparison: exact\n"
        "          ambiguous: true",
    )
    profile_path.write_text(content, encoding="utf-8")
    catalog = BenchmarkCatalog(
        benchmark_root,
        agent_ids={"gene"},
        flow_ids=set(),
        route_validator=lambda _model, _provider: None,
    )

    async def target_executor(*_args):
        return ExecutionResult(output={"ok": False})

    async def adjudication_executor(*_args):
        return RawAdjudicationResponse(
            decision=AdjudicationDecision(
                outcome="supports_expected",
                reason="expected fixture is semantically preferable",
                confidence=Decimal("0.9"),
                uncertainty="",
            )
        )

    adjudicator = SupplementalAdjudicator(
        executor=adjudication_executor,
        enabled=True,
        timeout_seconds=1,
        retries=0,
        turn_limit=1,
        tool_call_limit=0,
        result_max_bytes=10000,
    )
    response = await _service(
        catalog,
        target_executor,
        adjudicator=adjudicator,
        adjudication_case_limit=1,
    ).execute(BenchmarkSelection())

    first = response.runs[0].scoring[0].adjudication
    second = response.runs[1].scoring[0].adjudication
    assert first is not None and first.status == "completed"
    assert second is not None and second.failure is not None
    assert second.failure.category == "case_limit"
