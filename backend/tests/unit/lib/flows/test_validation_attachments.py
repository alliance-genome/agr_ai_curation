"""Tests for metadata-backed flow validation attachments."""

from __future__ import annotations

import pytest

from src.lib.domain_packs.validation_registry import (
    ValidationAttachmentOption,
    ValidationBindingState,
)
from src.lib.flows import validation_attachments as validation_attachments_module
from src.lib.flows.validation_attachments import (
    FlowValidationAttachmentError,
    apply_flow_validation_attachment_defaults,
    validation_attachment_catalog_by_agent,
    validation_schedule_from_node_data,
)
from src.schemas.domain_envelope import DefinitionState
from src.schemas.flows import FlowDefinition


def _flow_definition(
    agent_id: str = "disease_extractor",
    attachments=None,
    *,
    extra_nodes=None,
    edges=None,
) -> FlowDefinition:
    return FlowDefinition(
        version="1.1",
        nodes=[
            {
                "id": "task_1",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Initial Instructions",
                    "task_instructions": "Extract curation-ready objects",
                    "output_key": "task_input",
                },
            },
            {
                "id": "extract_1",
                "type": "agent",
                "position": {"x": 100, "y": 100},
                "data": {
                    "agent_id": agent_id,
                    "agent_display_name": agent_id,
                    "output_key": "extract_output",
                    **(
                        {"validation_attachments": attachments}
                        if attachments is not None
                        else {}
                    ),
                },
            },
            *(extra_nodes or []),
        ],
        edges=edges or [{"id": "e1", "source": "task_1", "target": "extract_1"}],
        entry_node_id="task_1",
    )


def _validator_node(node_id: str, output_key: str) -> dict:
    return {
        "id": node_id,
        "type": "agent",
        "position": {"x": 200, "y": 100},
        "data": {
            "agent_id": node_id,
            "agent_display_name": node_id,
            "output_key": output_key,
        },
    }


def _option(
    binding_id: str,
    *,
    attachment_id: str | None = None,
    label: str | None = None,
    state: ValidationBindingState = ValidationBindingState.ACTIVE,
    default_enabled: bool = True,
    allow_opt_out: bool = True,
    required: bool = False,
    blocking: bool = False,
) -> ValidationAttachmentOption:
    return ValidationAttachmentOption(
        attachment_id=attachment_id or f"fixture:binding:{binding_id}",
        domain_pack_id="fixture.validation",
        domain_pack_version="0.1.0",
        validator_id=f"validator:{binding_id}",
        validator_binding_id=binding_id,
        state=state,
        scope="field",
        object_type="GeneAssertion",
        field_path=binding_id,
        label=label or binding_id,
        definition_state=DefinitionState.STABLE,
        required=required,
        export_blocking=blocking,
        default_enabled=default_enabled,
        allow_opt_out=allow_opt_out,
    )


def test_catalog_exposes_multiple_validation_options_for_extraction_agent():
    catalog = validation_attachment_catalog_by_agent()

    options = catalog["disease_extractor"]
    states = {option["state"] for option in options}
    condition_ontology_options = [
        option
        for option in options
        if option.get("validator_binding_id")
        == "disease_ontology_term_lookup"
    ]

    assert len(options) >= 3
    assert "active" in states
    assert "under_development" in states
    assert any(option["default_enabled"] for option in options if option["state"] == "active")
    assert condition_ontology_options
    assert {
        option["validator_agent_id"] for option in condition_ontology_options
    } == {"ontology_term_validation"}
    assert "ontology_mapping" not in catalog
    assert "ontology_mapping_lookup" not in catalog


def test_apply_defaults_selects_active_and_keeps_under_development_visible():
    flow = _flow_definition()

    hydrated = apply_flow_validation_attachment_defaults(flow)
    attachments = hydrated.nodes[1].data.validation_attachments

    assert any(
        attachment.enabled and attachment.state == "active"
        for attachment in attachments
    )
    assert any(
        attachment.state == "under_development" and not attachment.enabled
        for attachment in attachments
    )


