from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from src.lib.benchmarks import flow_stages as stages
from tests.unit.lib.flows.test_execution_revisions import flow
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext


def test_repeated_agent_nodes_share_route_without_merging_stage_identity():
    result = stages.flow_stages(
        flow(None, None),
        {"node_0": {"category": "Extraction"}, "node_1": {"category": "Validation"}},
        authorize_validator=Mock(),
    )
    assert [item.role for item in result] == ["supervisor", "extraction", "validation"]
    assert result[1].route_slot == result[2].route_slot == "agent:ca_fixture"
    assert result[1].stage_id != result[2].stage_id


def test_deterministic_and_model_validators_have_explicit_roles(monkeypatch):
    monkeypatch.setattr(stages, "validation_schedule_from_node_data", lambda data: {
        "scheduled_validators": [
            {"validator_binding_id": "lookup", "validator_id": "Identifier check"},
            {"validator_binding_id": "semantic", "validator_id": "Context check"},
        ],
    })
    authorize = Mock(side_effect=[None, "semantic_agent"])
    result = stages.flow_stages(flow(None), {"node_0": {"category": "Extraction"}}, authorize_validator=authorize)
    assert result[2].role == result[3].role == "validation"
    assert result[2].route_slot is None
    assert result[3].route_slot == "validator:semantic"
    assert result[3].source_node_id == "node_0"
    assert authorize.call_count == 2


def test_unavailable_validator_fails_instead_of_silently_omitting_it(monkeypatch):
    monkeypatch.setattr(stages, "validation_schedule_from_node_data", lambda data: {
        "scheduled_validators": [{"validator_binding_id": "private"}],
    })
    with pytest.raises(ValueError, match="unavailable"):
        stages.flow_stages(flow(None), {"node_0": {}}, authorize_validator=Mock(side_effect=ValueError("unavailable")))


def test_sidecar_nodes_are_included_even_outside_control_order(monkeypatch):
    definition = flow(None, None)
    monkeypatch.setattr(stages, "apply_flow_validation_attachment_defaults", lambda value, **kwargs: value)
    monkeypatch.setattr(stages, "project_executable_flow_graph", lambda value: NS(
        ordered_executable_node_ids=("node_0",),
        validation_sidecars=(NS(validator_node_id="node_1", source_node_id="node_0", binding_id="sidecar"),),
    ))
    schedule = Mock(return_value={"scheduled_validators": []})
    monkeypatch.setattr(stages, "validation_schedule_from_node_data", schedule)
    result = stages.flow_stages(definition, {"node_0": {}, "node_1": {"category": "Custom"}}, authorize_validator=Mock())
    assert result[-1].role == "validation"
    assert result[-1].binding_id == "sidecar"
    assert result[-1].route_slot == "agent:ca_fixture"
    assert result[-1].source_node_id == "node_0"
    schedule.assert_called_once()  # Sidecar palette defaults are not control-step schedules.


def test_scheduled_package_validator_requires_current_visibility(monkeypatch):
    lookup = Mock(side_effect=ValueError("private validator"))
    monkeypatch.setattr("src.lib.config.agent_loader.get_agent_definition_for_package", lambda *args: object())
    monkeypatch.setattr("src.lib.config.agent_loader.canonical_system_agent_key", lambda value: "validator")
    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_active_visible_agent_metadata", lookup)
    curator = BenchmarkCuratorContext(subject="curator", auth_provider="oidc", db_user_id=42, active_groups=("group-a",))
    with pytest.raises(ValueError, match="private validator"):
        stages.authorize_scheduled_validator(Mock(), curator, {}, {
            "validator_agent_id": "validator", "validator_package_id": "pack",
            "validator_binding_id": "binding",
        })
    lookup.assert_called_once_with("validator", db_user_id=42, authenticated_groups=["group-a"])


def test_custom_profile_validator_retains_exact_authorized_receipt(monkeypatch):
    from uuid import uuid4
    from src.schemas.agent_execution_revision import AgentOutputContract, GenericProfilePin
    from tests.unit.lib.benchmarks.test_source_revisions import source_receipt

    source = source_receipt().model_copy(update={"output_contract": AgentOutputContract(
        output_state="structured_extraction", output_mode="profile_bound_generic",
        generic_profile_ref=GenericProfilePin(profile_id=uuid4(), profile_revision_id=uuid4(), revision=1, fingerprint="sha256:" + "a" * 64),
    )})
    validator = source_receipt().model_copy(update={"agent_key": "ca_validator"})
    pin = {"agent_id": str(validator.agent_id), "revision_id": str(validator.agent_revision_id),
           "agent_key": validator.agent_key, "fingerprint": validator.fingerprint}
    context = NS(unavailable=(), registry=NS(bindings=(NS(binding_id="binding", raw={"custom_validator": pin}),)))
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda key: object())
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.resolve_profile_validation", lambda *args, **kwargs: context)
    authorize = Mock(side_effect=[source, validator])
    monkeypatch.setattr("src.lib.agent_studio.execution_revision_service.authorize_execution_receipt", authorize)
    monkeypatch.setattr("src.lib.agent_studio.execution_revision_service.get_execution_revision", lambda *args, **kwargs: (
        NS(agent_id=validator.agent_id, id=validator.agent_revision_id, revision=validator.revision), NS(output_contract=validator.output_contract),
    ))
    curator = BenchmarkCuratorContext(subject="curator", auth_provider="oidc", db_user_id=42, active_groups=("group-a",))
    result = stages.authorize_scheduled_validator(Mock(), curator, {"execution_receipt": source.model_dump(mode="json")}, {"validator_binding_id": "binding"})
    assert result == validator
    assert authorize.call_args.args[1] == validator.model_dump(mode="json")
    assert authorize.call_args.args[2] == 42
