"""Complete Workshop proposals preserve local data and never persist."""

from unittest.mock import Mock
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.lib.agent_studio.authoring_context import workshop_draft_fingerprint
from src.lib.agent_studio.authoring_validation import (
    AgentModelValidationRecord, AgentToolValidationRecord, AgentValidationSources,
)
from src.lib.agent_studio.models import AgentWorkshopContext
from src.lib.agent_studio.custom_agent_service import custom_agent_name_exists
from src.lib.agent_studio.workshop_authoring import (
    apply_workshop_operations, propose_workshop_update, validate_workshop_context,
)


@pytest.fixture
def base():
    draft = AgentWorkshopContext(
        getting_started_mode="scratch", draft_name="Reader", draft_model_id="model-a",
        prompt_draft="Read the supplied document.", draft_icon="R", draft_visibility="private",
        draft_allowed_group_ids=[], inherited_allowed_group_ids=[], include_group_rules=True,
        group_prompt_overrides={}, draft_tool_ids=[],
    )
    draft.draft_fingerprint = workshop_draft_fingerprint(draft)
    return draft


@pytest.fixture
def db(monkeypatch):
    from src.lib.agent_studio import custom_agent_service
    from src.lib.agent_studio import capability_catalog
    sources = AgentValidationSources(
        models={"model-a": AgentModelValidationRecord("model-a", True, True, ("low", "high"))},
        tools={"read": AgentToolValidationRecord("read", True, True)},
        output_schema_keys=frozenset({"facts"}), group_ids=frozenset({"TEAM"}),
        builder_finalization_tool_ids=frozenset(),
    )
    monkeypatch.setattr(custom_agent_service, "_agent_validation_sources", lambda *a, **kw: sources)
    monkeypatch.setattr(custom_agent_service, "custom_agent_name_exists", lambda *a, **kw: False)
    monkeypatch.setattr(custom_agent_service, "_get_primary_project_id_for_user", lambda *a: "project")
    monkeypatch.setattr(capability_catalog, "build_authorized_capability_catalog", lambda **kw: [
        capability_catalog.CapabilityRecord(kind=kind, resource_id=key, name=key, description="")
        for kind, key in [("model", "model-a"), ("tool", "read"), ("output_contract", "facts"), ("group", "TEAM")]
    ])
    return Mock()


def propose(db, base, operations, state=None):
    return propose_workshop_update(
        db=db, base=base, user_id=1, active_group_ids=["TEAM"],
        state={} if state is None else state,
        tool_input={"base_draft_fingerprint": base.draft_fingerprint, "operations": operations},
    )


def test_complete_proposal_preserves_unrelated_fields_and_never_writes(db, base):
    original = base.model_dump()
    result = propose(db, base, [
        {"operation": "set_name", "text": "Evidence reader"},
        {"operation": "add_tool", "resource_id": "read"},
    ])
    assert result["valid"] and result["pending_user_approval"]
    assert result["saved"] is False
    candidate = AgentWorkshopContext.model_validate(result["candidate"])
    assert candidate.prompt_draft == base.prompt_draft
    assert candidate.draft_name == "Evidence reader"
    assert candidate.draft_tool_ids == ["read"]
    assert workshop_draft_fingerprint(candidate) == result["candidate_draft_fingerprint"]
    assert {entry["path"] for entry in result["diff"]} == {"custom_agent.name", "custom_agent.tool_ids"}
    assert base.model_dump() == original
    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.flush.assert_not_called()


def test_new_blank_draft_can_be_built(db, base):
    base.draft_name = ""
    base.prompt_draft = ""
    base.draft_fingerprint = workshop_draft_fingerprint(base)
    result = propose(db, base, [
        {"operation": "set_name", "text": "Reader"},
        {"operation": "set_instructions", "text": "Read supplied evidence."},
    ])
    assert result["valid"]