@pytest.mark.parametrize("agent_id", ["disease_extractor", "missing_agent"])
def test_save_hydration_rejects_retired_selections_before_catalog_lookup(agent_id):
    from src.lib.packages.persisted_flow_migration_loader import (
        load_persisted_flow_migration_catalog,
    )

    migration = load_persisted_flow_migration_catalog().migrations[0]
    retired = migration.retired_attachments[0]
    flow = _flow_definition(
        agent_id=agent_id,
        attachments=[{
            "attachment_id": retired.attachment_id,
            "validator_binding_id": retired.validator_binding_id,
            "domain_pack_id": "fixture.validation",
            "validator_id": "fixture:retired",
            "state": "under_development",
            "scope": "field",
        }],
    )
    original = flow.model_dump()

    with pytest.raises(FlowValidationAttachmentError, match="retired validation references"):
        apply_flow_validation_attachment_defaults(flow)

    assert flow.model_dump() == original


def test_apply_defaults_preserves_allowed_opt_out_selection():
    agent_registry = {
        "fixture_extractor": {
            "curation": {"domain_pack_id": "agr" + ".alliance.disease"}
        }
    }
    initial = apply_flow_validation_attachment_defaults(
        _flow_definition("fixture_extractor"),
        agent_registry=agent_registry,
    )
    opt_out_attachment = next(
        attachment
        for attachment in initial.nodes[1].data.validation_attachments
        if attachment.state == "active" and attachment.allow_opt_out
    )

    opt_out_attachment.enabled = False

    hydrated = apply_flow_validation_attachment_defaults(
        _flow_definition(
            "fixture_extractor",
            attachments=[attachment.model_dump() for attachment in initial.nodes[1].data.validation_attachments],
        ),
        agent_registry=agent_registry,
    )
    updated_attachment = next(
        attachment
        for attachment in hydrated.nodes[1].data.validation_attachments
        if attachment.attachment_id == opt_out_attachment.attachment_id
    )

    assert updated_attachment.enabled is False




def test_validation_groups_keep_under_development_metadata_out_of_runtime_states(
    monkeypatch,
):
    options = (
        _option("identifier", label="Identifier validator"),
        _option(
            "future-reference",
            label="Future reference validator",
            state=ValidationBindingState.UNDER_DEVELOPMENT,
            default_enabled=False,
            allow_opt_out=False,
        ),
    )
    monkeypatch.setattr(
        validation_attachments_module,
        "validation_attachment_options_for_agent",
        lambda agent_id, agent_registry=None: options if agent_id == "fixture_extractor" else (),
    )

    hydrated = apply_flow_validation_attachment_defaults(
        _flow_definition("fixture_extractor")
    )
    node_data = hydrated.nodes[1].data

    assert [
        attachment.validator_binding_id
        for attachment in node_data.validation_attachments
    ] == ["identifier", "future-reference"]
    assert [
        group.binding_id
        for group in node_data.validation_groups
    ] == ["identifier"]
    assert {
        group.state
        for group in node_data.validation_groups
    } == {"automatic"}

    schedule = validation_schedule_from_node_data(node_data.model_dump())
    assert [item["validator_binding_id"] for item in schedule["scheduled_validators"]] == [
        "identifier"
    ]
    assert schedule["opt_outs"] == []
    assert [item["validator_binding_id"] for item in schedule["inactive_metadata"]] == [
        "future-reference"
    ]








def test_apply_defaults_rejects_unknown_attachment_ids():
    flow = _flow_definition(
        attachments=[
            {
                "attachment_id": "unknown",
                "domain_pack_id": "fixture.validation",
                "validator_id": "unknown",
                "state": "active",
                "scope": "pack",
                "enabled": False,
            }
        ],
    )

    with pytest.raises(FlowValidationAttachmentError, match="Unknown validation"):
        apply_flow_validation_attachment_defaults(flow)




def test_validation_schedule_rejects_unexpected_attachment_types():
    with pytest.raises(
        FlowValidationAttachmentError,
        match="Unexpected validation attachment type: object",
    ):
        validation_schedule_from_node_data({"validation_attachments": [object()]})


