from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.lib.benchmarks import selected_catalog as service
from src.lib.benchmarks.frozen_flow import FrozenBenchmarkFlow
from src.lib.benchmarks.models import BenchmarkExecutionTarget, BenchmarkSuite
from src.lib.benchmarks.suites import resolve_suite
from tests.unit.lib.benchmarks.test_source_revisions import source_receipt
from tests.unit.lib.benchmarks.test_suites import _catalog, _route
from tests.unit.lib.flows.test_execution_revisions import flow


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(service, "capture_dependencies", lambda *a: None)
    receipt = source_receipt("ca_fixture")
    definition = flow(receipt).model_dump(mode="json")
    frozen = FrozenBenchmarkFlow(source_kind="saved_flow", source_id=str(uuid4()),
                                 source_revision="sha256:" + "a" * 64, title="Selected", description=None,
                                 definition=definition)
    target = {"kind": "flow", "id": frozen.source_id, "source_kind": "saved_flow",
              "source_revision": frozen.source_revision}
    suite = BenchmarkSuite.model_validate({
        "schema_version": 2, "suite_id": "selected", "cases": [{
            "case_id": "paper", "target": target,
            "input": {"resolver": "fixture", "reference": "paper", "version": "1", "digest": "sha256:" + "b" * 64},
        }], "configurations": [{"configuration_id": "baseline"}],
    })
    stage = NS(route_slot=f"agent:{receipt.agent_key}", default_route=_route(), execution_receipt=receipt,
               node_id="node_0", agent_id=receipt.agent_key)
    contracts = NS(stages=(stage,), nodes=(), route_default_conflicts=())
    capture = Mock(return_value=frozen)
    monkeypatch.setattr(service, "capture_saved_flow", capture)
    monkeypatch.setattr(service, "saved_flow_contracts", Mock(return_value=contracts))
    monkeypatch.setattr(service, "list_agents_visible_to_user", lambda *a, **kw: [])
    monkeypatch.setattr(service, "capture_flow_supervisor", lambda *a: None)
    return NS(suite=suite, frozen=frozen, receipt=receipt, stage=stage, contracts=contracts,
              capture=capture, curator=NS(db_user_id=7, active_groups=()))


def test_selected_flow_is_prepared_from_server_sources_and_resolves_plan(setup):
    catalog = service.prepare_selected_catalog(Mock(), setup.curator, _catalog(), setup.suite)
    assert len(catalog.targets) == 1
    assert catalog.targets[0].flow_snapshot.source_id == setup.frozen.source_id
    assert catalog.targets[0].source_execution_receipts == {f"agent:{setup.receipt.agent_key}": setup.receipt}
    assert setup.capture.call_args.kwargs["expected_revision"] == setup.frozen.source_revision
    plan = resolve_suite(setup.suite, catalog, max_cases=1, max_configurations=1, max_repetitions=1, max_cells=1)
    assert plan.cells[0].flow_snapshot == catalog.targets[0].flow_snapshot


@pytest.mark.parametrize("failure", ["drift", "no_stages", "conflict", "changed_pin"])
def test_unusable_selected_flow_fails_closed(setup, failure):
    if failure == "drift":
        setup.capture.side_effect = ValueError("source changed")
    elif failure == "no_stages":
        setup.contracts.stages = ()
    elif failure == "conflict":
        setup.contracts.route_default_conflicts = (setup.stage.route_slot,)
    else:
        setup.stage.execution_receipt = setup.receipt.model_copy(update={"revision": 2})
    with pytest.raises(ValueError):
        service.prepare_selected_catalog(Mock(), setup.curator, _catalog(), setup.suite)


def test_saved_selection_requires_revision_and_rejects_embedded_executable_data():
    target = {"kind": "flow", "id": str(uuid4()), "source_kind": "saved_flow"}
    with pytest.raises(ValueError):
        BenchmarkExecutionTarget.model_validate(target)
    target.update(source_revision="sha256:" + "a" * 64, definition={"nodes": []})
    with pytest.raises(ValueError):
        BenchmarkExecutionTarget.model_validate(target)
    assert BenchmarkExecutionTarget(kind="flow", id="recipe").model_dump(mode="json") == {"kind": "flow", "id": "recipe"}


@pytest.mark.parametrize("agent_key", ["csv_formatter", "tsv_formatter", "json_formatter"])
def test_non_model_system_step_is_frozen_without_exposing_a_model_route(setup, monkeypatch, agent_key):
    from src.lib.benchmarks.system_snapshot import FrozenSystemAgent
    from src.lib.benchmarks.source_revisions import benchmark_source_revisions, active_system_source
    from tests.unit.lib.benchmarks.test_system_snapshot import bundle

    row = NS(agent_key=agent_key, visibility="system")
    source = FrozenSystemAgent(
        agent_key=agent_key, model_id="model-a", model_temperature=0.2,
        model_reasoning=None, tool_ids=(), group_tool_policy={}, output_schema_key=None,
        prompt_layer_manifest=bundle(agent_key).to_manifest(),
    )
    monkeypatch.setattr(service, "list_agents_visible_to_user", lambda *a, **kw: [row])
    capture = Mock(return_value=source)
    monkeypatch.setattr(service, "capture_system_agent", capture)
    setup.contracts.stages += (NS(route_slot=None, agent_id=agent_key),)
    catalog = service.prepare_selected_catalog(Mock(), setup.curator, _catalog(), setup.suite)
    target = catalog.targets[0]
    assert target.system_agent_snapshots == {agent_key: source}
    assert f"agent:{agent_key}" not in target.route_slots
    capture.assert_called_once_with(row, active_groups=setup.curator.active_groups)
    with benchmark_source_revisions({}, target.system_agent_snapshots):
        assert active_system_source(agent_key) == source


def test_explicit_experiment_route_resolves_shared_default_conflict(setup):
    slot = setup.stage.route_slot
    setup.contracts.route_default_conflicts = (slot,)
    payload = setup.suite.model_dump(mode="json")
    payload["configurations"][0]["routes"] = {slot: _route().model_dump(mode="json")}
    suite = BenchmarkSuite.model_validate(payload)
    catalog = service.prepare_selected_catalog(Mock(), setup.curator, _catalog(), suite)
    plan = resolve_suite(suite, catalog, max_cases=1, max_configurations=1, max_repetitions=1, max_cells=1)
    assert plan.cells[0].routes[slot] == _route()
