import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import src.lib.flows.executor as flow_executor
import src.lib.benchmarks.runtime as benchmark_runtime
from src.lib.benchmarks.loader import BenchmarkCatalogError
from src.lib.benchmarks.models import (
    BenchmarkExecutionTarget,
    BenchmarkInputReference,
    BenchmarkSuiteRoute,
    ResolvedBenchmarkCell,
)
from src.lib.openai_agents.provider_usage import ProviderUsageRecord, emit_provider_usage
from src.lib.openai_agents.benchmark_routing import benchmark_route_plan

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_BENCHMARK_ROOT = REPOSITORY_ROOT / "packages" / "alliance" / "benchmarks"
APPROVED_RELEASE_ROUTES = [
    ("openai", "gpt-5.6-sol"),
    ("openai", "gpt-5.6-terra"),
    ("openrouter", "deepseek/deepseek-v4-pro-0813"),
    ("openrouter", "google/gemini-3.7-flash"),
    ("openrouter", "qwen/qwen3.8-27b"),
]


@pytest.fixture
def configured_benchmark_root(monkeypatch):
    monkeypatch.setenv("BENCHMARK_ROOT", str(ALLIANCE_BENCHMARK_ROOT))
    return ALLIANCE_BENCHMARK_ROOT


def test_default_runtime_catalog_loads_without_constructing_targets(
    configured_benchmark_root,
):
    catalog = benchmark_runtime.build_default_catalog()
    assert {loaded.profile.profile_id for loaded in catalog.profiles} == {
        "isolated-gene-agent-v1",
        "isolated-ontology-agent-v1",
        "flow-canary-gene-curation-v1",
    }
    for provider, model in APPROVED_RELEASE_ROUTES:
        catalog.validate_route(model, provider)


def test_default_runtime_catalog_rejects_unknown_and_mismatched_routes(
    configured_benchmark_root,
):
    catalog = benchmark_runtime.build_default_catalog()

    with pytest.raises(BenchmarkCatalogError, match="Unknown model_id"):
        catalog.validate_route("made-up-model", "not-real")
    with pytest.raises(BenchmarkCatalogError, match="belongs to provider 'openai'"):
        catalog.validate_route("gpt-5.6-sol", "openrouter")


def test_default_runtime_catalog_rejects_invalid_checked_in_route(
    tmp_path, configured_benchmark_root
):
    benchmark_root = tmp_path / "benchmarks"
    shutil.copytree(configured_benchmark_root, benchmark_root)
    profile = benchmark_root / "profiles" / "isolated-gene-agent-v1.yaml"
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "model: gpt-5.6-sol", "model: made-up-model", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkCatalogError, match="Unknown model_id"):
        benchmark_runtime.build_default_catalog(benchmark_root)


def test_default_runtime_catalog_requires_configured_root(monkeypatch):
    monkeypatch.delenv("BENCHMARK_ROOT", raising=False)

    with pytest.raises(BenchmarkCatalogError, match="BENCHMARK_ROOT must be configured"):
        benchmark_runtime.build_default_catalog()


def test_default_service_wires_configured_adjudication_model(monkeypatch):
    monkeypatch.setattr(
        benchmark_runtime,
        "build_default_catalog",
        lambda _root=None: cast(Any, object()),
    )
    monkeypatch.setattr(
        benchmark_runtime,
        "get_benchmark_adjudication_model",
        lambda: "deployment-adjudicator-v2",
    )

    service = benchmark_runtime.build_default_service()

    assert service.adjudicator is not None
    assert service.adjudicator.model == "deployment-adjudicator-v2"


def test_flow_supervisor_applies_route_to_supervisor_and_specialists(monkeypatch):
    captured = {}
    flow = SimpleNamespace(name="Canary", flow_definition={"nodes": []})

    monkeypatch.setattr(
        flow_executor,
        "get_agent_config",
        lambda _agent_id: SimpleNamespace(
            model="default-model", temperature=None, reasoning=None
        ),
    )
    monkeypatch.setattr(
        flow_executor,
        "resolve_model_provider",
        lambda model, provider_override=None: captured.setdefault(
            "resolved_route", (model, provider_override)
        )[1],
    )
    monkeypatch.setattr(
        flow_executor,
        "get_model_for_agent",
        lambda model, provider_override=None: (model, provider_override),
    )
    monkeypatch.setattr(flow_executor, "build_model_settings", lambda **kwargs: kwargs)

    def fake_tools(**kwargs):
        captured["tool_kwargs"] = kwargs
        return ([object()], {"ask_gene_validation_specialist"}, [], {})

    monkeypatch.setattr(flow_executor, "get_all_agent_tools", fake_tools)
    monkeypatch.setattr(
        flow_executor, "flow_requires_document", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        flow_executor,
        "build_supervisor_instructions",
        lambda *_args, **_kwargs: "instructions",
    )
    monkeypatch.setattr(
        flow_executor, "Agent", lambda **kwargs: SimpleNamespace(**kwargs)
    )

    supervisor = flow_executor.create_flow_supervisor(
        cast(Any, flow),
        model_id_override="deepseek/deepseek-v4-pro-0813",
        model_provider_override="openrouter",
    )

    assert captured["resolved_route"] == (
        "deepseek/deepseek-v4-pro-0813",
        "openrouter",
    )
    assert (
        captured["tool_kwargs"]["model_id_override"] == "deepseek/deepseek-v4-pro-0813"
    )
    assert captured["tool_kwargs"]["model_provider_override"] == "openrouter"
    assert supervisor.model == ("deepseek/deepseek-v4-pro-0813", "openrouter")

    captured.clear()
    benchmark_routes = {
        "supervisor": BenchmarkSuiteRoute(
            provider="openai", model="supervisor-model", reasoning_effort="high"
        ),
        "agent:extractor": BenchmarkSuiteRoute(
            provider="openrouter", model="extractor-model", reasoning_effort="low"
        ),
    }
    with benchmark_route_plan(benchmark_routes):
        routed_supervisor = flow_executor.create_flow_supervisor(
            cast(Any, flow), benchmark_routes=benchmark_routes
        )

    assert routed_supervisor.model == ("supervisor-model", "openai")
    assert routed_supervisor.model_settings["reasoning_effort"] == "high"
    assert captured["tool_kwargs"]["benchmark_routes"] == benchmark_routes