def test_curator_wording_survives_canonical_defaults_and_saved_roundtrip(monkeypatch):
    from dataclasses import replace

    option = replace(
        _option("subject_gene"),
        curator_label="Validate the gene named in the paper",
        when_off="Keep the extracted gene name without database validation.",
    )
    monkeypatch.setattr(
        validation_attachments_module,
        "validation_attachment_options_for_agent",
        lambda agent_id, **kwargs: (option,) if agent_id == "disease_extractor" else (),
    )
    hydrated = apply_flow_validation_attachment_defaults(_flow_definition())
    reopened = FlowDefinition.model_validate_json(hydrated.model_dump_json())
    selection = reopened.nodes[1].data.validation_attachments[0].model_dump()
    assert selection["curator_label"] == option.curator_label
    assert selection["when_off"] == option.when_off
    assert selection["enabled"] is True


def test_profile_attachment_resolution_uses_authoring_transaction(monkeypatch):
    """A new profile must be visible before the atomic flow update commits."""
    from types import SimpleNamespace
    from src.lib.domain_packs import profile_validation
    from src.schemas.agent_execution_revision import AgentExecutionReceipt

    transaction = object()
    receipt = object()
    pack = object()
    context = object()
    monkeypatch.setattr(validation_attachments_module, '_domain_pack_validation_registries',
                        lambda: {'generic': SimpleNamespace(domain_pack=pack)})
    monkeypatch.setattr(AgentExecutionReceipt, 'model_validate', lambda _: receipt)

    def resolve(actual_receipt, actual_pack, **kwargs):
        assert actual_receipt is receipt
        assert actual_pack is pack
        assert kwargs['db'] is transaction
        assert kwargs['user_id'] == 8
        assert kwargs['active_group_ids'] == ['GROUP_A']
        return context

    monkeypatch.setattr(profile_validation, 'resolve_profile_validation', resolve)
    monkeypatch.setattr(profile_validation, 'profile_validation_attachment_options',
                        lambda value: () if value is context else pytest.fail('Wrong context'))
    flow = _flow_definition('custom_extractor')
    hydrated = apply_flow_validation_attachment_defaults(
        flow, db=transaction, entries_by_node={'extract_1': {
            'curation': {'domain_pack_id': 'generic'}, 'execution_receipt': {},
            'authenticated_user_id': 8, 'authenticated_group_ids': ['GROUP_A'],
        }},
    )
    assert hydrated.nodes[1].data.validation_attachments == []


@pytest.mark.parametrize("enabled", [True, False])
def test_revision_reconciliation_preserves_choice_across_profile_fingerprint(monkeypatch, enabled):
    from dataclasses import replace
    old = _option("profile-" + "a" * 64 + "-validator_1")
    new = replace(old, attachment_id=old.attachment_id.replace("a" * 64, "b" * 64),
                  validator_binding_id=old.validator_binding_id.replace("a" * 64, "b" * 64))
    original = _flow_definition(attachments=[{**old.to_dict(), "enabled": enabled}])
    monkeypatch.setattr(validation_attachments_module, "_options_for_agent_entry", lambda entry, db=None: (new,))
    result = validation_attachments_module.reconcile_revision_attachments(
        original, entries_by_node={"extract_1": {}}, retargeted_node_ids=frozenset({"extract_1"}),
    )
    selected = result.nodes[1].data.validation_attachments
    assert [(item.attachment_id, item.enabled) for item in selected] == [(new.attachment_id, enabled)]
    assert original.nodes[1].data.validation_attachments[0].attachment_id == old.attachment_id
    assert result.edges == original.edges


