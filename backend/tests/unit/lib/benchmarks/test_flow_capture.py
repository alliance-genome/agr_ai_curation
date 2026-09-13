from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.lib.benchmarks import flow_capture, runtime
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.saved_flows import flow_summary
from tests.unit.lib.benchmarks.test_runtime import _resolved_cell
from tests.unit.lib.flows.test_execution_revisions import flow


@pytest.fixture
def setup(monkeypatch):
    definition = flow(None)
    row = NS(id=uuid4(), name="Original flow", description="Original description",
             flow_definition=definition.model_dump(mode="json"))
    curator = BenchmarkCuratorContext(subject="curator", auth_provider="oidc", db_user_id=7, active_groups=())
    access = Mock(return_value=row)
    resolve = Mock(return_value=NS(definition=definition, entries_by_node={}, findings=()))
    monkeypatch.setattr(flow_capture, "get_visible_flow", access)
    monkeypatch.setattr(flow_capture, "resolve_flow_execution_revisions", resolve)
    monkeypatch.setattr(flow_capture, "apply_flow_validation_attachment_defaults", lambda definition, **kw: definition)
    return NS(row=row, curator=curator, access=access, resolve=resolve)


def capture(setup):
    return flow_capture.capture_saved_flow(Mock(), setup.curator, setup.row.id,
                                           expected_revision=flow_summary(setup.row).revision)


@pytest.mark.parametrize("failure", ["access", "drift", "node"])
def test_capture_rejects_before_publishing_snapshot(setup, failure):
    revision = flow_summary(setup.row).revision
    if failure == "access":
        setup.access.side_effect = HTTPException(403, "Access denied")
    elif failure == "drift":
        setup.row.name = "Edited since discovery"
    else:
        setup.resolve.return_value.findings = (NS(severity="error"),)
    with pytest.raises((HTTPException, ValueError)):
        flow_capture.capture_saved_flow(Mock(), setup.curator, setup.row.id, expected_revision=revision)
    if failure in {"access", "drift"}:
        setup.resolve.assert_not_called()


def test_runtime_reauthorizes_identity_but_executes_original_flow(setup, monkeypatch):
    snapshot = capture(setup)
    cell = _resolved_cell("flow", str(setup.row.id), {}).model_copy(update={"flow_snapshot": snapshot})
    setup.row.name = "Later title"
    setup.row.flow_definition["nodes"][0]["data"]["task_instructions"] = "Later task"
    access = Mock(return_value=setup.row)
    monkeypatch.setattr("src.lib.flows.access.get_visible_flow", access)
    db = Mock()
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: nullcontext(db))
    selected = runtime._flow_from_frozen_cell(cell, {"db_user_id": 7})
    access.assert_called_once_with(db, setup.row.id, 7)
    assert selected.name == "Original flow"
    assert selected.description == "Original description"
    assert selected.flow_definition == snapshot.model_dump(mode="json")["definition"]
    assert selected.flow_definition != setup.row.flow_definition
    # The runtime receives an independent mutable copy, not the plan object.
    selected.flow_definition["nodes"].clear()
    assert snapshot.definition["nodes"]
    access.side_effect = HTTPException(403, "Sharing revoked")
    with pytest.raises(HTTPException):
        runtime._flow_from_frozen_cell(cell, {"db_user_id": 7})


def test_runtime_rejects_mismatched_target_or_missing_curator(setup):
    snapshot = capture(setup)
    cell = _resolved_cell("flow", str(uuid4()), {}).model_copy(update={"flow_snapshot": snapshot})
    with pytest.raises(ValueError, match="does not match"):
        runtime._flow_from_frozen_cell(cell, {"db_user_id": 7})
    cell = cell.model_copy(update={"target": cell.target.model_copy(update={"id": snapshot.source_id})})
    with pytest.raises(ValueError, match="authenticated"):
        runtime._flow_from_frozen_cell(cell, {})


def test_recipe_capture_and_runtime_ignore_later_recipe_edits_but_recheck_visibility(setup, monkeypatch):
    from src.lib.benchmarks.flow_contracts import BenchmarkFlowOutputContract
    metadata = Mock(return_value={})
    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_active_visible_agent_metadata", metadata)
    contract = BenchmarkFlowOutputContract(status="verified", representation="json_schema", schema_definition={"type": "object"})
    monkeypatch.setattr("src.lib.benchmarks.flow_contracts.discover_output_contract", lambda *a, **kw: contract)
    recipe = {"name": "Recipe", "description": "Original task", "steps": [{"agent_id": "extractor"}]}
    frozen = flow_capture.capture_recipe_flow(Mock(), setup.curator, recipe, flow(None))
    assert frozen.output_contracts["node_0"]["contract"]["status"] == "verified"
    cell = _resolved_cell("flow", "Recipe", {}).model_copy(update={"flow_snapshot": frozen})
    recipe["description"] = "Changed later"
    listing = Mock(return_value=[recipe])
    monkeypatch.setattr(runtime, "load_benchmark_flow_templates", listing)
    current = runtime._flow_from_frozen_cell(cell, {"db_user_id": 7, "active_groups": []})
    assert current.description == "Original task"
    assert current.flow_definition == frozen.model_dump(mode="json")["definition"]
    later = flow_capture.capture_recipe_flow(Mock(), setup.curator, recipe, flow(None))
    assert later.source_revision != frozen.source_revision
    listing.return_value = []
    with pytest.raises(ValueError):
        runtime._flow_from_frozen_cell(cell, {"db_user_id": 7, "active_groups": []})