def test_reviewed_candidate_uses_save_normalization(db, base):
    result = propose(db, base, [
        {"operation": "set_name", "text": "  Reader  "},
        {"operation": "set_instructions", "text": "  Read evidence.\n\n\n\nThen explain.  "},
        {"operation": "set_group_instructions", "resource_id": "TEAM", "text": "   "},
        {"operation": "set_icon", "text": ""},
    ])
    assert result["valid"]
    candidate = AgentWorkshopContext.model_validate(result["candidate"])
    assert candidate.draft_name == "Reader"
    assert candidate.draft_icon == "🔧"
    assert candidate.group_prompt_overrides == {}
    from src.lib.agent_studio.custom_agent_service import _normalize_editable_custom_prompt
    assert candidate.prompt_draft == _normalize_editable_custom_prompt(None, candidate.prompt_draft, target="test")
    assert validate_workshop_context(db, workshop=candidate, user_id=1, active_group_ids=["TEAM"]).valid


def test_clone_source_revision_is_reauthorized(db, base, monkeypatch):
    from src.lib.agent_studio import custom_agent_service as service
    source = SimpleNamespace(
        updated_at=datetime(2026, 9, 4, tzinfo=timezone.utc), allowed_group_ids=[], tool_ids=[],
    )
    monkeypatch.setattr(service, "get_custom_agent_visible_to_user", lambda *a: source)
    base.clone_source_agent_id = "ca_11111111-1111-1111-1111-111111111111"
    base.clone_source_updated_at = source.updated_at.isoformat()
    assert validate_workshop_context(db, workshop=base, user_id=1, active_group_ids=[]).valid
    source.updated_at = datetime(2026, 9, 5, tzinfo=timezone.utc)
    result = validate_workshop_context(db, workshop=base, user_id=1, active_group_ids=[])
    assert not result.valid
    assert "unavailable_workshop_source" in {item.code for item in result.findings}


def test_project_visibility_requires_current_membership(db, base, monkeypatch):
    from src.lib.agent_studio import custom_agent_service as service
    monkeypatch.setattr(service, "_get_primary_project_id_for_user", Mock(side_effect=ValueError("missing")))
    result = propose(db, base, [{"operation": "set_visibility", "text": "project"}])
    assert not result["valid"]
    assert "project_visibility_unavailable" in {item["code"] for item in result["findings"]}


def test_invalid_candidate_is_retained_for_repair_without_mutating_base(db, base):
    state = {}
    first = propose(db, base, [{"operation": "select_model", "resource_id": "missing"}], state)
    assert not first["valid"]
    assert first["findings"][0]["code"] == "unavailable_model"
    second = propose(db, base, [{"operation": "select_model", "resource_id": "model-a", "reasoning": "high"}], state)
    assert second["valid"]
    assert base.draft_model_reasoning is None


@pytest.mark.parametrize("operation", [
    {"operation": "replace_agent", "text": "{}"},
    {"operation": "set_profile_fields", "text": "[]"},
    {"operation": "set_allowed_groups", "resource_ids": ["TEAM"], "inherited_allowed_group_ids": []},
    {"operation": "set_name"},
])
def test_unsupported_or_malformed_operations_are_non_mutating(db, base, operation):
    original = base.model_dump()
    assert not propose(db, base, [operation])["success"]
    assert base.model_dump() == original


@pytest.mark.parametrize("operation,code", [
    ({"operation": "set_instructions", "text": "Platform Runtime Contract"}, "locked_prompt_layer"),
    ({"operation": "set_group_instructions", "resource_id": "TEAM", "text": "Generated runtime contract"}, "locked_prompt_layer"),
    ({"operation": "add_tool", "resource_id": "hidden"}, "unavailable_tool"),
    ({"operation": "set_allowed_groups", "resource_ids": ["UNKNOWN"]}, "unavailable_group"),
    ({"operation": "select_output", "resource_id": "facts"}, "missing_output_finalizer"),
    ({"operation": "select_model", "resource_id": "model-a", "reasoning": "invalid"}, "unsupported_reasoning_effort"),
])
def test_canonical_findings_block_approval(db, base, operation, code):
    result = propose(db, base, [operation])
    assert not result["pending_user_approval"]
    assert code in {finding["code"] for finding in result["findings"]}


