"""Profile extensions compile into one draft without persistence or retargeting."""

from copy import deepcopy
from contextlib import nullcontext
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.lib.agent_studio.profile_authoring import ProfileEdit, apply_profile_edit
from src.lib.agent_studio.workshop_authoring import apply_workshop_operations
from src.lib.agent_studio.models import AgentWorkshopContext
from src.lib.agent_studio.profile_authoring import ProfileInspection, inspect_workshop_profile


def edit(output, **kwargs):
    return apply_profile_edit(output, ProfileEdit.model_validate(kwargs))


def initial():
    return edit({"mode": "profile_bound_generic", "profilePin": None, "profileContract": None},
                action="set_basics", basics={"name": "Details", "description": "One record per item", "semantic_class": "item"})


def test_nested_pairing_required_nullable_and_aliases_preserve_source_pin():
    output = initial()
    output["profilePin"] = {"profile_id": "unchanged"}
    before = deepcopy(output)
    output = edit(output, action="add_field", field={"key": "sources", "required": True, "nullable": False,
        "value_schema": {"kind": "object", "fields": []}})
    output = edit(output, action="add_field", field_path=["sources"], field={"key": "name", "nullable": True, "value_schema": {"kind": "string"}})
    output = edit(output, action="set_source_labels", field_path=["sources", "name"], source_labels=["Source name"])
    child = output["profileContract"]["fields"][0]["value_schema"]["fields"][0]
    assert child["source_labels"] == ["Source name"] and child["nullable"]
    assert output["profilePin"] == before["profilePin"]
    assert before["profileContract"]["fields"] == []
    with pytest.raises(ValueError, match="does not exist"):
        edit(output, action="remove_field", field_path=["sources", "Source name"])


def test_reorder_requires_exact_sibling_set_and_remove_preserves_unrelated_mapping():
    output = initial()
    for key in ["first", "second"]:
        output = edit(output, action="add_field", field={"key": key, "value_schema": {"kind": "string"}})
    with pytest.raises(ValueError, match="every sibling"):
        edit(output, action="reorder_fields", field_order=["first"])
    output = edit(output, action="reorder_fields", field_order=["second", "first"])
    output["profileContract"]["validator_mappings"] = [{"mapping_id": "retained", "inputs": {"mention": {"field_path": "attributes.first"}}}]
    output = edit(output, action="remove_field", field_path=["first"])
    assert [field["key"] for field in output["profileContract"]["fields"]] == ["second"]
    assert output["profileContract"]["validator_mappings"][0]["inputs"]["mention"]["field_path"] == "attributes.first"


def test_profile_edits_do_not_choose_an_output_mode_implicitly():
    with pytest.raises(ValueError, match="Select profile-bound"):
        edit({"mode": "none"}, action="set_basics", basics={"name": "Details", "description": "", "semantic_class": "item"})


def test_malformed_parent_is_rejected_without_preventing_replacement_repair():
    output = initial()
    output["profileContract"]["fields"] = [{"key": "sources"}]
    with pytest.raises(ValueError, match="object or repeating group"):
        edit(output, action="add_field", field_path=["sources"], field={"key": "name", "value_schema": {"kind": "string"}})
    repaired = edit(output, action="replace_field", field_path=["sources"], field={"key": "sources", "value_schema": {"kind": "object", "fields": []}})
    assert repaired["profileContract"]["fields"][0]["value_schema"]["kind"] == "object"
    assert output["profileContract"]["fields"] == [{"key": "sources"}]
    output["profileContract"]["validator_mappings"] = [{}]
    with pytest.raises(ValueError, match="mapping list is malformed"):
        edit(output, action="remove_mapping", mapping_id="missing")


def test_shared_workshop_compiler_owns_combined_profile_and_general_changes():
    base = AgentWorkshopContext(draft_name="Original", draft_output=initial())
    candidate = apply_workshop_operations(base, [
        {"operation": "set_name", "text": "Renamed"},
        {"operation": "edit_profile", "profile_edit": {"action": "add_field", "field": {"key": "labels", "value_schema": {"kind": "string"}}}},
    ])
    assert candidate.draft_name == "Renamed"
    assert candidate.draft_output is not None and base.draft_output is not None
    assert candidate.draft_output["profileContract"]["fields"][0]["key"] == "labels"
    assert base.draft_name == "Original" and base.draft_output["profileContract"]["fields"] == []


