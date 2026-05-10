"""Tests for metadata-backed flow validation attachments."""

from __future__ import annotations

import pytest

from src.lib.flows.validation_attachments import (
    FlowValidationAttachmentError,
    apply_flow_validation_attachment_defaults,
    validation_attachment_catalog_by_agent,
    validation_schedule_from_node_data,
)
from src.schemas.flows import FlowDefinition


def _flow_definition(agent_id: str = "chemical_extractor", attachments=None) -> FlowDefinition:
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
        ],
        edges=[{"id": "e1", "source": "task_1", "target": "extract_1"}],
        entry_node_id="task_1",
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


def test_apply_defaults_selects_required_active_and_keeps_planned_blocked_visible():
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


def test_apply_defaults_preserves_allowed_opt_out_reason():
    agent_registry = {
        "fixture_extractor": {
            "curation": {"domain_pack_id": "agr" + ".alliance.chemical_condition"}
        }
    }
    initial = apply_flow_validation_attachment_defaults(
        _flow_definition("fixture_extractor"),
        agent_registry=agent_registry,
    )
    required_attachment = next(
        attachment
        for attachment in initial.nodes[1].data.validation_attachments
        if attachment.state == "active" and attachment.required and attachment.allow_opt_out
    )

    required_attachment.enabled = False
    required_attachment.opt_out_reason = "Curator confirmed this paper needs manual lookup."

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
        if attachment.attachment_id == required_attachment.attachment_id
    )

    assert updated_attachment.enabled is False
    assert updated_attachment.opt_out_reason == (
        "Curator confirmed this paper needs manual lookup."
    )


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
                    "opt_out_reason": "Manual review",
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
