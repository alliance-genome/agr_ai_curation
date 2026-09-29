from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

from fastapi import HTTPException
import pytest

from src.lib.agent_studio.authoring_validation import AuthoringValidationFinding
from src.lib.benchmarks import saved_flows as service
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.flow_contracts import BenchmarkFlowOutputContract
from src.lib.benchmarks.flow_stages import BenchmarkFlowStage
from src.schemas.flows import FlowDefinition
from tests.unit.lib.flows.test_execution_revisions import flow
from tests.unit.lib.benchmarks.test_source_revisions import source_receipt

VERIFIED = BenchmarkFlowOutputContract(
    status="verified", representation="json_schema", schema_definition={"type": "object"},
    schema_digest="sha256:" + "e" * 64)


def finding(code, node_id="node_0", message="private contract data"):
    return AuthoringValidationFinding(code=code, severity="error", path="p", message=message,
                                      node_id=node_id)


def two_step_flow():
    """A custom step (node_0) then a system step (node_1)."""
    return FlowDefinition.model_validate({
        "nodes": [
            {"id": "task", "type": "task_input", "position": {"x": 0, "y": 0},
             "data": {"agent_id": "task_input", "agent_display_name": "Task",
                      "output_key": "task", "task_instructions": "Extract"}},
            {"id": "node_0", "type": "agent", "position": {"x": 100, "y": 100},
             "data": {"agent_id": "ca_fixture", "agent_display_name": "Fixture",
                      "output_key": "result_0"}},
            {"id": "node_1", "type": "agent", "position": {"x": 200, "y": 100},
             "data": {"agent_id": "disease_extractor", "agent_display_name": "Disease",
                      "output_key": "result_1"}},
        ],
        "entry_node_id": "task",
        "edges": [{"id": "e0", "source": "task", "target": "node_0"},
                  {"id": "e1", "source": "node_0", "target": "node_1"}],
    })


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
    monkeypatch.setattr(service, "step_output_kind", lambda agent_id, metadata, contract: "text")
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


def test_revision_changes_when_saved_definition_changes(setup):
    original = service.flow_summary(setup.row).revision
    setup.row.flow_definition["nodes"][0]["data"]["task_instructions"] = "New task"
    assert service.flow_summary(setup.row).revision != original


@pytest.fixture
def pair(setup, monkeypatch):
    definition = two_step_flow()
    setup.resolved.definition = definition
    setup.resolved.entries_by_node = {"node_0": {"execution_receipt": {}}}
    system = Mock(return_value={"curation": {"domain_pack_id": "agr.alliance.disease"}})
    monkeypatch.setattr(service, "get_active_visible_agent_metadata", system)
    setup.discover.return_value = VERIFIED
    setup.discover.side_effect = None
    monkeypatch.setattr(service, "step_output_kind", lambda agent_id, metadata, contract: (
        "profile_attributes" if agent_id.startswith("ca_") else "pack_fields"))
    stages = Mock(return_value=())
    monkeypatch.setattr(service, "flow_stages", stages)
    setup.system, setup.stages, setup.definition = system, stages, definition
    return setup


def test_unavailable_custom_revision_exposes_no_contracts(setup):
    setup.resolved.entries_by_node = {"node_0": None}
    setup.resolved.findings = (finding("unavailable_execution_revision"),)
    result = service.saved_flow_contracts(Mock(), setup.curator, setup.row.id)
    node = result.nodes[0]
    assert (node.problem, node.output_kind) == ("unavailable_agent", "unavailable")
    assert node.contract.status == "not_verified" and node.contract.execution_receipt is None
    assert node.contract.reason == service.UNAVAILABLE_AGENT_REASON
    assert result.runnable is False and result.stages == ()
    assert result.run_problem == "A step's agent isn't available to you."
    assert "private contract data" not in result.model_dump_json()
    setup.discover.assert_not_called()


def test_schema_failure_is_per_step_and_sanitized(pair):
    pair.discover.side_effect = [ValueError("secret private profile definition"), VERIFIED]
    result = service.saved_flow_contracts(Mock(), pair.curator, pair.row.id)
    first, second = result.nodes
    assert (first.problem, first.output_kind) == ("unreadable_structure", "unavailable")
    assert first.contract.reason == service.UNREADABLE_STRUCTURE_REASON
    assert second.problem is None and second.contract.status == "verified"
    assert result.runnable is False and result.stages == ()
    assert result.run_problem == service.CANNOT_RUN
    assert "secret" not in result.model_dump_json()


