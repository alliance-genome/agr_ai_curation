"""Saved execution configuration preserves explicit state and inherited access."""

from copy import deepcopy
import hashlib
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.schemas.agent_execution_revision import (
    AgentExecutionSnapshot,
    AgentOutputContract,
    initial_output_contract,
)


@pytest.fixture(autouse=True)
def configured_test_groups(monkeypatch):
    from src.lib.config import groups_loader

    configured_groups = groups_loader.get_valid_group_ids()
    monkeypatch.setattr(
        groups_loader, "get_valid_group_ids",
        lambda: [*configured_groups, "TEAM_A", "TEAM_B"],
    )


def profile_ref():
    return {
        "profile_id": str(uuid4()),
        "profile_revision_id": str(uuid4()),
        "revision": 1,
        "fingerprint": "sha256:" + "a" * 64,
    }


@pytest.mark.parametrize(
    "raw",
    [
        {"output_state": "none"},
        {
            "output_state": "structured_extraction",
            "output_mode": "domain",
            "output_schema_key": "Example",
        },
        {
            "output_state": "structured_extraction",
            "output_mode": "profile_bound_generic",
            "generic_profile_ref": profile_ref(),
        },
        {"output_state": "structured_extraction", "output_mode": "unprofiled_generic"},
    ],
)
def test_explicit_output_states_round_trip(raw):
    result = AgentOutputContract.model_validate(raw)
    assert AgentOutputContract.model_validate(result.model_dump(mode="json")) == result


@pytest.mark.parametrize(
    "raw",
    [
        {"output_state": "none", "output_schema_key": "Example"},
        {"output_state": "none", "output_mode": "unprofiled_generic"},
        {"output_state": "structured_extraction"},
        {"output_state": "structured_extraction", "output_mode": "domain"},
        {
            "output_state": "structured_extraction",
            "output_mode": "profile_bound_generic",
        },
        {
            "output_state": "structured_extraction",
            "output_mode": "unprofiled_generic",
            "generic_profile_ref": profile_ref(),
        },
        {
            "output_state": "structured_extraction",
            "output_mode": "domain",
            "output_schema_key": "Example",
            "generic_profile_ref": profile_ref(),
        },
    ],
)
def test_output_contract_rejects_contradictory_or_implicit_modes(raw):
    with pytest.raises(ValidationError):
        AgentOutputContract.model_validate(raw)


def test_current_head_baseline_never_infers_open_generic_from_null_schema():
    assert initial_output_contract(None).output_state == "none"
    assert initial_output_contract("").output_state == "none"
    assert initial_output_contract("Example").output_mode == "domain"


def snapshot():
    from src.lib.prompts.assembly import _bundle, _make_layer

    instructions = "Curator instructions"
    bundle = _bundle(
        "example",
        [
            _make_layer(
                layer_id="example:base_prompt",
                kind="base_prompt",
                title="Custom instructions",
                content=instructions,
                provenance="custom_agent",
                editable=True,
                locked=False,
                source_ref="custom_agent:test",
            )
        ],
    )
    return {
        "model_id": "example-model",
        "model_temperature": 0.0,
        "model_reasoning": None,
        "instructions": instructions,
        "instructions_hash": "sha256:"
        + hashlib.sha256(instructions.encode()).hexdigest(),
        "prompt_layer_manifest": bundle.to_manifest(),
        "group_prompt_layers": {},
        "tool_ids": ["read_document"],
        "system_managed_tool_ids": ["read_document"],
        "group_tool_policy": {"rules": []},
        "allowed_group_ids": ["TEAM_A"],
        "inherited_allowed_group_ids": ["TEAM_A"],
        "group_rules_enabled": False,
        "group_rules_component": None,
        "group_prompt_overrides": {},
        "template_source": None,
        "output_contract": {"output_state": "none"},
        "curation": None,
        "structured_finalization": None,
    }


def test_snapshot_preserves_zero_temperature_and_all_material_fields():
    raw = snapshot()
    saved = AgentExecutionSnapshot.model_validate(raw)
    assert saved.model_temperature == 0.0
    assert (
        AgentExecutionSnapshot.model_validate(
            saved.model_dump(mode="json")
        ).fingerprint()
        == saved.fingerprint()
    )
    raw["tool_ids"].append("another_tool")
    assert saved.tool_ids == ["read_document"]
    changed = saved.model_dump(mode="json")
    changed["group_rules_enabled"] = True
    assert (
        AgentExecutionSnapshot.model_validate(changed).fingerprint()
        != saved.fingerprint()
    )


@pytest.mark.parametrize(
    "change",
    [
        {"instructions": "Changed without updating the hash"},
        {"allowed_group_ids": []},
        {"allowed_group_ids": ["TEAM_B"]},
        {"tool_ids": []},
    ],
)
def test_snapshot_rejects_corruption_and_broadened_access(change):
    raw = deepcopy(snapshot())
    raw.update(change)
    with pytest.raises(ValidationError):
        AgentExecutionSnapshot.model_validate(raw)


def test_saved_prompt_layers_reject_tampering_without_live_template_reads(monkeypatch):
    from src.lib.prompts import assembly

    raw = snapshot()
    monkeypatch.setattr(
        assembly, "get_all_active_prompts", lambda: pytest.fail("read live templates")
    )
    saved = AgentExecutionSnapshot.model_validate(raw)
    assert (
        assembly.prompt_bundle_from_manifest(saved.prompt_layer_manifest).render()
        == saved.instructions
    )
    raw["prompt_layer_manifest"]["layers"][0]["content"] = "Changed"
    with pytest.raises(ValidationError, match="hash"):
        AgentExecutionSnapshot.model_validate(raw)
