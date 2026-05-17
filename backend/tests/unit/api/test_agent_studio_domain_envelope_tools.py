"""Unit coverage for Agent Studio domain-envelope Opus tools."""

from __future__ import annotations

import asyncio

from src.api import agent_studio as api_module
from src.lib.agent_studio.models import ChatContext


DOMAIN_TOOL_NAMES = {
    "list_domain_envelopes",
    "get_domain_envelope_state",
    "get_domain_pack_validation_plan",
    "get_domain_envelope_review_rows",
    "get_export_submission_readiness",
}


def test_get_all_opus_tools_includes_domain_envelope_inspection_tools():
    tools = api_module._get_all_opus_tools(ChatContext(active_tab="agents"))
    tools_by_name = {tool.get("name"): tool for tool in tools}

    assert DOMAIN_TOOL_NAMES.issubset(tools_by_name)
    assert tools_by_name["get_domain_envelope_state"]["input_schema"]["required"] == [
        "envelope_id"
    ]
    assert tools_by_name["get_export_submission_readiness"]["input_schema"]["required"] == [
        "session_id"
    ]
    validation_plan_description = tools_by_name["get_domain_pack_validation_plan"][
        "description"
    ]
    get_prompt_description = tools_by_name["get_prompt"]["description"]
    assert "active automatic validation defaults" in validation_plan_description
    assert "under-development validator metadata" in validation_plan_description
    assert "get_prompt(agent_id=...)" in validation_plan_description
    assert "validator_bindings[].validator_agent.agent_id" in validation_plan_description
    assert "Validator-agent IDs returned by get_domain_pack_validation_plan" in (
        get_prompt_description
    )
    assert "planned or blocked validators" not in validation_plan_description
    assert "opt-out reason" not in validation_plan_description.lower()


def test_handle_tool_call_dispatches_domain_envelope_state_with_user_scope(monkeypatch):
    captured = {}

    def fake_get_domain_envelope_state(**kwargs):
        captured.update(kwargs)
        return {
            "success": True,
            "semantic_source": "domain_envelope.objects",
            "envelope_id": kwargs["envelope_id"],
        }

    monkeypatch.setattr(
        api_module.agent_studio_domain_envelope_tools,
        "get_domain_envelope_state",
        fake_get_domain_envelope_state,
    )

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="get_domain_envelope_state",
            tool_input={
                "envelope_id": "env-1",
                "object_id": "obj-1",
                "field_path": "gene.symbol",
                "history_limit": 5,
            },
            context=ChatContext(active_tab="agents"),
            user_email="curator@example.org",
            user_auth_sub="auth-sub-1",
            messages=[],
        )
    )

    assert result["success"] is True
    assert captured["session_factory"] is api_module.SessionLocal
    assert captured["user_auth_sub"] == "auth-sub-1"
    assert captured["envelope_id"] == "env-1"
    assert captured["object_id"] == "obj-1"
    assert captured["field_path"] == "gene.symbol"
    assert captured["history_limit"] == 5


def test_handle_tool_call_dispatches_export_readiness_with_normalized_inputs(monkeypatch):
    captured = {}

    def fake_get_export_submission_readiness(**kwargs):
        captured.update(kwargs)
        return {
            "success": True,
            "session_id": kwargs["session_id"],
            "candidate_ids": kwargs["candidate_ids"],
            "expected_envelope_revisions": kwargs["expected_envelope_revisions"],
        }

    monkeypatch.setattr(
        api_module.agent_studio_domain_envelope_tools,
        "get_export_submission_readiness",
        fake_get_export_submission_readiness,
    )

    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="get_export_submission_readiness",
            tool_input={
                "session_id": "session-1",
                "candidate_ids": ["candidate-1", "  ", "candidate-2"],
                "expected_envelope_revisions": {"env-1": 3},
                "mode": "submission",
            },
            context=ChatContext(active_tab="agents"),
            user_email="curator@example.org",
            user_auth_sub="auth-sub-1",
            messages=[],
        )
    )

    assert result["success"] is True
    assert captured["session_factory"] is api_module.SessionLocal
    assert captured["user_auth_sub"] == "auth-sub-1"
    assert captured["session_id"] == "session-1"
    assert captured["candidate_ids"] == ["candidate-1", "candidate-2"]
    assert captured["expected_envelope_revisions"] == {"env-1": 3}
    assert captured["mode"] == "submission"


def test_handle_tool_call_rejects_invalid_export_readiness_revision_map():
    result = asyncio.run(
        api_module._handle_tool_call(
            tool_name="get_export_submission_readiness",
            tool_input={
                "session_id": "session-1",
                "expected_envelope_revisions": {"env-1": "3"},
            },
            context=ChatContext(active_tab="agents"),
            user_email="curator@example.org",
            user_auth_sub="auth-sub-1",
            messages=[],
        )
    )

    assert result["success"] is False
    assert "expected_envelope_revisions.env-1 must be an integer" in result["error"]


def test_domain_reference_summary_merges_stable_tool_refs_without_prompt_text():
    event = api_module._domain_references_from_tool_result(
        "get_domain_envelope_state",
        {
            "envelope": {"envelope_id": "env-1", "envelope_revision": 4},
            "objects": [{"object_id": "obj-1", "field_path": "gene.symbol"}],
            "validation_findings": [
                {
                    "finding_id": "finding-1",
                    "field_path": "gene.symbol",
                    "message": "Raw message should not become a reference.",
                }
            ],
        },
    )

    merged = api_module._merge_domain_reference_events([event])

    assert merged == {
        "tool_names": ["get_domain_envelope_state"],
        "references": {
            "envelope_id": ["env-1"],
            "envelope_revision": ["4"],
            "field_path": ["gene.symbol"],
            "finding_id": ["finding-1"],
            "object_id": ["obj-1"],
        },
    }
    assert "Raw message should not become a reference." not in str(merged)