def test_unrelated_failing_step_keeps_the_other_steps_structure(pair):
    pair.resolved.entries_by_node = {"node_0": None}
    pair.resolved.findings = (finding("unavailable_execution_revision"),)
    result = service.saved_flow_contracts(Mock(), pair.curator, pair.row.id)
    assert result.nodes[0].problem == "unavailable_agent"
    assert result.nodes[1].contract == VERIFIED and result.nodes[1].output_kind == "pack_fields"
    assert pair.discover.call_count == 1
    pair.stages.assert_not_called()


def test_needs_resave_step_keeps_its_structure_from_the_resolved_receipt(pair, monkeypatch):
    receipt = source_receipt("ca_fixture")
    pair.definition.nodes[1].data.execution_receipt = receipt
    snapshot = NS(template_source=None, tool_ids=[], output_contract=receipt.output_contract)
    monkeypatch.setattr(service, "get_execution_revision", Mock(return_value=(None, snapshot)))
    pair.resolved.entries_by_node = {"node_0": None}
    pair.resolved.findings = (finding("unavailable_model"),)
    result = service.saved_flow_contracts(Mock(), pair.curator, pair.row.id)
    node = result.nodes[0]
    assert node.problem == "needs_resave" and node.contract == VERIFIED
    first_call = pair.discover.call_args_list[0]
    assert first_call.kwargs["metadata"] == {"execution_receipt": receipt.model_dump(mode="json")}
    assert result.runnable is False and result.stages == ()
    assert result.run_problem == service.RESAVE_MODEL
    assert node.output_kind == "profile_attributes"


def test_supplied_receipt_on_an_unauthorized_step_is_never_read(pair):
    # Review Focus 1: recovery keys on the finding code, not on a receipt being present.
    pair.definition.nodes[1].data.execution_receipt = source_receipt("ca_fixture")
    pair.resolved.entries_by_node = {"node_0": None}
    pair.resolved.findings = (finding("unavailable_execution_revision"),)
    result = service.saved_flow_contracts(Mock(), pair.curator, pair.row.id)
    assert result.nodes[0].problem == "unavailable_agent"
    assert all(call.kwargs["agent_id"] != "ca_fixture" for call in pair.discover.call_args_list)


def test_hidden_system_step_is_unavailable_not_a_flow_failure(pair):
    # Review Focus 3: the visible-agent service raises instead of returning None.
    pair.system.side_effect = ValueError("Agent 'disease_extractor' is not visible")
    result = service.saved_flow_contracts(Mock(), pair.curator, pair.row.id)
    assert result.nodes[1].problem == "unavailable_agent"
    assert result.nodes[0].problem is None
    assert result.runnable is False and result.run_problem == "A step's agent isn't available to you."
    assert "not visible" not in result.model_dump_json()


def test_projection_run_problem_never_names_fields(pair):
    pair.resolved.findings = (finding(
        "undeclared_projection_field", node_id="node_1",
        message="Field 'object.attribute.secret_field' is not declared"),)
    result = service.saved_flow_contracts(Mock(), pair.curator, pair.row.id)
    assert result.run_problem == service.CHOOSE_FIELDS_AGAIN
    assert all(node.problem is None for node in result.nodes)
    assert "secret_field" not in result.model_dump_json()


@pytest.mark.parametrize("code,expected", [
    ("unavailable_model", "A step uses a model or setting that is no longer available. "
                          "Open the flow in AI Curation and re-save that step's agent."),
    ("unsupported_reasoning_effort", "A step uses a model or setting that is no longer "
                                     "available. Open the flow in AI Curation and re-save "
                                     "that step's agent."),
    ("extraction_identity_lookup_tools", "A step's agent still has database lookup tools. "
                                         "Open it in AI Curation and re-save it without them."),
    ("missing_execution_revision", "A step's agent isn't available to you."),
    ("execution_contract_mismatch", "A step's agent isn't available to you."),
    ("invalid_profile_projection", "A file output step needs its fields chosen again. "
                                   "Open the flow in AI Curation."),
    ("incompatible_projection_field_type", "A file output step needs its fields chosen again. "
                                          "Open the flow in AI Curation."),
    ("something_new", "This flow can't run right now. Open it in AI Curation and check its "
                      "steps and validators."),
])
def test_run_problem_is_a_fixed_sentence_per_code(code, expected):
    assert service.run_problem_for_code(code) == expected


