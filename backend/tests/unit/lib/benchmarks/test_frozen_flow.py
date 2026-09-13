from copy import deepcopy
from uuid import uuid4

import pytest

from src.lib.benchmarks.frozen_flow import FrozenBenchmarkFlow, freeze_json
from src.lib.benchmarks.models import BenchmarkSuite, ResolvedBenchmarkPlan
from src.lib.benchmarks.models import BenchmarkTargetCatalogEntry, ResolvedBenchmarkCell
from src.lib.benchmarks.suites import resolve_suite
from tests.unit.lib.benchmarks.test_suites import _catalog
from tests.unit.lib.flows.test_execution_revisions import flow


def snapshot(definition=None):
    return FrozenBenchmarkFlow(
        source_kind="recipe", source_id="Extraction Flow", source_revision="sha256:" + "a" * 64,
        title="Extraction Flow", description="Extract experimental entities",
        definition=definition or flow(None).model_dump(mode="json"),
    )


def test_nested_definition_is_detached_immutable_and_round_trips():
    original = flow(None).model_dump(mode="json")
    frozen = snapshot(original)
    expected = frozen.model_dump(mode="json")
    original["nodes"][0]["data"]["task_instructions"] = "Changed later"
    assert frozen.model_dump(mode="json") == expected
    with pytest.raises(TypeError):
        frozen.definition["nodes"][0]["data"]["task_instructions"] = "Mutation"
    exported = frozen.model_dump(mode="json")
    exported["definition"]["nodes"][0]["data"]["task_instructions"] = "Export mutation"
    assert frozen.model_dump(mode="json") == expected
    assert FrozenBenchmarkFlow.model_validate_json(frozen.model_dump_json()) == frozen


@pytest.mark.parametrize("value", [float("nan"), float("inf"), b"bytes", {1: "not a JSON key"}])
def test_non_json_snapshot_values_rejected(value):
    with pytest.raises(ValueError):
        freeze_json({"nested": [value]})


def test_saved_flow_identity_requires_uuid():
    args = snapshot().model_dump(mode="json")
    args.update(source_kind="saved_flow", source_id="display title")
    with pytest.raises(ValueError):
        FrozenBenchmarkFlow.model_validate(args)
    args["source_id"] = str(uuid4())
    assert FrozenBenchmarkFlow.model_validate(args).source_id == args["source_id"]


@pytest.mark.parametrize("container", [BenchmarkTargetCatalogEntry, ResolvedBenchmarkCell])
@pytest.mark.parametrize("mismatch", ["id", "revision", "kind", "missing", "recipe"])
def test_selected_flow_identity_is_checked_at_catalog_and_durable_cell_boundaries(container, mismatch):
    flow_id = str(uuid4())
    source = snapshot().model_dump(mode="json")
    source.update(source_kind="saved_flow", source_id=flow_id)
    target = {"kind": "flow", "id": flow_id, "source_kind": "saved_flow",
              "source_revision": source["source_revision"]}
    payload = {"target": target, "flow_snapshot": source}
    if container is BenchmarkTargetCatalogEntry:
        payload["route_slots"] = ("supervisor",)
    else:
        payload.update(cell_id="cell", case_id="paper", configuration_id="baseline", repetition=1,
                       input={"resolver": "fixture", "reference": "paper", "version": "1",
                              "digest": "sha256:" + "b" * 64},
                       routes={"supervisor": {"provider": "provider-a", "model": "model-a"}})
    assert container.model_validate(payload).flow_snapshot.source_id == flow_id
    if mismatch == "id":
        source["source_id"] = str(uuid4())
    elif mismatch == "revision":
        source["source_revision"] = "sha256:" + "c" * 64
    elif mismatch == "kind":
        target.pop("source_kind")
        target.pop("source_revision")
    elif mismatch == "missing":
        payload.pop("flow_snapshot")
    else:
        source["source_kind"] = "recipe"
    with pytest.raises(ValueError):
        container.model_validate(payload)


def test_flow_snapshot_changes_cell_and_plan_identity_without_rewriting_historical_plans():
    catalog = _catalog()
    target = next(item for item in catalog.targets if item.target.kind == "flow")
    suite = BenchmarkSuite.model_validate({
        "schema_version": 2, "suite_id": "frozen-flow", "cases": [{
            "case_id": "paper", "target": target.target.model_dump(mode="json"),
            "input": {"resolver": "fixture", "reference": "paper", "version": "1",
                      "digest": "sha256:" + "b" * 64},
        }], "configurations": [{"configuration_id": "baseline"}],
    })
    limits = dict(max_cases=1, max_configurations=1, max_repetitions=1, max_cells=1)
    historical = resolve_suite(suite, catalog, **limits)
    assert "flow_snapshot" not in historical.cells[0].model_dump(mode="json")

    def plan_for(selected):
        frozen_target = target.model_copy(update={"flow_snapshot": selected})
        current = catalog.model_copy(update={"targets": tuple(
            frozen_target if item is target else item for item in catalog.targets
        )})
        return resolve_suite(suite, current, **limits)

    first = plan_for(snapshot())
    changed = deepcopy(snapshot().model_dump(mode="json"))
    changed["definition"]["nodes"][0]["data"]["task_instructions"] = "Different extraction task"
    second = plan_for(FrozenBenchmarkFlow.model_validate(changed))
    assert first.suite_digest == second.suite_digest
    assert first.catalog_digest != second.catalog_digest
    assert first.cells[0].cell_id != second.cells[0].cell_id
    assert first.plan_digest != second.plan_digest
    assert first.cells[0].flow_snapshot == snapshot()
    assert ResolvedBenchmarkPlan.model_validate_json(first.model_dump_json()) == first
    assert resolve_suite(suite, catalog, **limits) == historical
