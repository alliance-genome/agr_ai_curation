"""Canonical exact-draft validation contracts for Agent Studio authoring."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.lib.agent_studio.authoring_validation import (
    AgentModelValidationRecord,
    AgentToolValidationRecord,
    AgentValidationSources,
    AuthoringValidationContext,
    AuthoringValidationFinding,
    CustomAgentDraft,
    ValidationPhase,
    report_authoring_validation_engine_failure,
    validate_custom_agent_authoring_draft,
    validate_flow_authoring_draft,
)


def _flow() -> dict:
    return {
        "version": "1.1",
        "nodes": [
            {
                "id": "task",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Initial Instructions",
                    "task_instructions": "Extract the requested facts.",
                    "output_key": "task_input",
                },
            },
            {
                "id": "extract",
                "type": "agent",
                "position": {"x": 100, "y": 100},
                "data": {
                    "agent_id": "extractor",
                    "agent_display_name": "Extractor",
                    "step_goal": "Extract facts",
                    "custom_instructions": "Keep evidence spans.",
                    "prompt_version": 2,
                    "output_key": "facts",
                    "validation_attachments": [],
                    "validation_groups": [],
                },
            },
            {
                "id": "output",
                "type": "output",
                "position": {"x": 300, "y": 100},
                "data": {
                    "agent_id": "csv_formatter",
                    "agent_display_name": "CSV",
                    "include_evidence": True,
                    "output_filename_template": "{{input_filename_stem}}.csv",
                    "projection_plan": {"row_source": "facts"},
                    "output_key": "csv",
                },
            },
        ],
        "edges": [
            {
                "id": "control",
                "source": "task",
                "target": "extract",
                "role": "control_flow",
            },
            {
                "id": "formatted",
                "source": "extract",
                "target": "output",
                "role": "output_attachment",
            },
        ],
        "entry_node_id": "task",
    }


def _flow_resolver(agent_id, _context):
    return {
        "extractor": {
            "category": "Extraction",
            "is_active": True,
            "supervisor": {"enabled": True},
            "produces_flow_artifacts": True,
        },
        "csv_formatter": {
            "category": "Output",
            "subcategory": "Formatter",
            "is_active": True,
            "supervisor": {"enabled": True},
        },
    }.get(agent_id)


def _flow_result(
    candidate,
    *,
    phase: ValidationPhase = "proposal",
    resolver=_flow_resolver,
    hydrate=lambda value: value,
):
    return validate_flow_authoring_draft(
        candidate,
        context=AuthoringValidationContext.from_values(
            db_user_id=7, active_group_ids=["TEAM_C"]
        ),
        resolve_agent=resolver,
        apply_attachment_defaults=hydrate,
        phase=phase,
    )


def test_exact_flow_is_equivalent_across_every_authoring_phase():
    results = [
        _flow_result(_flow(), phase=phase)
        for phase in ("proposal", "pre_apply", "post_apply", "save")
    ]

    assert all(result.valid for result in results)
    assert [result.findings for result in results] == [(), (), (), ()]
    assert all(
        result.candidate is not None
        and result.candidate.model_dump()["nodes"][1]["data"]["prompt_version"] == 2
        for result in results
    )
    assert all(
        result.candidate is not None
        and result.candidate.model_dump()["nodes"][2]["data"]["projection_plan"]
        == {"row_source": "facts"}
        for result in results
    )


def test_flow_findings_are_precise_and_do_not_leak_unavailable_identity():
    candidate = _flow()
    candidate["nodes"][1]["data"]["agent_id"] = "secret_other_users_agent"
    result = _flow_result(candidate, resolver=lambda *_args: None)

    finding = next(item for item in result.findings if item.code == "unavailable_agent")
    assert finding.path == "flow_definition.nodes.extract.data.agent_id"
    assert finding.node_id == "extract"
    assert "secret_other_users_agent" not in finding.message
    assert "secret_other_users_agent" not in str(finding.fix_hint)


def test_flow_topology_findings_preserve_node_and_edge_identity():
    candidate = _flow()
    candidate["edges"].append(
        {
            "id": "cycle",
            "source": "extract",
            "target": "task",
            "role": "control_flow",
        }
    )

    result = _flow_result(candidate)

    assert not result.valid
    assert any(
        finding.code in {"cycle", "branch", "join"}
        and (finding.node_id or finding.edge_id)
        and finding.path.startswith("flow_definition.")
        for finding in result.findings
    )


def test_flow_validation_never_mutates_the_supplied_candidate():
    candidate = _flow()
    original = _flow()

    def _hydrate(copy):
        copy.nodes[1].data.step_goal = "hydrated copy"
        return copy

    result = _flow_result(candidate, hydrate=_hydrate)

    assert result.valid
    assert candidate == original
    assert result.candidate is not None
    assert result.candidate.nodes[1].data.step_goal == "hydrated copy"


def test_pre_apply_rejects_a_stale_exact_draft_fingerprint():
    context = AuthoringValidationContext.from_values(
        db_user_id=7,
        active_group_ids=["TEAM_C"],
        expected_draft_fingerprint="sha256:proposal",
        current_draft_fingerprint="sha256:current",
    )

    result = validate_flow_authoring_draft(
        _flow(),
        context=context,
        resolve_agent=_flow_resolver,
        apply_attachment_defaults=lambda value: value,
        phase="pre_apply",
    )

    finding = next(
        item for item in result.errors if item.code == "stale_draft_fingerprint"
    )
    assert finding.path == "flow_definition.draft_fingerprint"


def _agent_sources() -> AgentValidationSources:
    return AgentValidationSources(
        models={
            "gpt-test": AgentModelValidationRecord(
                model_id="gpt-test",
                curator_visible=True,
                supports_reasoning=True,
                reasoning_options=("low", "high"),
            )
        },
        tools={
            "search": AgentToolValidationRecord(
                tool_id="search", attachable=True, installed=True
            ),
            "finalize_demo": AgentToolValidationRecord(
                tool_id="finalize_demo", attachable=True, installed=True
            ),
        },
        output_schema_keys=frozenset({"DemoEnvelope"}),
        group_ids=frozenset({"TEAM_C", "TEAM_D"}),
        builder_finalization_tool_ids=frozenset({"finalize_demo"}),
    )


def _agent(**overrides):
    candidate = {
        "name": "Draft agent",
        "description": "A complete general draft",
        "custom_prompt": "Inspect the input.",
        "group_prompt_overrides": {"TEAM_C": "Use TEAM_C conventions."},
        "icon": "🔧",
        "visibility": "private",
        "allowed_group_ids": ["TEAM_C"],
        "inherited_allowed_group_ids": ["TEAM_C", "TEAM_D"],
        "include_group_rules": True,
        "model_id": "gpt-test",
        "model_reasoning": "high",
        "model_temperature": 0.1,
        "tool_ids": ["search"],
        "output_schema_key": None,
        "category": "Custom",
    }
    candidate.update(overrides)
    return candidate


def _agent_result(
    candidate,
    *,
    phase: ValidationPhase = "proposal",
    extensions=(),
):
    return validate_custom_agent_authoring_draft(
        candidate,
        context=AuthoringValidationContext.from_values(
            db_user_id=7, active_group_ids=["TEAM_C"]
        ),
        sources=_agent_sources(),
        phase=phase,
        extension_validators=extensions,
    )


def test_no_output_contract_is_valid_without_finalizer_and_not_generic():
    result = _agent_result(_agent(output_schema_key="", tool_ids=["search"]))

    assert result.valid
    assert isinstance(result.candidate, CustomAgentDraft)
    assert result.candidate.output_schema_key is None


def test_model_response_schema_requires_available_contract_and_excludes_builder_finalizer():
    valid = _agent_result(_agent(output_schema_key="DemoEnvelope"))
    conflicting = _agent_result(
        _agent(
            output_schema_key="DemoEnvelope",
            tool_ids=["search", "finalize_demo"],
        )
    )
    unavailable = _agent_result(
        _agent(output_schema_key="RetiredEnvelope", tool_ids=["finalize_demo"])
    )

    assert [finding.code for finding in conflicting.errors] == ["output_schema_with_finalize_tool"]
    assert valid.valid
    assert "unavailable_output_contract" in {
        finding.code for finding in unavailable.errors
    }


@pytest.mark.parametrize(
    ("overrides", "code", "path"),
    [
        ({"model_id": "retired"}, "unavailable_model", "custom_agent.model_id"),
        ({"model_reasoning": "max"}, "unsupported_reasoning_effort", "custom_agent.model_reasoning"),
        ({"tool_ids": ["retired"]}, "unavailable_tool", "custom_agent.tool_ids.0"),
        ({"allowed_group_ids": ["TEAM_B"]}, "unavailable_group", "custom_agent.allowed_group_ids"),
        ({"allowed_group_ids": []}, "widened_inherited_access", "custom_agent.allowed_group_ids"),
        (
            {"group_prompt_overrides": {"TEAM_B": "Use local rules."}},
            "unavailable_group",
            "custom_agent.group_prompt_overrides.TEAM_B",
        ),
        (
            {"custom_prompt": "Copy the Platform Runtime Contract here."},
            "locked_prompt_layer",
            "custom_agent.custom_prompt",
        ),
    ],
)
def test_agent_live_reference_and_policy_findings(overrides, code, path):
    result = _agent_result(_agent(**overrides))

    finding = next(item for item in result.errors if item.code == code)
    assert finding.path == path


@dataclass(frozen=True)
class _Extension:
    validator_id: str = "future-profile-adapter"

    def validate(self, _candidate, _context):
        return [
            AuthoringValidationFinding(
                code="future_profile_warning",
                severity="warning",
                path="custom_agent",
                message="Future profile adapter warning.",
            )
        ]


def test_typed_extension_boundary_is_nonblocking_for_warnings():
    result = _agent_result(_agent(), extensions=[_Extension()])

    assert result.valid
    assert result.findings[-1].code == "future_profile_warning"


def test_agent_findings_are_equivalent_across_authoring_phases():
    results = [
        _agent_result(_agent(model_id="retired"), phase=phase)
        for phase in ("proposal", "pre_apply", "post_apply", "save")
    ]

    assert [tuple(item.code for item in result.findings) for result in results] == [
        ("unavailable_model",),
        ("unavailable_model",),
        ("unavailable_model",),
        ("unavailable_model",),
    ]


def test_expected_product_findings_do_not_emit_observability_events(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    result = _flow_result(_flow(), resolver=lambda *_args: None)

    assert not result.valid
    assert captured == []


def test_unexpected_engine_reporting_contains_only_sanitized_metadata(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda *args, **kwargs: captured.append((args, kwargs)) or True,
    )

    error = report_authoring_validation_engine_failure(
        artifact_kind="flow",
        phase="pre_apply",
    )

    assert "prompt" not in str(error).lower()
    assert len(captured) == 1
    _, kwargs = captured[0]
    assert kwargs["tags"] == {
        "validator_kind": "flow",
        "validation_code": "engine_failure",
        "validation_path": "flow",
        "validation_phase": "pre_apply",
    }
    assert kwargs["context"] == {"finding_count": 0}
