"""Regression for attachment-only custom validators, independent of paid execution."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.lib.agent_studio import flow_tools
from src.lib.agent_studio.authoring_validation import (
    AuthoringValidationContext,
    validate_flow_authoring_draft,
)
from src.lib.flows.validation_attachments import (
    apply_flow_validation_attachment_defaults,
    validation_schedule_from_node_data,
)
from src.schemas.flows import FlowDefinition

AGENT = "ca_724922ab-a9c3-4bff-8e67-81914ab4b66d"
PIN = "7ddec4f5-0866-4853-ae37-595755db87cb"
BINDING = "allele_mention_reference_validation"
ATTACHMENT = "agr.alliance.allele:binding:allele_mention_reference_validation:field:AlleleMention:mention.text"


def fixture():
    entries = {
        "allele_extractor": {
            "category": "Extraction",
            "curation": {"domain_pack_id": "agr.alliance.allele"},
        },
        AGENT: {
            "name": "Allele Validation Agent (Custom)",
            "category": "Validation",
            "output_schema_key": "AlleleResultEnvelope",
            "supervisor": {"enabled": False},
            "agent_revision_id": "00000000-0000-0000-0000-000000000008",
        },
    }
    definition = FlowDefinition.model_validate(
        {
            "entry_node_id": "task",
            "nodes": [
                {
                    "id": "task",
                    "type": "task_input",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "agent_id": "task_input",
                        "agent_display_name": "Task",
                        "output_key": "task",
                        "task_instructions": "Extract",
                    },
                },
                {
                    "id": "node_5",
                    "type": "agent",
                    "position": {"x": 1, "y": 1},
                    "data": {
                        "agent_id": "allele_extractor",
                        "agent_display_name": "Extract",
                        "output_key": "extract",
                    },
                },
            ],
            "edges": [{"id": "start", "source": "task", "target": "node_5"}],
        }
    )
    return (
        apply_flow_validation_attachment_defaults(
            definition, agent_registry=entries
        ).model_dump(mode="json"),
        entries,
    )


def compile_attachment(candidate, entries, **overrides):
    operation = {
        "operation": "add_agent_step",
        "agent_id": AGENT,
        "agent_revision_id": PIN,
        "role": "validation_attachment",
        "source_node_id": "node_5",
        "satisfies_binding_id": BINDING,
        "replaces_attachment_id": ATTACHMENT,
        **overrides,
    }
    flow_tools._compile_flow_operations(
        candidate=candidate,
        metadata={},
        operations=[operation],
        accessible_agents=entries,
        semantic_refs={},
    )


@pytest.mark.parametrize("phase", ["proposal", "pre_apply", "post_apply", "save"])
def test_exact_replacement_operation_preserves_pin_and_required_schedule(phase):
    candidate, entries = fixture()
    original = deepcopy(candidate)
    compile_attachment(candidate, entries)
    added = candidate["nodes"][-1]
    assert added["data"]["agent_revision_id"] == PIN  # Not today's catalog head.
    assert candidate["nodes"][:-1] == original["nodes"]
    edge = candidate["edges"][-1]
    assert edge["role"] == "validation_attachment"
    assert edge["replaces_attachment_id"] == ATTACHMENT
    assert "satisfies_binding_id" not in edge
    result = validate_flow_authoring_draft(
        candidate,
        context=AuthoringValidationContext.from_values(
            db_user_id=2, active_group_ids=[]
        ),
        resolve_agent=lambda key, _: entries.get(key),
        apply_attachment_defaults=lambda value: apply_flow_validation_attachment_defaults(
            value, agent_registry=entries
        ),
        phase=phase,
    )
    assert result.valid, result.findings
    assert result.candidate is not None
    schedule = validation_schedule_from_node_data(
        result.candidate.nodes[1].data.model_dump()
    )
    assert len(schedule["replacement_validators"]) == 1
    replacement = schedule["replacement_validators"][0]
    assert replacement["validator_node_id"] == added["id"]
    assert replacement["required"] is True
    assert not any(
        item["validator_binding_id"] == BINDING
        for item in schedule["scheduled_validators"]
    )


def test_mismatched_dual_binding_is_rejected():
    candidate, entries = fixture()
    with pytest.raises(flow_tools._FlowProposalCompileError, match="do not match"):
        compile_attachment(candidate, entries, satisfies_binding_id="wrong")


def test_validator_remains_unavailable_as_ordinary_step():
    candidate, entries = fixture()
    with pytest.raises(flow_tools._FlowProposalCompileError) as raised:
        compile_attachment(candidate, entries, role="control_flow")
    assert raised.value.code == "attachment_only_agent_in_control_flow"


def test_incompatible_result_schema_and_binding_are_typed():
    candidate, entries = fixture()
    compile_attachment(
        candidate, entries, replaces_attachment_id=None, satisfies_binding_id="missing"
    )
    entries[AGENT]["output_schema_key"] = "NoSuchSchema"
    result = validate_flow_authoring_draft(
        candidate,
        context=AuthoringValidationContext.from_values(
            db_user_id=2, active_group_ids=[]
        ),
        resolve_agent=lambda key, _: entries.get(key),
        apply_attachment_defaults=lambda value: apply_flow_validation_attachment_defaults(
            value, agent_registry=entries
        ),
    )
    assert {
        "incompatible_validator_result_contract",
        "incompatible_validation_binding",
    } <= {f.code for f in result.findings}


def test_authorized_catalog_keeps_validators_only_for_attachment_authoring(monkeypatch):
    from src.lib.agent_studio import catalog_service

    monkeypatch.setattr(flow_tools, "get_current_user_id", lambda: 2)
    monkeypatch.setattr(flow_tools, "get_current_active_group_ids", lambda: [])
    monkeypatch.setattr(
        catalog_service,
        "list_available_agents",
        lambda **kw: [
            {
                "agent_id": AGENT,
                "category": "Validation",
                "supervisor": {"enabled": False},
            },
        ],
    )
    assert AGENT not in flow_tools._accessible_flow_agents()
    assert AGENT in flow_tools._accessible_flow_agents(include_attachment_only=True)


def test_supported_supplemental_binding_has_separate_schedule(monkeypatch):
    from dataclasses import replace
    from src.lib.flows import validation_attachments as attachments

    candidate, entries = fixture()
    registry = attachments.domain_pack_validation_registries()["agr.alliance.allele"]
    original_binding = next(
        item for item in registry.bindings if item.binding_id == BINDING
    )
    monkeypatch.setattr(
        attachments,
        "domain_pack_validation_registries",
        lambda: {
            "agr.alliance.allele": SimpleNamespace(
                bindings=(
                    *registry.bindings,
                    replace(original_binding, binding_id="supplemental"),
                )
            )
        },
    )
    compile_attachment(
        candidate,
        entries,
        replaces_attachment_id=None,
        satisfies_binding_id="supplemental",
    )
    result = validate_flow_authoring_draft(
        candidate,
        context=AuthoringValidationContext.from_values(
            db_user_id=2, active_group_ids=[]
        ),
        resolve_agent=lambda key, _: entries.get(key),
        apply_attachment_defaults=lambda value: apply_flow_validation_attachment_defaults(
            value, agent_registry=entries
        ),
    )
    assert result.valid, result.findings
    assert result.candidate is not None
    schedule = validation_schedule_from_node_data(
        result.candidate.nodes[1].data.model_dump()
    )
    assert len(schedule["supplemental_validators"]) == 1
    assert any(
        item["validator_binding_id"] == BINDING
        for item in schedule["scheduled_validators"]
    )


def test_proposal_path_does_not_apply_or_save_and_reports_compiler_contradiction(
    monkeypatch,
):
    candidate, entries = fixture()
    fingerprint = "sha256:" + "a" * 64
    context = {
        **candidate,
        "flow_name": "Synthetic regression",
        "flow_draft_fingerprint": fingerprint,
    }
    flow_tools.set_workflow_user_context(2)
    flow_tools.set_current_flow_context(context)
    monkeypatch.setattr(flow_tools, "_accessible_flow_agents", lambda **kw: entries)
    monkeypatch.setattr(
        flow_tools,
        "_validate_exact_flow_for_current_user",
        lambda value, **kw: validate_flow_authoring_draft(
            value,
            context=AuthoringValidationContext.from_values(
                db_user_id=2, active_group_ids=[]
            ),
            resolve_agent=lambda key, _: entries.get(key),
            apply_attachment_defaults=lambda value: apply_flow_validation_attachment_defaults(
                value, agent_registry=entries
            ),
        ),
    )
    operation = {
        "operation": "add_agent_step",
        "agent_id": AGENT,
        "agent_revision_id": PIN,
        "role": "validation_attachment",
        "source_node_id": "node_5",
        "satisfies_binding_id": BINDING,
        "replaces_attachment_id": ATTACHMENT,
    }
    original = deepcopy(context)
    try:
        handler = flow_tools._propose_flow_draft_update_handler()
        result = handler(
            base_draft_fingerprint=fingerprint,
            operations=[operation],
            change_summary="Attach validator",
        )
        assert result["success"] is True, result
        assert result["pending_user_approval"] is True
        assert context == original

        def fail(**kwargs):
            raise RuntimeError("private internal details")

        monkeypatch.setattr(flow_tools, "_compile_flow_operations", fail)
        error = handler(
            base_draft_fingerprint=fingerprint,
            operations=[operation],
            change_summary="Attach validator",
            reset_candidate=True,
        )
        assert error["code"] == "flow_authoring_compile_failed"
        assert error["failure_kind"] == "operational"
        assert "private internal details" not in str(error)
    finally:
        flow_tools.clear_workflow_user_context()
        flow_tools.set_current_flow_context(None)
