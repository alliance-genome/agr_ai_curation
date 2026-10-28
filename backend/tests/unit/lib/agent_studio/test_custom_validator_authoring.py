"""Regression for attachment-only custom validators, independent of paid execution."""

from copy import deepcopy

import pytest

from src.lib.agent_studio import flow_tools
from src.lib.agent_studio.authoring_validation import (
    AuthoringValidationContext,
    validate_flow_authoring_draft,
)
from src.lib.flows.validation_attachments import (
    apply_flow_validation_attachment_defaults,
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
def test_historical_override_is_rejected_in_every_mutation_phase(phase):
    candidate, entries = fixture()
    candidate["nodes"].append({
        "id": "old-validator", "type": "agent", "position": {"x": 2, "y": 2},
        "data": {"agent_id": AGENT, "agent_display_name": "Old validator",
                 "output_key": "validation", "agent_revision_id": PIN},
    })
    candidate["edges"].append({"id": "old-override", "source": "node_5",
        "target": "old-validator", "role": "validation_attachment",
        "replaces_attachment_id": ATTACHMENT})
    original = deepcopy(candidate)
    result = validate_flow_authoring_draft(candidate,
        context=AuthoringValidationContext.from_values(db_user_id=2, active_group_ids=[]),
        resolve_agent=lambda key, _: entries.get(key),
        apply_attachment_defaults=lambda value: value, phase=phase)
    assert not result.valid
    assert "unsupported_validator_override" in {f.code for f in result.findings}
    assert candidate == original


@pytest.mark.parametrize("overrides", [
    {}, {"satisfies_binding_id": "wrong"},
    {"replaces_attachment_id": None, "satisfies_binding_id": "supplemental"},
])
def test_override_proposals_are_rejected_without_mutating_draft(overrides):
    candidate, entries = fixture()
    original = deepcopy(candidate)
    with pytest.raises(flow_tools._FlowProposalCompileError) as error:
        compile_attachment(candidate, entries, **overrides)
    assert error.value.code == "unsupported_validator_override"
    assert candidate == original


def test_validator_remains_unavailable_as_ordinary_step():
    candidate, entries = fixture()
    with pytest.raises(flow_tools._FlowProposalCompileError) as raised:
        compile_attachment(candidate, entries, role="control_flow")
    assert raised.value.code == "attachment_only_agent_in_control_flow"


def test_authorized_catalog_excludes_attachment_only_validators(monkeypatch):
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
        assert result["success"] is False, result
        assert result["code"] == "unsupported_validator_override"
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
