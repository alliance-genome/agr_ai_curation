"""Custom profile saves replace mechanical builders, not curator settings."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.agent_studio.profile_builder_contract import (
    profile_builder_tool_ids,
    validate_profile_builder_tools,
)
from src.schemas.agent_execution_revision import AgentOutputContract, GenericProfilePin


def profile_output():
    return AgentOutputContract(
        output_state="structured_extraction", output_mode="profile_bound_generic",
        generic_profile_ref=GenericProfilePin(
            profile_id=uuid4(), profile_revision_id=uuid4(), revision=1,
            fingerprint="sha256:" + "a" * 64,
        ),
    )


def test_installed_builder_transition_preserves_non_builder_capabilities():
    original = [
        "search_document", "read_chunk", "record_evidence", "get_agent_contract",
        "agr_species_context_lookup", "stage_allele_observation",
        "patch_allele_observation", "discard_allele_observation",
        "list_staged_allele_observations", "find_staged_allele_observations",
        "finalize_allele_extraction",
    ]
    repaired = profile_builder_tool_ids(original)
    assert repaired[:5] == original[:5]
    assert set(repaired[5:]) == {
        "stage_generic_object", "patch_generic_object", "discard_generic_object",
        "list_staged_generic_objects", "find_staged_generic_objects",
        "finalize_generic_extraction",
    }
    assert original[-1] == "finalize_allele_extraction"
    assert profile_builder_tool_ids(repaired) == repaired
    validate_profile_builder_tools(profile_output(), repaired)
    # Runtime requires stage/finalize, not every optional lifecycle operation.
    validate_profile_builder_tools(profile_output(), ["stage_generic_object", "finalize_generic_extraction"])
    with pytest.raises(ValueError, match="save a new revision"):
        validate_profile_builder_tools(profile_output(), original)


def test_missing_installed_generic_builder_blocks_transition(monkeypatch):
    from src.lib.agent_studio import catalog_service
    monkeypatch.setattr(catalog_service, "_load_package_tool_registry", lambda: SimpleNamespace(
        bindings=[], bindings_by_tool_id={},
    ))
    with pytest.raises(ValueError, match="requires installed generic"):
        profile_builder_tool_ids(["record_evidence"])


def test_profile_save_repairs_head_before_snapshot_without_rewriting_old_revision(monkeypatch):
    from src.lib.agent_studio import custom_agent_service as service
    from src.lib.agent_studio import execution_snapshot, execution_revision_service
    output = profile_output()
    old_tools = ["read_chunk", "record_evidence", "stage_allele_observation", "finalize_allele_extraction"]
    head = SimpleNamespace(
        tool_ids=list(old_tools), instructions="Keep one existing allele; synonyms use |.",
        group_prompt_overrides={"TEST_GROUP": "Keep curator scientific guidance."},
        model_id="curator-model", model_reasoning="low", visibility="private",
        user_id=7,
    )
    preserved = deepcopy(vars(head))
    previous = SimpleNamespace(
        system_managed_tool_ids=list(old_tools[1:]), group_tool_policy={"rules": []},
        default_export_execution_mode=None,
    )
    captured = {}

    class Snapshot(SimpleNamespace):
        def model_copy(self, *, update):
            return Snapshot(**{**vars(self), **update})

    def capture(db, agent, selected, **kwargs):
        assert selected == output
        validate_profile_builder_tools(selected, agent.tool_ids)
        return Snapshot(tool_ids=list(agent.tool_ids), system_managed_tool_ids=list(agent.tool_ids[1:]))

    def append(db, agent, saved, **kwargs):
        captured["saved"] = saved
        captured["kwargs"] = kwargs
        return saved

    monkeypatch.setattr(execution_snapshot, "capture_execution_snapshot", capture)
    monkeypatch.setattr(execution_revision_service, "append_execution_revision", append)
    service._record_execution_save(
        None, head, expected_revision_id="old-pin", previous_output=output,
        previous_snapshot=previous,
    )
    assert "finalize_generic_extraction" in head.tool_ids
    assert "finalize_allele_extraction" not in captured["saved"].system_managed_tool_ids
    assert previous.system_managed_tool_ids == old_tools[1:]
    assert captured["kwargs"]["expected_revision_id"] == "old-pin"
    assert {k: v for k, v in vars(head).items() if k != "tool_ids"} == {
        k: v for k, v in preserved.items() if k != "tool_ids"
    }
