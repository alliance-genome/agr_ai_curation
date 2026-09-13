from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

from fastapi import HTTPException
import pytest

from src.lib.benchmarks import saved_flows as service
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.flow_contracts import BenchmarkFlowOutputContract
from tests.unit.lib.flows.test_execution_revisions import flow


@pytest.fixture
def setup(monkeypatch):
    curator = BenchmarkCuratorContext(subject="curator", auth_provider="oidc", db_user_id=42, active_groups=("group-a",))
    definition = flow(None)
    row = NS(id=uuid4(), name="My experiment", description="Extract entities", flow_definition=definition.model_dump(mode="json"))
    access = Mock(return_value=row)
    monkeypatch.setattr(service, "get_visible_flow", access)
    resolved = NS(definition=definition, findings=(), entries_by_node={"node_0": {"execution_receipt": {}}})
    resolve = Mock(return_value=resolved)
    monkeypatch.setattr(service, "resolve_flow_execution_revisions", resolve)
    discover = Mock(return_value=BenchmarkFlowOutputContract(status="not_verified", reason="Undeclared"))
    monkeypatch.setattr(service, "discover_output_contract", discover)
    monkeypatch.setattr(service, "stage_model_defaults", lambda session, curator, stages: (stages, ()))
    return NS(curator=curator, row=row, access=access, resolve=resolve, resolved=resolved, discover=discover)


def test_access_is_checked_before_reading_contracts(setup):
    setup.access.side_effect = HTTPException(403, "Access denied")
    with pytest.raises(HTTPException):
        service.saved_flow_contracts(Mock(), setup.curator, setup.row.id)
    setup.resolve.assert_not_called()
    setup.discover.assert_not_called()


def test_revision_drift_stops_selection_before_contract_reads(setup):
    with pytest.raises(ValueError, match="changed"):
        service.saved_flow_contracts(Mock(), setup.curator, setup.row.id, expected_revision="sha256:" + "0" * 64)
    setup.resolve.assert_not_called()


def test_unavailable_custom_revision_exposes_no_contracts(setup):
    setup.resolved.findings = (NS(severity="error", message="private contract data"),)
    result = service.saved_flow_contracts(Mock(), setup.curator, setup.row.id)
    assert result.nodes == () and result.status == "not_verified"
    assert "private contract data" not in result.model_dump_json()
    setup.discover.assert_not_called()


def test_authorized_saved_flow_has_identity_without_task_instructions(setup):
    session = Mock()
    result = service.saved_flow_contracts(session, setup.curator, setup.row.id)
    assert result.flow.source_kind == "saved_flow"
    assert result.flow.title == "My experiment"
    assert result.flow.revision.startswith("sha256:")
    assert result.nodes[0].output_key == "result_0"
    assert "task_instructions" not in result.model_dump_json()
    setup.access.assert_called_once_with(session, setup.row.id, 42)
    assert setup.resolve.call_args.kwargs == {"user_id": 42, "active_group_ids": ["group-a"]}
    session.commit.assert_not_called()
    session.add.assert_not_called()


def test_schema_failure_is_sanitized_without_partial_contracts(setup):
    setup.discover.side_effect = ValueError("secret private profile definition")
    result = service.saved_flow_contracts(Mock(), setup.curator, setup.row.id)
    assert result.nodes == () and result.status == "not_verified"
    assert "secret" not in result.model_dump_json()


def test_revision_changes_when_saved_definition_changes(setup):
    original = service.flow_summary(setup.row).revision
    setup.row.flow_definition["nodes"][0]["data"]["task_instructions"] = "New task"
    assert service.flow_summary(setup.row).revision != original
