import pytest

from src.lib.benchmarks.frozen_flow import FrozenBenchmarkFlow
from src.lib.benchmarks.stage_definitions import StageDefinition
from src.lib.benchmarks.models import BenchmarkTargetCatalogEntry, BenchmarkExecutionTarget
from tests.unit.lib.benchmarks.test_frozen_flow import snapshot
from tests.unit.lib.benchmarks.test_suites import _catalog, _payload
from src.lib.benchmarks.suites import resolve_suite, validate_suite


def test_stage_definitions_are_deeply_immutable_and_preserve_explicit_role():
    payload = snapshot().model_dump(mode="json")
    payload["stage_definitions"] = {"first": {
        "stage_id": "first", "role": "validation", "agent_id": "extractor_named_agent", "node_id": "node_0",
    }}
    frozen = FrozenBenchmarkFlow.model_validate(payload)
    assert frozen.stage_definitions["first"].identity().role == "validation"
    payload["stage_definitions"]["first"]["role"] = "extraction"
    assert frozen.stage_definitions["first"].role == "validation"
    with pytest.raises(TypeError):
        frozen.stage_definitions["second"] = frozen.stage_definitions["first"]
    with pytest.raises(ValueError):
        frozen.stage_definitions["first"].role = "other"
    assert FrozenBenchmarkFlow.model_validate_json(frozen.model_dump_json()) == frozen


def test_historical_flow_does_not_invent_stage_definitions():
    assert "stage_definitions" not in snapshot().model_dump(mode="json")


def test_direct_stages_are_frozen_and_participate_in_plan_identity():
    import json
    from src.lib.benchmarks.stage_definitions import role_from_category

    catalog = _catalog()
    target = catalog.targets[0]
    assert "direct_stage_definitions" not in target.model_dump(mode="json")
    payload = target.model_dump(mode="json")
    payload["direct_stage_definitions"] = {"direct": {
        "stage_id": "direct", "role": role_from_category("Validation"), "agent_id": target.target.id,
    }}
    frozen = BenchmarkTargetCatalogEntry.model_validate_json(json.dumps(payload))
    with pytest.raises(TypeError):
        frozen.direct_stage_definitions["new"] = frozen.direct_stage_definitions["direct"]
    assert frozen.direct_stage_definitions["direct"].role == "validation"
    suite_payload = _payload()
    suite_payload["cases"] = [suite_payload["cases"][0]]
    suite_payload["cases"][0]["target"] = target.target.model_dump(mode="json")
    suite = validate_suite(suite_payload)
    plans = [resolve_suite(suite, catalog.model_copy(update={"targets": (selected,)}),
                           max_cases=100, max_configurations=100, max_repetitions=100, max_cells=10000)
             for selected in (target, frozen)]
    assert plans[0].cells[0].cell_id != plans[1].cells[0].cell_id
    assert plans[1].cells[0].direct_stage_definitions == frozen.direct_stage_definitions


def test_direct_binding_keeps_direct_parent_without_fabricating_flow_nodes():
    from unittest.mock import Mock
    from src.lib.benchmarks.stage_measurements import (
        measure_bound_validator, measure_declared_stage, observe_stages, stage_definition_scope,
    )
    definitions = {
        "direct": StageDefinition(stage_id="direct", role="validation", agent_id="custom"),
        "binding:test": StageDefinition(stage_id="binding:test", role="validation", binding_id="test"),
    }
    with observe_stages(Mock()), stage_definition_scope(definitions), measure_declared_stage("direct") as parent:
        with measure_bound_validator("test") as child:
            assert parent is not None and child is not None
            assert child.parent_execution_id == parent.execution_id
            assert child.identity.node_id is None and child.identity.source_node_id is None


def test_mismatched_stage_identity_is_rejected():
    payload = snapshot().model_dump(mode="json")
    payload["stage_definitions"] = {"wrong": {"stage_id": "right", "role": "other"}}
    with pytest.raises(ValueError, match="identity"):
        FrozenBenchmarkFlow.model_validate(payload)


def test_stage_semantics_participate_in_frozen_flow_and_plan_identity():
    original = snapshot()
    changed = original.model_copy(update={"stage_definitions": {
        "first": StageDefinition(stage_id="first", role="validation", node_id="node_0"),
    }})
    suite_value = _payload()
    suite_value["cases"][0]["target"] = {"kind": "flow", "id": original.source_id}
    suite_value["cases"] = [suite_value["cases"][0]]
    suite = validate_suite(suite_value)
    plans = []
    for source in (original, changed):
        catalog = _catalog()
        template = catalog.targets[0]
        target = BenchmarkTargetCatalogEntry(
            target=BenchmarkExecutionTarget(kind="flow", id=original.source_id),
            route_slots=template.route_slots, flow_snapshot=source,
        )
        catalog = catalog.model_copy(update={"targets": (target,)})
        plans.append(resolve_suite(suite, catalog, max_cases=100, max_configurations=100, max_repetitions=100, max_cells=10000))
    assert plans[0].plan_digest != plans[1].plan_digest
    assert plans[0].cells[0].cell_id != plans[1].cells[0].cell_id