def test_revision_reconciliation_does_not_drop_explicit_validator_edges(monkeypatch):
    old, new = _option("old"), _option("new")
    original = _flow_definition(attachments=[{**old.to_dict(), "enabled": True}],
        extra_nodes=[_validator_node("validator", "validation_output")], edges=[
            {"id": "e1", "source": "task_1", "target": "extract_1"},
            {"id": "validation", "source": "extract_1", "target": "validator",
             "role": "validation_attachment", "replaces_attachment_id": old.attachment_id},
        ])
    monkeypatch.setattr(validation_attachments_module, "_options_for_agent_entry", lambda entry, db=None: (new,))
    with pytest.raises(FlowValidationAttachmentError, match="reconnect the validator"):
        validation_attachments_module.reconcile_revision_attachments(
            original, entries_by_node={"extract_1": {}}, retargeted_node_ids=frozenset({"extract_1"}),
        )
    assert len(original.edges) == 2


def test_revision_reconciliation_refuses_to_silently_enable_unmatched_optout(monkeypatch):
    original = _flow_definition(attachments=[{**_option("old").to_dict(), "enabled": False}])
    monkeypatch.setattr(validation_attachments_module, "_options_for_agent_entry", lambda entry, db=None: (_option("new"),))
    with pytest.raises(FlowValidationAttachmentError, match="disabled check cannot be matched"):
        validation_attachments_module.reconcile_revision_attachments(
            original, entries_by_node={"extract_1": {}}, retargeted_node_ids=frozenset({"extract_1"}),
        )


@pytest.mark.parametrize("agent", ["ca_saved_validator", "packaged_validator"])
@pytest.mark.parametrize("edge_field", ["satisfies_binding_id", "replaces_attachment_id"])
def test_override_edges_are_readable_but_rejected_without_mutating_saved_flow(agent, edge_field):
    flow = _flow_definition("fixture_extractor", extra_nodes=[{
        "id": "validator", "type": "agent", "position": {"x": 200, "y": 100},
        "data": {"agent_id": agent, "agent_display_name": "Saved validator", "output_key": "validated"},
    }], edges=[{"id": "control", "source": "task_1", "target": "extract_1", "role": "control_flow"}, {"id": "old-link", "source": "extract_1", "target": "validator",
                "role": "validation_attachment", edge_field: "fixture-binding"}])
    saved = flow.model_dump(mode="json")
    # Parsing and displaying saved definitions remains possible; new execution is forbidden.
    assert FlowDefinition.model_validate(saved).model_dump(mode="json") == saved
    with pytest.raises(FlowValidationAttachmentError, match="Remove the validator attachment connection"):
        apply_flow_validation_attachment_defaults(flow)
    assert flow.model_dump(mode="json") == saved


@pytest.mark.parametrize("state", ["replaced", "supplemental"])
def test_saved_override_groups_without_edges_cannot_silently_become_automatic(state):
    flow = _flow_definition("fixture_extractor")
    from src.schemas.flows import FlowValidationAttachmentGroup
    flow.nodes[1].data.validation_groups = [FlowValidationAttachmentGroup(
        group_id="saved", binding_id="fixture", state=state, validator_node_id="old-validator",
        attachment_id=None, edge_id=None, replaces_attachment_id=None, label=None)]
    with pytest.raises(FlowValidationAttachmentError, match="not supported"):
        apply_flow_validation_attachment_defaults(flow)
    # Read-only schedule inspection can still show the unsupported saved choice.
    validation_schedule_from_node_data(flow.nodes[1].data.model_dump())


def test_schedule_retains_automatic_optout_and_inactive_metadata():
    schedule = validation_schedule_from_node_data({"validation_attachments": [
        {"attachment_id": "active", "validator_binding_id": "active", "state": "active", "enabled": True},
        {"attachment_id": "disabled", "validator_binding_id": "disabled", "state": "active", "enabled": False},
        {"attachment_id": "future", "state": "under_development", "enabled": False},
    ]})
    assert schedule["replacement_validators"] == schedule["supplemental_validators"] == []
    assert [x["attachment_id"] for x in schedule["scheduled_validators"]] == ["active"]
    assert [x["attachment_id"] for x in schedule["opt_outs"]] == ["disabled"]
    assert schedule["opt_outs"][0]["skipped_by_flow_configuration"] is True
    assert [x["attachment_id"] for x in schedule["inactive_metadata"]] == ["future"]
