from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from src.lib.benchmarks.supervisor_snapshot import FrozenFlowSupervisor, capture_flow_supervisor
from src.lib.benchmarks.source_revisions import benchmark_source_revisions
from src.lib.benchmarks.models import BenchmarkSuiteRoute
from src.lib.flows import executor
from src.lib.openai_agents.benchmark_routing import benchmark_route_plan
from tests.unit.lib.benchmarks.test_frozen_flow import snapshot


def test_capture_uses_normal_prompt_generator_without_constructing_agents(monkeypatch):
    monkeypatch.setattr("src.lib.openai_agents.config.get_agent_config", lambda _: NS(model="original", temperature=0.2, reasoning="low"))
    monkeypatch.setattr("src.lib.openai_agents.config.get_flow_supervisor_parallel_tool_calls_enabled", lambda: False)
    monkeypatch.setattr(executor, "flow_requires_document", lambda *a, **kw: True)
    forbidden = Mock(side_effect=AssertionError("No agent construction at capture"))
    monkeypatch.setattr(executor, "get_all_agent_tools", forbidden)
    frozen = capture_flow_supervisor(snapshot(), NS(db_user_id=7, active_groups=()))
    assert frozen.available_tools == ("ask_ca_fixture_specialist",)
    assert "ask_ca_fixture_specialist" in frozen.instructions
    assert "Document Available" not in frozen.instructions
    assert "Document Available" in frozen.instructions_with_document
    assert frozen.temperature == 0.2 and not frozen.parallel_tool_calls
    assert FrozenFlowSupervisor.model_validate_json(frozen.model_dump_json()) == frozen
    forbidden.assert_not_called()


def test_runtime_uses_frozen_supervisor_and_fails_if_a_step_disappears(monkeypatch):
    frozen = FrozenFlowSupervisor(
        model="original", temperature=0.2, reasoning="low", parallel_tool_calls=False,
        requires_document=True, available_tools=("ask_extractor_specialist",),
        instructions="Saved instructions", instructions_with_document="Saved document instructions",
    )
    forbidden = Mock(side_effect=AssertionError("Must not consult mutable supervisor sources"))
    monkeypatch.setattr(executor, "get_agent_config", forbidden)
    monkeypatch.setattr(executor, "get_flow_supervisor_parallel_tool_calls_enabled", forbidden)
    monkeypatch.setattr(executor, "flow_requires_document", forbidden)
    monkeypatch.setattr(executor, "build_supervisor_instructions", forbidden)
    monkeypatch.setattr(executor, "resolve_model_provider", lambda *a, **kw: "openai")
    monkeypatch.setattr(executor, "get_model_for_agent", lambda *a, **kw: "fixture-model")
    monkeypatch.setattr(executor, "build_model_settings", lambda **kw: kw)
    monkeypatch.setattr(executor, "Agent", lambda **kw: NS(**kw))
    tools = Mock(return_value=([object()], {"ask_extractor_specialist"}, [], {}))
    monkeypatch.setattr(executor, "get_all_agent_tools", tools)
    routes = {"supervisor": BenchmarkSuiteRoute(provider="openai", model="experiment", reasoning_effort="high")}
    with benchmark_route_plan(routes), benchmark_source_revisions({}, supervisor=frozen):
        built = executor.create_flow_supervisor(NS(id="fixture-flow", name="Flow", flow_definition={}), document_id="paper", benchmark_routes=routes)
        assert built.instructions == "Saved document instructions"
        assert built.model_settings["temperature"] == 0.2
        assert built.model_settings["reasoning_effort"] == "high"
        assert not built.model_settings["parallel_tool_calls"]
        tools.return_value = ([], set(), [{"reason": "revoked"}], {})
        with pytest.raises(ValueError, match="no longer executable"):
            executor.create_flow_supervisor(NS(id="fixture-flow", name="Flow", flow_definition={}), document_id="paper", benchmark_routes=routes)
    forbidden.assert_not_called()
