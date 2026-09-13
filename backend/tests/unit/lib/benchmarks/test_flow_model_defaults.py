from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.lib.benchmarks import flow_model_defaults as module
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.flow_stages import BenchmarkFlowStage
from tests.unit.lib.benchmarks.test_source_revisions import source_receipt


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(module, "list_agents_visible_to_user", lambda *args, **kwargs: [
        NS(agent_key="system", model_id="model-a", model_reasoning="low"),
    ])
    monkeypatch.setattr(module, "list_models", lambda: [
        NS(model_id=model, supports_reasoning=True) for model in ("model-a", "model-b")
    ])
    monkeypatch.setattr(module, "get_agent_config", lambda name: NS(model="model-a", reasoning="low"))
    monkeypatch.setattr(module, "resolve_model_provider", lambda model: "fixture")
    return BenchmarkCuratorContext(subject="curator", auth_provider="oidc", db_user_id=42, active_groups=("group-a",))


def test_custom_stage_defaults_use_each_pinned_revision_and_report_coupling(configured, monkeypatch):
    first = source_receipt()
    second = first.model_copy(update={"agent_revision_id": uuid4(), "revision": 2})
    loader = Mock(side_effect=[(NS(), NS(model_id="model-a", model_reasoning="low")),
                             (NS(), NS(model_id="model-b", model_reasoning="high"))])
    monkeypatch.setattr(module, "get_execution_revision", loader)
    slot = f"agent:{first.agent_key}"
    stages = tuple(BenchmarkFlowStage(
        stage_id=str(index), node_id=str(index), title="Extractor", role="extraction",
        route_slot=slot, agent_id=receipt.agent_key, execution_receipt=receipt,
    ) for index, receipt in enumerate((first, second)))
    result, conflicts = module.stage_model_defaults(Mock(), configured, stages)
    assert [stage.default_route.model for stage in result] == ["model-a", "model-b"]
    assert conflicts == (slot,)
    assert [call.args[2] for call in loader.call_args_list] == [first.agent_revision_id, second.agent_revision_id]
    assert all(call.kwargs == {"active_group_ids": ["group-a"]} for call in loader.call_args_list)


def test_deterministic_step_has_no_model_default(configured):
    stage = BenchmarkFlowStage(stage_id="check", node_id="node", title="Check", role="validation", route_slot=None)
    result, conflicts = module.stage_model_defaults(Mock(), configured, (stage,))
    assert result == (stage,) and conflicts == ()


def test_hidden_system_agent_is_not_given_a_default(configured):
    stage = BenchmarkFlowStage(stage_id="hidden", node_id="node", title="Hidden", role="other", route_slot="agent:hidden", agent_id="hidden")
    with pytest.raises(ValueError, match="unavailable"):
        module.stage_model_defaults(Mock(), configured, (stage,))