def test_runnable_flow_reports_validated_bindings_per_step(pair):
    pair.stages.return_value = (
        BenchmarkFlowStage(stage_id="supervisor", node_id=None, title="Flow supervisor",
                           role="supervisor", route_slot="supervisor"),
        BenchmarkFlowStage(stage_id="v1", node_id="node_0", source_node_id="node_0",
                           title="Term check", role="validation", route_slot=None,
                           binding_id="disease_ontology_term_lookup"),
        BenchmarkFlowStage(stage_id="v2", node_id="checker", source_node_id="node_1",
                           title="Subject check", role="validation", route_slot=None,
                           binding_id="disease_subject_materialization"),
    )
    result = service.saved_flow_contracts(Mock(), pair.curator, pair.row.id)
    assert result.runnable is True and result.run_problem is None
    assert result.nodes[0].validated_bindings == ("disease_ontology_term_lookup",)
    assert result.nodes[1].validated_bindings == ("disease_subject_materialization",)
    assert len(result.stages) == 3


def test_stage_failure_keeps_steps_but_is_not_runnable(pair):
    pair.stages.side_effect = ValueError("Validator binding is not in the authorized profile")
    result = service.saved_flow_contracts(Mock(), pair.curator, pair.row.id)
    assert len(result.nodes) == 2 and result.status == "verified"
    assert result.runnable is False and result.stages == ()
    assert result.run_problem == service.CANNOT_RUN
    assert all(node.validated_bindings == () for node in result.nodes)


def test_unreadable_flow_definition_keeps_the_whole_flow_reason(setup):
    setup.row.flow_definition = {"nodes": "not a list"}
    result = service.saved_flow_contracts(Mock(), setup.curator, setup.row.id)
    assert result.nodes == () and result.runnable is False
    assert result.reason is not None
    assert result.reason.startswith("A flow node or its saved output structure is unavailable")


def test_output_kind_failure_is_a_per_step_unreadable_structure(pair, monkeypatch):
    def kind(agent_id, metadata, contract):
        if agent_id.startswith("ca_"):
            raise ValueError("secret receipt detail")
        return "pack_fields"
    monkeypatch.setattr(service, "step_output_kind", kind)
    result = service.saved_flow_contracts(Mock(), pair.curator, pair.row.id)
    first, second = result.nodes
    assert (first.problem, first.output_kind) == ("unreadable_structure", "unavailable")
    assert first.contract.reason == service.UNREADABLE_STRUCTURE_REASON
    assert second.problem is None and second.output_kind == "pack_fields"
    assert result.runnable is False and result.stages == ()
    assert "secret" not in result.model_dump_json()


def test_needs_resave_formatter_copy_keeps_its_formatter_kind(pair, monkeypatch):
    from src.lib.benchmarks.flow_contracts import step_output_kind
    receipt = source_receipt("ca_fixture")
    pair.definition.nodes[1].data.execution_receipt = receipt
    pair.resolved.entries_by_node = {"node_0": None}
    pair.resolved.findings = (finding("unavailable_model"),)
    pair.discover.side_effect = [
        BenchmarkFlowOutputContract(status="not_verified", execution_receipt=receipt,
                                    reason="Undeclared"),
        VERIFIED,
    ]
    snapshot = NS(template_source="tsv_formatter", tool_ids=["finalize_and_save"],
                  output_contract=receipt.output_contract)
    revision = Mock(return_value=(NS(id=receipt.agent_revision_id), snapshot))
    monkeypatch.setattr(service, "get_execution_revision", revision)
    monkeypatch.setattr(service, "step_output_kind", step_output_kind)
    session = Mock()
    result = service.saved_flow_contracts(session, pair.curator, pair.row.id)
    node = result.nodes[0]
    assert (node.problem, node.output_kind) == ("needs_resave", "formatter")
    assert revision.call_args.args == (session, receipt.agent_id, receipt.agent_revision_id, 42)
    assert revision.call_args.kwargs == {"active_group_ids": ["group-a"]}
