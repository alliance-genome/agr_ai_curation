import json
from pathlib import Path

import pytest

from src.lib.benchmarks.loader import BenchmarkCatalogError
from src.lib.benchmarks.models import (
    BenchmarkRoute,
    BenchmarkSelection,
    ExecutionResult,
    ProviderUsage,
)
from src.lib.benchmarks.service import BenchmarkService


def _service(catalog, executor, **overrides):
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


async def test_provider_usage_slot_carries_shared_normalized_shape(benchmark_catalog):
    fixture_path = Path(__file__).parent / "fixtures" / "provider_usage.json"
    usage = ProviderUsage.model_validate(
        json.loads(fixture_path.read_text(encoding="utf-8"))
    )

    async def executor(*_args):
        return ExecutionResult(output={"ok": True}, provider_usage=usage)

    run = (
        await _service(benchmark_catalog, executor).execute(BenchmarkSelection())
    ).runs[0]
    assert run.provider_usage == usage
    assert run.provider_usage is not None
    assert run.provider_usage.actual_provider == "deepseek"


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
