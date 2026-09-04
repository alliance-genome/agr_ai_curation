"""Contract tests for lossless Agent Studio authoring snapshots."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from src.lib.agent_studio.authoring_context import (
    flow_draft_fingerprint,
    workshop_draft_fingerprint,
)
from src.lib.agent_studio.models import (
    AgentWorkshopContext,
    ChatContext,
    ChatMessage,
    ChatRequest,
    FlowContextDefinition,
)


def _flow_context() -> ChatContext:
    return ChatContext(
        active_tab="flows",
        flow_id="flow-1",
        flow_name="Test Flow",
        flow_description="Exact draft",
        flow_updated_at="2026-09-04T00:00:00Z",
        flow_definition=FlowContextDefinition.model_validate(
            {
                "version": "1.1",
                "entry_node_id": "task",
                "nodes": [
                    {
                        "id": "task",
                        "node_type": "task_input",
                        "position": {"x": 250, "y": 100},
                        "agent_id": "task_input",
                        "agent_display_name": "Initial Instructions",
                        "task_instructions": "Extract genes",
                        "output_key": "task_input",
                        "validation_attachments": [],
                        "validation_groups": [],
                    }
                ],
                "edges": [],
            }
        ),
    )


def _adversarial_flow_context() -> ChatContext:
    return ChatContext(
        active_tab="flows",
        flow_id="flow-é-A",
        flow_name="Unicode β flow",
        flow_description="Case-sensitive IDs A/a",
        flow_updated_at="2026-09-04T01:02:03.456Z",
        flow_definition=FlowContextDefinition.model_validate(
            {
                "version": "1.1",
                "entry_node_id": "A",
                "nodes": [
                    {
                        "id": "a",
                        "node_type": "agent",
                        "position": {"x": 1e20, "y": -0.0},
                        "agent_id": "unicode_β",
                        "agent_display_name": "β extractor",
                        "output_key": "out-a",
                        "projection_plan": {
                            "é": 1.25,
                            "A": 1e-7,
                            "a": 1e20,
                        },
                    },
                    {
                        "id": "A",
                        "node_type": "task_input",
                        "position": {"x": 1e-7, "y": 1.25},
                        "agent_id": "task_input",
                        "agent_display_name": "Initial Instructions",
                        "task_instructions": "Exact 🧬 task",
                        "output_key": "task_input",
                    },
                ],
                "edges": [
                    {
                        "id": "é-edge",
                        "source": "A",
                        "target": "a",
                        "role": "control_flow",
                        "condition": {"type": "contains", "value": "β"},
                    }
                ],
            }
        ),
    )


def _adversarial_workshop_context() -> AgentWorkshopContext:
    return AgentWorkshopContext(
        getting_started_mode="clone",
        template_source="source-é",
        custom_agent_id="ca-Aa",
        custom_agent_updated_at="2026-09-04T01:02:03.456Z",
        draft_name="β agent",
        draft_description="Unicode 🧬 description",
        draft_icon="science",
        draft_visibility="project",
        draft_allowed_group_ids=["é", "a", "A"],
        prompt_draft="Exact main prompt",
        group_prompt_overrides={"é": "accent", "a": "lower", "A": "upper"},
        include_group_rules=True,
        draft_model_id="gpt-5.6-sol",
        draft_model_reasoning="high",
        draft_tool_ids=["é-tool", "a-tool", "A-tool"],
        draft_output_schema_key="gene",
    )


def test_flow_fingerprint_matches_frontend_canonical_fixture():
    assert flow_draft_fingerprint(_flow_context()) == (
        "sha256:d78fb31bafaeef99c20bbebb07af22debf58dbe0315a1caa327a03e89f7586d7"
    )


def test_adversarial_fingerprints_match_frontend_canonical_fixtures():
    negative_zero = _adversarial_flow_context()
    positive_zero = negative_zero.model_copy(deep=True)
    positive_zero.flow_definition.nodes[0].position["y"] = 0.0
    assert math.copysign(1.0, negative_zero.flow_definition.nodes[0].position["y"]) == -1.0
    assert flow_draft_fingerprint(negative_zero) == flow_draft_fingerprint(positive_zero)
    assert flow_draft_fingerprint(negative_zero) == (
        "sha256:f9f8664ca18901527a106d90c077ae0b52f2733a592531c7cd1110795a558b92"
    )
    assert workshop_draft_fingerprint(_adversarial_workshop_context()) == (
        "sha256:55e4d999b342e07877cb84ca6dafb6f05c852572ac4512d6a1d2491610afe235"
    )


def test_chat_request_accepts_exact_flow_and_workshop_snapshots():
    context = _flow_context()
    workshop = AgentWorkshopContext(
        getting_started_mode="scratch",
        draft_name="Draft agent",
        draft_description="Description",
        draft_icon="science",
        draft_visibility="private",
        draft_allowed_group_ids=["FB", "WB"],
        inherited_allowed_group_ids=[],
        prompt_draft="Use exact evidence.",
        group_prompt_overrides={"WB": "Use WormBase conventions."},
        include_group_rules=True,
        draft_tool_ids=["read_chunk", "search_document"],
        draft_output_schema_key="gene",
    )
    context.agent_workshop = workshop.model_copy(
        update={"draft_fingerprint": workshop_draft_fingerprint(workshop)}
    )
    context.flow_draft_fingerprint = flow_draft_fingerprint(context)

    request = ChatRequest(
        messages=[ChatMessage(role="user", content="Review this exact draft")],
        context=context,
    )

    assert request.context is not None
    assert request.context.flow_definition is not None
    assert request.context.flow_definition.nodes[0].position == {"x": 250.0, "y": 100.0}
    assert request.context.agent_workshop is not None
    assert request.context.agent_workshop.group_prompt_overrides == {
        "WB": "Use WormBase conventions."
    }


@pytest.mark.parametrize("artifact", ["flow", "workshop"])
def test_chat_request_rejects_missing_or_stale_authoring_fingerprint(artifact: str):
    context = _flow_context()
    if artifact == "flow":
        context.flow_draft_fingerprint = f"sha256:{'0' * 64}"
    else:
        context.flow_draft_fingerprint = flow_draft_fingerprint(context)
        context.agent_workshop = AgentWorkshopContext(
            draft_name="Changed now",
            draft_fingerprint=f"sha256:{'0' * 64}",
        )

    with pytest.raises(ValidationError, match="fingerprint"):
        ChatRequest(
            messages=[ChatMessage(role="user", content="Review")],
            context=context,
        )
