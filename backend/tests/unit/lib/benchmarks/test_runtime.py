import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import src.lib.flows.executor as flow_executor
import src.lib.benchmarks.runtime as benchmark_runtime
from src.lib.benchmarks.loader import BenchmarkCatalogError

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_BENCHMARK_ROOT = REPOSITORY_ROOT / "packages" / "alliance" / "benchmarks"


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
