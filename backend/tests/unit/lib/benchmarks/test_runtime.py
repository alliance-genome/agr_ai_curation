from types import SimpleNamespace
from typing import Any, cast

import src.lib.flows.executor as flow_executor
import src.lib.benchmarks.runtime as benchmark_runtime


def test_default_runtime_catalog_loads_without_constructing_targets():
    catalog = benchmark_runtime.build_default_catalog()
    assert {loaded.profile.profile_id for loaded in catalog.profiles} == {
        "isolated-gene-agent-v1",
        "isolated-ontology-agent-v1",
        "flow-canary-gene-curation-v1",
    }


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