def test_runtime_node_lookup_uses_frozen_semantics_and_rejects_unknown_node():
    from unittest.mock import Mock
    from src.lib.benchmarks.stage_measurements import measure_flow_node, observe_stages, stage_definition_scope

    stage = StageDefinition(stage_id="first", role="validation", node_id="node_0", agent_id="extractor_named_agent")
    observer = Mock()
    with observe_stages(observer), stage_definition_scope({stage.stage_id: stage}):
        with measure_flow_node("node_0") as running:
            assert running.identity.role == "validation"
        with pytest.raises(ValueError, match="unique frozen"):
            with measure_flow_node("missing"):
                pytest.fail("Unknown node must not execute")
    assert observer.started.call_count == 1


@pytest.mark.parametrize("sidecar", [False, True])
def test_validator_binding_is_scoped_to_source_node_and_explicit_sidecar(sidecar):
    from unittest.mock import Mock
    from src.lib.benchmarks.stage_measurements import (
        current_stage, measure_bound_validator, measure_declared_stage,
        measure_flow_node, observe_stages, stage_definition_scope,
    )

    definitions = {
        "supervisor": StageDefinition(stage_id="supervisor", role="supervisor"),
        "source": StageDefinition(stage_id="source", role="extraction", node_id="a"),
        "binding": StageDefinition(
            stage_id="binding", role="validation", node_id="sidecar" if sidecar else "a",
            source_node_id="a", binding_id="shared", agent_id="same",
        ),
        "other": StageDefinition(
            stage_id="other", role="validation", node_id="b", source_node_id="b",
            binding_id="shared", agent_id="same",
        ),
    }
    observer = Mock()
    with observe_stages(observer), stage_definition_scope(definitions):
        with measure_declared_stage("supervisor") as supervisor:
            with measure_flow_node("a") as source:
                assert source is not None and supervisor is not None
                assert source.parent_execution_id == supervisor.execution_id
                with measure_bound_validator("shared", node_id="sidecar" if sidecar else None) as child:
                    assert child is not None
                    assert child.identity.stage_id == "binding"
                    assert child.parent_execution_id == source.execution_id
                assert current_stage() is source
                with pytest.raises(ValueError, match="unique frozen"):
                    with measure_bound_validator("missing"):
                        pytest.fail("Unknown bindings cannot execute")
    assert observer.started.call_count == observer.completed.call_count == 3


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_normal_validator_dispatch_enters_frozen_stage(monkeypatch, batch, failed):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from src.lib.domain_packs import validator_dispatch as dispatch
    from src.lib.benchmarks.stage_measurements import (
        current_stage, measure_flow_node, observe_stages, stage_definition_scope,
    )

    definitions = {
        "source": StageDefinition(stage_id="source", role="extraction", node_id="a"),
        "binding": StageDefinition(stage_id="binding", role="validation", node_id="a",
                                   source_node_id="a", binding_id="test"),
    }
    request = SimpleNamespace(validator_binding_id="test", request_id="request")
    job = SimpleNamespace(request=request, match=SimpleNamespace(binding=SimpleNamespace(binding_id="test")))
    result = SimpleNamespace(status="unresolved" if failed else "resolved")
    clock = [0.0]
    monkeypatch.setattr("src.lib.benchmarks.stage_measurements.monotonic", lambda: clock[0])

    def finalize(value, **kwargs):
        assert current_stage().identity.stage_id == "binding"
        clock[0] += 0.25
        return value

    def runner(*args, **kwargs):
        stage = current_stage()
        assert stage is not None and stage.identity.stage_id == "binding"
        if failed:
            raise RuntimeError("Sensitive provider text must not enter stage measurement")
        return result

    monkeypatch.setattr(dispatch, "_validated_result_from_agent_output", lambda *a, **kw: result)
    monkeypatch.setattr(dispatch, "_finalize_validator_result", finalize)
    monkeypatch.setattr(dispatch, "_validated_results_from_agent_batch_output", lambda *a, **kw: [finalize(result)])
    monkeypatch.setattr(dispatch, "_unresolved_result_for_dispatch_problem", lambda *a, **kw: result)
    monkeypatch.setattr(dispatch, "_report_validator_dispatch_failure", lambda *a, **kw: None)
    monkeypatch.setattr(dispatch, "_validator_batch_summary", lambda jobs: {})
    monkeypatch.setattr(dispatch, "_emit_validator_batch_event", lambda *a, **kw: None)
    observer = Mock()
    with observe_stages(observer), stage_definition_scope(definitions), measure_flow_node("a"):
        if batch:
            results, summary = dispatch._run_validator_job_batch([job], batch_runner=runner, event_emitter=None)
            assert results == [result] and summary["status"] == ("error" if failed else "completed")
        else:
            assert dispatch._run_single_validator_job(job, agent_runner=runner) is result
    assert observer.started.call_count == observer.completed.call_count == 2
    child, parent = [call.args[0] for call in observer.completed.call_args_list]
    assert child.elapsed_ms == 250
    assert child.status == ("failed" if failed else "succeeded")
    assert child.failure_type == ("RuntimeError" if failed else None)
    assert parent.status == "succeeded"  # The ordinary unresolved result still returns normally.