def _resolved_cell(kind: str, target_id: str, routes: dict[str, BenchmarkSuiteRoute]):
    return ResolvedBenchmarkCell(
        cell_id="sha256:" + "1" * 64,
        case_id="case-1",
        configuration_id="config-1",
        repetition=1,
        target=BenchmarkExecutionTarget(kind=kind, id=target_id),
        input=BenchmarkInputReference(
            resolver="fixture",
            reference="paper-1",
            version="1",
            digest="sha256:" + "2" * 64,
        ),
        routes=routes,
    )


@pytest.mark.asyncio
async def test_resolved_agent_cell_applies_route_and_keeps_all_invocations(monkeypatch):
    captured = {}
    route = BenchmarkSuiteRoute(
        provider="openrouter", model="extractor-model", reasoning_effort="high"
    )
    cell = _resolved_cell("agent", "extractor", {"agent:extractor": route})

    def fake_agent(agent_id, **kwargs):
        captured.update(agent_id=agent_id, kwargs=kwargs)
        return SimpleNamespace(model=SimpleNamespace())

    async def fake_stream(**kwargs):
        for attempt in (1, 2):
            emit_provider_usage(
                ProviderUsageRecord(
                    route_slot="agent:extractor",
                    requested_provider="openrouter",
                    requested_model="extractor-model",
                    reasoning_effort="high",
                    actual_provider=f"provider-{attempt}",
                    actual_model="extractor-model",
                    routing_attempt=attempt,
                    latency_ms=attempt,
                    input_tokens=1,
                    output_tokens=2,
                    total_tokens=3,
                    billed_cost=None,
                    sequence=attempt,
                )
            )
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    monkeypatch.setattr(benchmark_runtime, "get_agent_by_id", fake_agent)
    monkeypatch.setattr(benchmark_runtime, "run_agent_streamed", fake_stream)

    result = await benchmark_runtime.execute_resolved_agent_cell(
        cell, {"messages": []}, "run-1"
    )

    assert captured["kwargs"]["model_id_override"] == "extractor-model"
    assert captured["kwargs"]["model_provider_override"] == "openrouter"
    assert captured["kwargs"]["model_reasoning_override"] == "high"
    assert [item.routing_attempt for item in result.invocations] == [1, 2]
    assert result.output == "done"


@pytest.mark.asyncio
async def test_resolved_flow_cell_passes_complete_routes_and_stable_invocations(
    monkeypatch,
):
    routes = {
        "supervisor": BenchmarkSuiteRoute(
            provider="openai", model="supervisor-model", reasoning_effort="high"
        ),
        "agent:extractor": BenchmarkSuiteRoute(
            provider="openrouter", model="extractor-model", reasoning_effort="low"
        ),
        "validator:ontology": BenchmarkSuiteRoute(
            provider="openai", model="validator-model", reasoning_effort="medium"
        ),
    }
    cell = _resolved_cell("flow", "flow-1", routes)
    captured = {}
    monkeypatch.setattr(benchmark_runtime, "_flow_from_recipe", lambda _id: SimpleNamespace(id="flow-id"))

    async def fake_flow(**kwargs):
        captured.update(kwargs)
        reserved = list(enumerate(routes.items(), 1))
        for sequence, (slot, route) in reversed(reserved):
            emit_provider_usage(
                ProviderUsageRecord(
                    route_slot=slot,
                    requested_provider=route.provider,
                    requested_model=route.model,
                    reasoning_effort=route.reasoning_effort,
                    actual_provider=route.provider,
                    actual_model=route.model,
                    routing_attempt=0,
                    latency_ms=sequence,
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                    billed_cost=None,
                    sequence=sequence,
                )
            )
        yield {"type": "FLOW_FINISHED", "data": {"ok": True}}

    monkeypatch.setattr(benchmark_runtime, "execute_flow", fake_flow)
    result = await benchmark_runtime.execute_resolved_flow_cell(cell, {}, "run-1")

    assert captured["benchmark_routes"] == cell.routes
    assert [item.route_slot for item in result.invocations] == list(routes)
    assert [item.requested_model for item in result.invocations] == [
        "supervisor-model",
        "extractor-model",
        "validator-model",
    ]
