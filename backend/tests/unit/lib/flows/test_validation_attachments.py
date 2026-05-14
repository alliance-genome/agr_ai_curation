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
    agent_id: str = "chemical_extractor",
    attachments=None,
    *,
    extra_nodes=None,
    edges=None,
) -> FlowDefinition:
    return FlowDefinition(
        version="1.0",
        nodes=[
            {
                "id": "task_1",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Initial Instructions",
                    "task_instructions": "Extract curation-ready objects",
                    "input_source": "user_query",
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
                    "input_source": "previous_output",
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
            "input_source": "previous_output",
            "output_key": output_key,
        },
    }


def _option(
    binding_id: str,
    *,
    attachment_id: str | None = None,
    label: str | None = None,
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
        state=ValidationBindingState.ACTIVE,
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

    options = catalog["chemical_extractor"]
    states = {option["state"] for option in options}

    assert len(options) >= 3
    assert "active" in states
    assert "planned" in states
    assert "blocked" in states
    assert any(option["default_enabled"] for option in options if option["state"] == "active")


def test_apply_defaults_selects_active_and_keeps_planned_blocked_visible():
    flow = _flow_definition()

    hydrated = apply_flow_validation_attachment_defaults(flow)
    attachments = hydrated.nodes[1].data.validation_attachments

    assert any(
        attachment.enabled and attachment.state == "active"
        for attachment in attachments
    )
    assert any(
        attachment.state == "planned" and not attachment.enabled
        for attachment in attachments
    )
    assert any(
        attachment.state == "blocked" and not attachment.enabled
        for attachment in attachments
    )


def test_apply_defaults_preserves_allowed_opt_out_selection():
    agent_registry = {
        "fixture_extractor": {
            "curation": {"domain_pack_id": "agr" + ".alliance.chemical_condition"}
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


def test_validation_attachment_edges_resolve_validator_group_states(monkeypatch):
    options = (
        _option(
            "identifier",
            attachment_id="fixture:binding:identifier",
            label="Identifier validator",
            required=True,
            blocking=True,
        ),
        _option(
            "symbol",
            attachment_id="fixture:binding:symbol",
            label="Symbol validator",
            default_enabled=True,
            allow_opt_out=True,
        ),
        _option(
            "quality",
            attachment_id="fixture:binding:quality",
            label="Quality validator",
            default_enabled=True,
        ),
    )
    monkeypatch.setattr(
        validation_attachments_module,
        "validation_attachment_options_for_agent",
        lambda agent_id, agent_registry=None: options if agent_id == "fixture_extractor" else (),
    )
    initial = apply_flow_validation_attachment_defaults(
        _flow_definition("fixture_extractor")
    )
    attachments = [
        attachment.model_dump()
        for attachment in initial.nodes[1].data.validation_attachments
    ]
    for attachment in attachments:
        if attachment["validator_binding_id"] == "symbol":
            attachment["enabled"] = False

    hydrated = apply_flow_validation_attachment_defaults(
        _flow_definition(
            "fixture_extractor",
            attachments=attachments,
            extra_nodes=[
                _validator_node("custom_identifier_validator", "custom_identifier"),
                _validator_node("supplemental_validator", "supplemental"),
            ],
            edges=[
                {"id": "e1", "source": "task_1", "target": "extract_1"},
                {
                    "id": "e2",
                    "source": "extract_1",
                    "target": "custom_identifier_validator",
                    "role": "validation_attachment",
                    "satisfies_binding_id": "identifier",
                },
                {
                    "id": "e3",
                    "source": "extract_1",
                    "target": "supplemental_validator",
                    "role": "validation_attachment",
                    "satisfies_binding_id": "custom.supplemental",
                },
            ],
        )
    )

    groups_by_binding = {
        group.binding_id: group
        for group in hydrated.nodes[1].data.validation_groups
    }

    assert groups_by_binding["identifier"].state == "replaced"
    assert groups_by_binding["identifier"].validator_node_id == (
        "custom_identifier_validator"
    )
    assert groups_by_binding["identifier"].blocking is True
    assert groups_by_binding["symbol"].state == "skipped"
    assert groups_by_binding["quality"].state == "automatic"
    assert groups_by_binding["custom.supplemental"].state == "supplemental"
    assert groups_by_binding["custom.supplemental"].blocking is False
    assert "export_blocking" not in groups_by_binding[
        "identifier"
    ].model_dump()


def test_validation_attachment_edges_require_direct_extraction_source(monkeypatch):
    monkeypatch.setattr(
        validation_attachments_module,
        "validation_attachment_options_for_agent",
        lambda agent_id, agent_registry=None: (
            (_option("identifier"),)
            if agent_id == "fixture_extractor"
            else ()
        ),
    )

    flow = _flow_definition(
        "fixture_extractor",
        extra_nodes=[_validator_node("custom_validator", "custom_validator")],
        edges=[
            {
                "id": "e1",
                "source": "task_1",
                "target": "custom_validator",
                "role": "validation_attachment",
                "satisfies_binding_id": "identifier",
            }
        ],
    )

    with pytest.raises(FlowValidationAttachmentError, match="originate directly"):
        apply_flow_validation_attachment_defaults(flow)


def test_validation_attachment_edges_require_distinct_binding_ids(monkeypatch):
    monkeypatch.setattr(
        validation_attachments_module,
        "validation_attachment_options_for_agent",
        lambda agent_id, agent_registry=None: (
            (_option("identifier"),)
            if agent_id == "fixture_extractor"
            else ()
        ),
    )

    flow = _flow_definition(
        "fixture_extractor",
        extra_nodes=[
            _validator_node("custom_validator_1", "custom_validator_1"),
            _validator_node("custom_validator_2", "custom_validator_2"),
        ],
        edges=[
            {"id": "e1", "source": "task_1", "target": "extract_1"},
            {
                "id": "e2",
                "source": "extract_1",
                "target": "custom_validator_1",
                "role": "validation_attachment",
                "satisfies_binding_id": "identifier",
            },
            {
                "id": "e3",
                "source": "extract_1",
                "target": "custom_validator_2",
                "role": "validation_attachment",
                "satisfies_binding_id": "identifier",
            },
        ],
    )

    with pytest.raises(FlowValidationAttachmentError, match="distinct validator bindings"):
        apply_flow_validation_attachment_defaults(flow)


def test_validation_attachment_edge_rejects_disabled_replacement(monkeypatch):
    options = (
        _option(
            "identifier",
            attachment_id="fixture:binding:identifier",
            allow_opt_out=True,
        ),
    )
    monkeypatch.setattr(
        validation_attachments_module,
        "validation_attachment_options_for_agent",
        lambda agent_id, agent_registry=None: options if agent_id == "fixture_extractor" else (),
    )
    initial = apply_flow_validation_attachment_defaults(
        _flow_definition("fixture_extractor")
    )
    attachments = [
        attachment.model_dump()
        for attachment in initial.nodes[1].data.validation_attachments
    ]
    attachments[0]["enabled"] = False

    flow = _flow_definition(
        "fixture_extractor",
        attachments=attachments,
        extra_nodes=[_validator_node("custom_validator", "custom_validator")],
        edges=[
            {"id": "e1", "source": "task_1", "target": "extract_1"},
            {
                "id": "e2",
                "source": "extract_1",
                "target": "custom_validator",
                "role": "validation_attachment",
                "replaces_attachment_id": "fixture:binding:identifier",
            },
        ],
    )

    with pytest.raises(FlowValidationAttachmentError, match="disabled and replaced"):
        apply_flow_validation_attachment_defaults(flow)


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


def test_validation_schedule_splits_active_opt_out_and_inactive_metadata():
    schedule = validation_schedule_from_node_data(
        {
            "validation_attachments": [
                {
                    "attachment_id": "active",
                    "domain_pack_id": "fixture",
                    "validator_id": "shape",
                    "validator_binding_id": "shape",
                    "state": "active",
                    "scope": "pack",
                    "enabled": True,
                },
                {
                    "attachment_id": "opt-out",
                    "domain_pack_id": "fixture",
                    "validator_id": "lookup",
                    "validator_binding_id": "lookup",
                    "state": "active",
                    "scope": "field",
                    "enabled": False,
                    "required": True,
                },
                {
                    "attachment_id": "planned",
                    "domain_pack_id": "fixture",
                    "validator_id": "planned",
                    "state": "planned",
                    "scope": "pack",
                    "enabled": False,
                },
            ]
        }
    )

    assert [item["attachment_id"] for item in schedule["scheduled_validators"]] == [
        "active"
    ]
    assert [item["attachment_id"] for item in schedule["opt_outs"]] == ["opt-out"]
    assert [item["attachment_id"] for item in schedule["inactive_metadata"]] == [
        "planned"
    ]


def test_validation_schedule_rejects_unexpected_attachment_types():
    with pytest.raises(
        FlowValidationAttachmentError,
        match="Unexpected validation attachment type: object",
    ):
        validation_schedule_from_node_data({"validation_attachments": [object()]})