def test_public_tool_schema_resolves_nested_profile_definitions_from_its_root():
    from jsonschema import Draft202012Validator
    from src.api.agent_studio_opus_tools import PROPOSE_WORKSHOP_TOOL
    schema = PROPOSE_WORKSHOP_TOOL["input_schema"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    payload = {"base_draft_fingerprint": "sha256:fixture", "operations": [
        {"operation": "edit_profile", "profile_edit": {"action": "add_field", "field": {
            "key": "stock", "value_schema": {"kind": "object", "fields": [
                {"key": "name", "value_schema": {"kind": "string"}},
            ]},
        }}},
        {"operation": "edit_profile", "profile_edit": {"action": "update_field", "field_path": ["stock", "name"],
         "field_update": {"description": "Keep the exact name.", "required": True, "nullable": False,
                          "value_schema": {"kind": "enum", "values": ["a", "b"]}}}},
        {"operation": "edit_profile", "profile_edit": {"action": "update_basics", "basics_update": {"description": "Only stocks."}}},
    ]}
    assert list(validator.iter_errors(payload)) == []
    payload["operations"][1]["profile_edit"]["field_update"]["value_schema"]["kind"] = "arbitrary"
    assert list(validator.iter_errors(payload))


def test_profile_inspection_is_read_only_and_preview_is_explicitly_placeholder():
    output = initial()
    output["profileContract"]["fields"] = [{"key": "labels", "value_schema": {"kind": "array", "items": {"kind": "string"}}}]
    workshop = AgentWorkshopContext(draft_output=output)
    db = Mock()
    before = workshop.model_dump()
    result = inspect_workshop_profile(db, workshop=workshop, user_id=1, active_group_ids=[], request=ProfileInspection(action="preview"))
    assert result["placeholder_data"] and not result["paper_evidence"] and not result["saved"]
    assert result["example_attributes"] == {"labels": ["Example text"]}
    assert workshop.model_dump() == before
    assert db.mock_calls == []
    with pytest.raises(ValueError, match="authenticated"):
        inspect_workshop_profile(db, workshop=workshop, user_id=None, active_group_ids=[], request=ProfileInspection(action="current"))


def test_saved_inspection_passes_exact_revision_and_authenticated_user_to_authorization(monkeypatch):
    from src.lib.agent_studio import generic_profile_service as service
    profile_id, revision_id = uuid4(), uuid4()
    read = Mock(return_value=SimpleNamespace(profile_id=profile_id, id=revision_id, revision=3, fingerprint="exact", contract={}))
    monkeypatch.setattr(service, "get_profile_revision", read)
    db = Mock()
    request = ProfileInspection(action="saved_revision", profile_id=str(profile_id), revision=3)
    result = inspect_workshop_profile(db, workshop=AgentWorkshopContext(), user_id=42, active_group_ids=[], request=request)
    read.assert_called_once_with(db, profile_id, 3, 42, include_archived=True)
    assert result["profile_revision_id"] == str(revision_id) and not result["selected_in_draft"]
    read.side_effect = service.ProfileNotFoundError("Profile not found")
    with pytest.raises(service.ProfileNotFoundError):
        inspect_workshop_profile(db, workshop=AgentWorkshopContext(), user_id=43, active_group_ids=[], request=request)
    db.commit.assert_not_called()


def test_saved_list_preserves_authorized_cursor(monkeypatch):
    from src.lib.agent_studio import generic_profile_service as service
    cursor = uuid4()
    listing = Mock(return_value=([], cursor))
    monkeypatch.setattr(service, "list_profiles", listing)
    db = Mock()
    result = inspect_workshop_profile(db, workshop=AgentWorkshopContext(), user_id=42, active_group_ids=[],
                                     request=ProfileInspection(action="list_saved", after=str(cursor)))
    listing.assert_called_once_with(db, 42, after_id=cursor)
    assert result == {"profiles": [], "next_cursor": str(cursor)}


def test_profile_inspection_tool_is_only_available_in_workshop():
    from src.api.agent_studio_opus_tools import is_tool_allowed_for_context
    from src.lib.agent_studio.models import ChatContext
    assert not is_tool_allowed_for_context("inspect_workshop_profile", None)
    assert not is_tool_allowed_for_context("inspect_workshop_profile", ChatContext.model_validate({"active_tab": "agents"}))
    assert is_tool_allowed_for_context("inspect_workshop_profile", ChatContext.model_validate({"active_tab": "agent_workshop", "agent_workshop": AgentWorkshopContext()}))


def test_workshop_policy_explains_record_boundaries_and_forbids_inferred_validation(monkeypatch):
    from src.lib.agent_studio.models import ChatContext
    from src.lib.agent_studio.prompt_builder import build_opus_system_prompt
    monkeypatch.setattr("src.lib.agent_studio.prompt_builder.build_package_diagnostic_tools_prompt", lambda: "")
    prompt = build_opus_system_prompt(
        ChatContext.model_validate({"active_tab": "agent_workshop", "agent_workshop": AgentWorkshopContext()}),
        load_template=lambda: "Base guidance", list_model_definitions=lambda: [],
        get_prompt_catalog=lambda: None, prepare_trace_context=lambda _: None,
    )
    for required in ["one-record boundary", "required (must exist)", "nullable", "catalog number paired", "Always include", "update_field", "update_basics",
                     "IN ADDITION TO", "Do not create arrays or repeating groups", "never put groups inside parts",
                     "Synonyms / source labels (not output fields)", "Never infer a validator solely from a field name",
                     "never invoke persistence or open its confirmation", "A null schema never implies open extraction",
                     "not LinkML-aligned or submission-ready", "Extraction-time agents cannot edit"]:
        assert required in prompt


@pytest.mark.asyncio
async def test_inspection_dispatcher_requires_active_authenticated_workshop(monkeypatch):
    import src.api.agent_studio as api
    from src.lib.agent_studio.models import ChatContext
    db = Mock()
    monkeypatch.setattr(api, "SessionLocal", lambda: nullcontext(db))
    workshop = AgentWorkshopContext(draft_output=initial())
    args = {"tool_name": "inspect_workshop_profile", "tool_input": {"action": "current"},
            "user_email": "fixture@example.org", "user_auth_sub": "fixture-auth"}
    for context, user_id in [(None, 1), (ChatContext.model_validate({"active_tab": "agents"}), 1),
                             (ChatContext.model_validate({"active_tab": "agent_workshop", "agent_workshop": workshop}), None)]:
        result = await api._handle_tool_call(**args, context=context, user_db_id=user_id)
        assert result["success"] is False
    result = await api._handle_tool_call(**args, context=ChatContext.model_validate({"active_tab": "agent_workshop", "agent_workshop": workshop}), user_db_id=1)
    assert result["success"] and result["output"] == workshop.draft_output
    assert db.mock_calls == []


def test_profile_inspection_uses_existing_bounded_provider_result_and_recall(monkeypatch):
    import src.api.agent_studio as api
    monkeypatch.setattr(api, "get_agent_studio_provider_tool_result_inline_max_chars", lambda: 1500)
    result = {"success": True, "output": {"profileContract": {"description": "private draft " * 1000}}}
    content = api._provider_tool_result_content(tool_name="inspect_workshop_profile", tool_input={"action": "current"},
                                               tool_result=result, session_id="session", turn_id="turn")
    assert len(content) <= 1500
    payload = json.loads(content)
    assert payload["status"] == "compacted_tool_result"
    assert payload["recall"]["turn"]["tool"] == "get_chat_turn"


def test_targeted_settings_preserve_keys_siblings_prompts_and_source_pin():
    base = AgentWorkshopContext(prompt_draft="Earlier extraction prompt", draft_output=initial())
    base.draft_output["profilePin"] = {"profile_id": "keep-this-pin"}
    base.draft_output = edit(base.draft_output, action="add_field", field={
        "key": "stock", "display_name": "Stock", "value_schema": {"kind": "object", "fields": [
            {"key": "name", "display_name": "Name", "description": "Keep exact spelling.",
             "source_labels": ["Supplied name"], "value_schema": {"kind": "string"}},
            {"key": "number", "display_name": "Stock number", "value_schema": {"kind": "string"}},
        ]},
    })
    before = base.model_dump()
    candidate = apply_workshop_operations(base, [
        {"operation": "edit_profile", "profile_edit": {"action": "update_basics", "basics_update": {
            "description": "Only living stocks; one record per distinct stock."}}},
        {"operation": "edit_profile", "profile_edit": {"action": "update_field", "field_path": ["stock", "number"],
            "field_update": {"required": True, "nullable": True}}},
        {"operation": "edit_profile", "profile_edit": {"action": "update_field", "field_path": ["stock", "name"],
            "field_update": {"display_name": "Supplier name"}}},
    ])
    result = candidate.draft_output
    fields = result["profileContract"]["fields"][0]["value_schema"]["fields"]
    assert fields[1]["required"] and fields[1]["nullable"]
    assert not fields[0]["required"] and not fields[0]["nullable"]
    assert fields[0]["key"] == "name" and fields[0]["source_labels"] == ["Supplied name"]
    assert fields[0]["description"] == "Keep exact spelling."
    assert result["profileContract"]["semantic_class"] == "item"
    assert result["profilePin"] == base.draft_output["profilePin"]
    assert candidate.prompt_draft == base.prompt_draft
    assert base.model_dump() == before
    cleared = edit(result, action="update_field", field_path=["stock", "number"],
                   field_update={"required": False, "nullable": False, "description": ""})
    number = cleared["profileContract"]["fields"][0]["value_schema"]["fields"][1]
    assert not number["required"] and not number["nullable"] and number["description"] == ""


@pytest.mark.parametrize("field_update", [{}, {"required": None}, {"key": "rename"}])
def test_targeted_updates_reject_empty_null_or_identity_edits(field_update):
    output = edit(initial(), action="add_field", field={"key": "name", "value_schema": {"kind": "string"}})
    before = deepcopy(output)
    with pytest.raises(ValueError):
        edit(output, action="update_field", field_path=["name"], field_update=field_update)
    assert output == before


@pytest.mark.parametrize("schema", [
    {"kind": "array", "items": {"kind": "string"}},
    {"kind": "object", "fields": [{"key": "nested", "value_schema": {"kind": "object", "fields": []}}]},
])
def test_new_answer_shapes_reject_lists_and_parts_with_parts(schema):
    output = initial()
    before = deepcopy(output)
    with pytest.raises(ValueError):
        edit(output, action="add_field", field={"key": "stock", "value_schema": schema})
    assert output == before


def test_cannot_add_or_convert_a_part_into_another_group():
    output = edit(initial(), action="add_field", field={"key": "stock", "value_schema": {"kind": "object", "fields": [
        {"key": "number", "value_schema": {"kind": "string"}},
    ]}})
    with pytest.raises(ValueError, match="parts cannot contain"):
        edit(output, action="add_field", field_path=["stock"], field={"key": "nested", "value_schema": {"kind": "object", "fields": []}})
    with pytest.raises(ValueError, match="parts cannot contain"):
        edit(output, action="update_field", field_path=["stock", "number"], field_update={"value_schema": {"kind": "object", "fields": []}})


def test_existing_lists_survive_metadata_edits_and_require_explicit_conversion():
    output = initial()
    output["profileContract"]["fields"] = [{"key": "names", "value_schema": {"kind": "array", "items": {"kind": "string"}}}]
    renamed = edit(output, action="update_field", field_path=["names"], field_update={"display_name": "Name"})
    assert renamed["profileContract"]["fields"][0]["value_schema"] == output["profileContract"]["fields"][0]["value_schema"]
    converted = edit(renamed, action="update_field", field_path=["names"], field_update={"value_schema": {"kind": "string"}})
    assert converted["profileContract"]["fields"][0]["value_schema"] == {"kind": "string"}


def test_validator_discovery_preserves_custom_pins_and_caller_without_mutating_draft(monkeypatch):
    from src.lib.agent_studio import profile_mapping_options as service
    from .test_profile_mappings import fixture
    raw, capability = fixture()
    mapping = raw["validator_mappings"][0]
    mapping["capability_ref"]["binding_id"] += "--custom--" + str(uuid4())
    mapping["inputs"]["mention"]["field_path"] = "attributes.removed_part"
    workshop = AgentWorkshopContext(draft_output={"mode": "profile_bound_generic", "profileContract": raw})
    before = deepcopy(workshop.draft_output)
    options = {"capabilities": [{"metadata": {"origin": origin}} for origin in ("package", "custom_agent")],
               "next_cursor": "next-page", "fields": []}
    discover = Mock(return_value=options)
    monkeypatch.setattr(service, "profile_mapping_options", discover)
    result = inspect_workshop_profile(Mock(), workshop=workshop, user_id=42, active_group_ids=["GROUP_A"],
                                      request=ProfileInspection(action="validator_options", after="page-two"))
    assert result["capabilities"] == options["capabilities"] and result["next_cursor"] == "next-page"
    assert discover.call_args.kwargs == {"user_id": 42, "active_group_ids": ["GROUP_A"], "after": "page-two"}
    query_mapping = discover.call_args.args[0].validator_mappings[0]
    assert query_mapping.capability_ref.model_dump() == mapping["capability_ref"]
    assert query_mapping.capability_fingerprint == capability.fingerprint()
    assert not query_mapping.inputs and not query_mapping.outputs
    assert workshop.draft_output == before
