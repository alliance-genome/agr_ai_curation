"""Editor options reuse save-time rules and never infer source-label paths."""

from copy import deepcopy
from dataclasses import replace

from src.lib.agent_studio.profile_mapping_options import profile_mapping_options
from src.lib.agent_studio.profile_mapping_service import validate_profile_mappings
from src.lib.domain_packs.validation_registry import ValidationBindingState
from src.schemas.generic_extraction_profile import normalize_profile_contract
from .test_profile_mappings import fixture


def options(raw, cap):
    return profile_mapping_options(normalize_profile_contract(raw), active_group_ids=[], capabilities=[cap])


def test_input_output_direction_nullability_and_aliases_match_save_rules():
    raw, cap = fixture()
    result = options(raw, cap)
    chosen = result["capabilities"][0]
    assert chosen["input_paths"] == {"mention": ["attributes.paper_name"]}
    assert chosen["output_paths"] == {"identifier": ["attributes.paper_name", "attributes.resolved_id"]}
    assert all("Paper label" not in field["path"] for field in result["fields"])
    assert raw["validator_mappings"][0]["inputs"] == {"mention": {"field_path": "attributes.paper_name"}}
    for path in chosen["input_paths"]["mention"]:
        candidate = deepcopy(raw)
        candidate["validator_mappings"][0]["inputs"]["mention"]["field_path"] = path
        validate_profile_mappings(candidate, capabilities=[cap])


def test_array_fanout_and_optional_parent_restrictions():
    raw, cap = fixture()
    raw["validator_mappings"] = []
    raw["fields"] = [{"key": "records", "required": True,
                      "value_schema": {"kind": "array", "items": {"kind": "object", "fields": raw["fields"]}}}]
    assert options(raw, cap)["capabilities"][0]["input_paths"]["mention"] == []
    reuse = cap.binding.custom_profile_reuse.model_copy(update={"supports_element_fanout": True})
    cap = replace(cap, binding=replace(cap.binding, custom_profile_reuse=reuse))
    result = options(raw, cap)
    assert result["capabilities"][0]["input_paths"]["mention"] == ["attributes.records[].paper_name"]
    assert result["capabilities"][0]["output_paths"]["identifier"] == ["attributes.records[].paper_name", "attributes.records[].resolved_id"]
    assert next(f for f in result["fields"] if f["path"].endswith(".paper_name"))["array_domains"] == ["attributes.records[]"]
    raw["fields"][0]["required"] = False
    assert options(raw, cap)["capabilities"][0]["input_paths"]["mention"] == []


def test_unsupported_nested_fanout_is_not_offered():
    raw, cap = fixture()
    raw["validator_mappings"] = []
    for key in ["inner", "outer"]:
        raw["fields"] = [{"key": key, "required": True, "value_schema": {
            "kind": "array", "items": {"kind": "object", "fields": raw["fields"]}}}]
    reuse = cap.binding.custom_profile_reuse.model_copy(update={"supports_element_fanout": True})
    cap = replace(cap, binding=replace(cap.binding, custom_profile_reuse=reuse))
    result = options(raw, cap)["capabilities"][0]
    assert result["input_paths"]["mention"] == [] and result["output_paths"]["identifier"] == []


def test_unavailable_capabilities_are_explained_not_silently_executable():
    raw, cap = fixture()
    cap = replace(cap, binding=replace(cap.binding, state=ValidationBindingState.UNDER_DEVELOPMENT))
    result = options(raw, cap)["capabilities"][0]
    assert result["state"] == "under_development" and not result["selectable"]
    assert result["diagnostics"] and result["input_paths"]["mention"]


def test_capability_pagination_reuses_configured_limit(monkeypatch):
    raw, cap = fixture()
    other = replace(cap, ref=cap.ref.model_copy(update={"binding_id": "z_lookup"}))
    monkeypatch.setenv("GENERIC_PROFILE_LIST_PAGE_SIZE", "1")
    parsed = normalize_profile_contract(raw)
    first = profile_mapping_options(parsed, active_group_ids=[], capabilities=[other, cap])
    assert len(first["capabilities"]) == 1 and first["next_cursor"] == cap.key()
    last = profile_mapping_options(parsed, active_group_ids=[], capabilities=[other, cap], after=first["next_cursor"])
    assert last["capabilities"][0]["capability_ref"]["binding_id"] == "z_lookup" and last["next_cursor"] is None