def test_clearing_output_means_none(db, base):
    base.draft_output_schema_key = "facts"
    base.draft_fingerprint = workshop_draft_fingerprint(base)
    result = propose(db, base, [{"operation": "clear_output"}])
    assert result["valid"]
    assert not result["candidate"].get("draft_output_schema_key")


def test_operation_and_prompt_limits_are_environment_backed(db, base, monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_WORKSHOP_PROPOSAL_MAX_OPERATIONS", "1")
    assert not propose(db, base, [{"operation": "clear_output"}] * 2)["success"]
    monkeypatch.setenv("AGENT_STUDIO_WORKSHOP_PROMPT_MAX_CHARS", "12")
    result = propose(db, base, [{"operation": "set_instructions", "text": "x" * 13}])
    assert not result["valid"]
    assert "prompt_size_limit" in {finding["code"] for finding in result["findings"]}


def test_inherited_floor_change_invalidates_fingerprint(db, base):
    base.inherited_allowed_group_ids = ["TEAM"]
    result = propose(db, base, [{"operation": "set_name", "text": "Renamed"}])
    assert result["code"] == "stale_draft_fingerprint"


def test_all_editor_operations_preserve_identity(base):
    result = apply_workshop_operations(base, [
        {"operation": "set_description", "text": "Description"},
        {"operation": "set_icon", "text": "X"},
        {"operation": "set_visibility", "text": "project"},
        {"operation": "set_include_group_rules", "enabled": False},
        {"operation": "set_group_instructions", "resource_id": "TEAM", "text": "Rules"},
        {"operation": "reset_group_instructions", "resource_id": "TEAM"},
        {"operation": "add_tool", "resource_id": "read"},
        {"operation": "remove_tool", "resource_id": "read"},
    ])
    assert result.custom_agent_id == base.custom_agent_id
    assert result.inherited_allowed_group_ids == base.inherited_allowed_group_ids
    assert result.draft_description == "Description"
    assert result.draft_icon == "X"
    assert result.draft_visibility == "project"
    assert result.include_group_rules is False
    assert result.group_prompt_overrides == {}
    assert result.draft_tool_ids == []


def test_validation_phases_use_same_findings(db, base):
    base.draft_model_id = "missing"
    outcomes = [validate_workshop_context(
        db, workshop=base, user_id=1, active_group_ids=["TEAM"], phase=phase,
    ).findings for phase in ["proposal", "pre_apply", "post_apply", "save"]]
    assert all(findings == outcomes[0] for findings in outcomes)


def test_newer_saved_agent_rejects_proposal_and_apply(db, base, monkeypatch):
    from src.lib.agent_studio import custom_agent_service
    base.custom_agent_id = "ca_00000000-0000-0000-0000-000000000001"
    base.custom_agent_updated_at = "2026-09-04T10:00:00Z"
    source = SimpleNamespace(
        id="source", updated_at=datetime(2026, 9, 4, 11, tzinfo=timezone.utc),
        allowed_group_ids=[], inherited_allowed_group_ids=[], tool_ids=[],
    )
    monkeypatch.setattr(custom_agent_service, "get_custom_agent_for_user", lambda *args: source)
    for phase in ["proposal", "pre_apply", "post_apply"]:
        result = validate_workshop_context(
            db, workshop=base, user_id=1, active_group_ids=["TEAM"], phase=phase,
        )
        assert not result.valid
        assert "stale_saved_agent" in {finding.code for finding in result.findings}
    db.commit.assert_not_called()


def test_save_rechecks_timestamp_under_row_lock_before_mutation():
    from src.lib.agent_studio.custom_agent_service import update_custom_agent
    db = Mock()
    source = SimpleNamespace(
        updated_at=datetime(2026, 9, 4, 11, tzinfo=timezone.utc), name="Original",
    )
    with pytest.raises(ValueError, match="changed since it was opened"):
        update_custom_agent(
            db, source, name="Replacement",
            expected_updated_at=datetime(2026, 9, 4, 10, tzinfo=timezone.utc),
        )
    db.refresh.assert_called_once_with(source, with_for_update=True)
    assert source.name == "Original"
    db.add.assert_not_called()
    db.flush.assert_not_called()


def test_engine_failure_reporting_is_sanitized(db, base, monkeypatch):
    from src.lib.agent_studio import capability_catalog
    from src.lib.agent_studio.authoring_validation import AuthoringValidationEngineError
    captured = []
    def fail(**kwargs):
        raise RuntimeError("PRIVATE prompt and tool configuration")
    monkeypatch.setattr(capability_catalog, "build_authorized_capability_catalog", fail)
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    with pytest.raises(AuthoringValidationEngineError):
        validate_workshop_context(db, workshop=base, user_id=1, active_group_ids=[], phase="pre_apply")
    assert len(captured) == 1
    assert "PRIVATE" not in str(captured)
    assert "pre_apply" in str(captured)


def test_validation_endpoint_checks_fingerprint_and_performs_no_writes(db, base, monkeypatch):
    import asyncio
    from src.api.agent_studio_custom import WorkshopDraftValidationRequest, validate_workshop_draft_endpoint
    db.query.return_value.filter.return_value.one_or_none.return_value = SimpleNamespace(id=1)
    response = asyncio.run(validate_workshop_draft_endpoint(
        WorkshopDraftValidationRequest(workshop=base, phase="pre_apply"),
        user={"sub": "curator"}, db=db,
    ))
    assert response["valid"]
    db.commit.assert_not_called()
    db.add.assert_not_called()
    base.draft_name = "Changed after fingerprint"
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        asyncio.run(validate_workshop_draft_endpoint(
            WorkshopDraftValidationRequest(workshop=base, phase="pre_apply"),
            user={"sub": "curator"}, db=db,
        ))
    assert error.value.status_code == 422


@pytest.mark.parametrize("phase", ["proposal", "pre_apply", "post_apply", "save"])
def test_revoked_capability_has_identical_findings_at_every_boundary(db, base, monkeypatch, phase):
    from src.lib.agent_studio import custom_agent_service as service, capability_catalog
    from src.lib.agent_studio.authoring_validation import AuthoringValidationError
    from src.lib.agent_studio.workshop_authoring import workshop_save_candidate
    # Registered and attachable, but no longer present in this curator's live catalog.
    base.draft_tool_ids = ["read"]
    monkeypatch.setattr(capability_catalog, "build_authorized_capability_catalog", lambda **kw: [
        capability_catalog.CapabilityRecord(kind="model", resource_id="model-a", name="Model", description=""),
    ])
    if phase == "save":
        with pytest.raises(AuthoringValidationError) as error:
            service._require_valid_custom_agent_draft(
                db, user_id=1, active_group_ids=[], candidate=workshop_save_candidate(base),
            )
        findings = error.value.result.findings
    else:
        findings = validate_workshop_context(
            db, workshop=base, user_id=1, active_group_ids=[], phase=phase,
        ).findings
    assert {item.code for item in findings} == {"unavailable_tool"}
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("available", [True, False])
def test_saved_reference_requires_fresh_authorized_flow_catalog(db, monkeypatch, available):
    import asyncio
    from uuid import UUID
    from fastapi import HTTPException
    from src.api.agent_studio_custom import get_workshop_saved_reference
    from src.lib.agent_studio import capability_catalog
    agent_uuid = UUID("11111111-1111-1111-1111-111111111111")
    agent_id = f"ca_{agent_uuid}"
    db.query.return_value.filter.return_value.one_or_none.return_value = SimpleNamespace(id=1)
    catalog = Mock(return_value=[
        capability_catalog.CapabilityRecord(
            kind="agent", resource_id=agent_id, name="Saved", description="",
            compatibility={"flow_selectable": True},
        ),
    ] if available else [])
    monkeypatch.setattr(capability_catalog, "build_authorized_capability_catalog", catalog)
    if available:
        result = asyncio.run(get_workshop_saved_reference(agent_uuid, user={"sub": "owner"}, db=db))
        assert result.agent_id == agent_id
    else:
        with pytest.raises(HTTPException) as error:
            asyncio.run(get_workshop_saved_reference(agent_uuid, user={"sub": "owner"}, db=db))
        assert error.value.status_code == 409
    assert catalog.call_count == 1
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("updating", [False, True])
def test_direct_save_api_rejects_tool_outside_authenticated_catalog(db, monkeypatch, updating):
    import asyncio
    from uuid import uuid4
    from fastapi import HTTPException
    from src.api import agent_studio_custom as api
    from src.lib.agent_studio import custom_agent_service as service, capability_catalog
    from src.models.sql.agent import Agent
    monkeypatch.setattr(api, "set_global_user_from_cognito", lambda *a: SimpleNamespace(id=1))
    monkeypatch.setattr(service, "get_model", lambda *a: SimpleNamespace(curator_visible=True))
    monkeypatch.setattr(service, "_tool_policy_by_key", lambda *a: {
        "read": SimpleNamespace(allow_attach=True, curator_visible=True, config={"allowed_group_ids": ["ZFIN"]}),
    })
    monkeypatch.setattr(service, "has_tool_binding", lambda *a: True)
    monkeypatch.setattr(service, "_system_managed_tool_ids", lambda *a: [])
    monkeypatch.setattr(capability_catalog, "build_authorized_capability_catalog", lambda **kw: [
        capability_catalog.CapabilityRecord(kind="model", resource_id="model-a", name="Model", description=""),
    ])
    agent = Agent(
        id=uuid4(), user_id=1, name="Reader", instructions="Read evidence.", tool_ids=[],
        group_prompt_overrides={}, allowed_group_ids=[], inherited_allowed_group_ids=[],
        model_id="model-a", model_temperature=0.1, visibility="private", icon="R", version=1,
    )
    monkeypatch.setattr(api, "get_custom_agent_for_user", lambda *a: agent)
    with pytest.raises(HTTPException) as error:
        if updating:
            asyncio.run(api.update_custom_agent_endpoint(
                agent.id, api.UpdateCustomAgentRequest(tool_ids=["read"]), user={"sub": "owner"}, db=db,
            ))
        else:
            asyncio.run(api.create_custom_agent_endpoint(
                api.CreateCustomAgentRequest(name="Reader", custom_prompt="Read evidence.", model_id="model-a", tool_ids=["read"]),
                user={"sub": "owner"}, db=db,
            ))
    assert error.value.status_code == 400
    assert "unavailable_tool" in str(error.value.detail)
    db.commit.assert_not_called()
    db.add.assert_not_called()
    db.rollback.assert_called_once()


def test_case_variant_name_conflicts_in_proposal_and_save(db, base, monkeypatch):
    from src.lib.agent_studio import custom_agent_service as service
    from src.api import agent_studio_custom as api
    from fastapi import HTTPException
    import asyncio
    monkeypatch.setattr(service, "custom_agent_name_exists", custom_agent_name_exists)
    monkeypatch.setattr(service, "get_model", lambda *a: SimpleNamespace(curator_visible=True))
    monkeypatch.setattr(api, "set_global_user_from_cognito", lambda *a: SimpleNamespace(id=1))
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(name="READER")
    result = propose(db, base, [{"operation": "set_name", "text": "reader"}])
    assert not result["valid"]
    assert "duplicate_agent_name" in {item["code"] for item in result["findings"]}
    predicate = db.query.return_value.filter.call_args.args[1]
    assert "lower(agents.name)" in str(predicate)
    assert predicate.right.value == "reader"
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.create_custom_agent_endpoint(
            api.CreateCustomAgentRequest(name="reader", custom_prompt="Read evidence.", model_id="model-a"),
            user={"sub": "owner"}, db=db,
        ))
    assert error.value.status_code == 409
    db.add.assert_not_called()
    db.commit.assert_not_called()
