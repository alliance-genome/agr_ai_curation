"""Provider schema admission and lossless decoding of targeted source proposals."""
import json
import traceback

import pytest
from agents import Agent
from agents.agent_output import AgentOutputSchema
from agents.models.openai_responses import Converter

from src.lib.validation_history.store import HistoryScope
from src.lib.validation_review.extractor_phase import create_phase
from src.schemas.validation_followup import TargetedFollowupResult, TargetedFollowupTransport


def transport_payload(encoded):
    return {"entries": [{"entry_id": "entry", "outcome": "proposed_correction",
        "assessment": "Source clarifies the value.", "remaining_uncertainty": "",
        "proposed_values": [{"field_path": "details", "value_json": encoded,
            "evidence_record_ids": ["methods"], "collection_mappings": []}],
        "evidence_record_ids": ["methods"]}]}


@pytest.mark.parametrize("value", [
    'α "quoted" \\ gene', "[1]", None, True, False, 42, -7, 1.25,
    [], {}, ["x", None, 3, False], {"arbitrary key": [1, {"nested": True}]},
])
def test_decode_preserves_native_values_and_evidence(value):
    result = TargetedFollowupTransport.model_validate(transport_payload(json.dumps(value))).decode()
    proposed = result.entries[0].proposed_values[0]
    assert proposed.value == value and type(proposed.value) is type(value)
    assert proposed.evidence_record_ids == ["methods"]
    assert result.entries[0].evidence_record_ids == ["methods"]
    assert "value_json" not in proposed.model_dump()
    # Re-reading canonical scientific strings such as '[1]' must not decode again.
    assert TargetedFollowupResult.model_validate(result.model_dump()).entries[0].proposed_values[0].value == value


@pytest.mark.parametrize("encoded", [
    "SENSITIVE_UNQUOTED", '"SENSITIVE" trailing', '{"SENSITIVE":1,"SENSITIVE":2}',
    'NaN', 'Infinity', '-Infinity', '1e400', '{"x":[1e400]}',
    '', '```json\n1\n```',
])
def test_invalid_encoding_fails_without_value_in_error(encoded):
    with pytest.raises(ValueError, match="one valid finite JSON value") as error:
        TargetedFollowupTransport.model_validate(transport_payload(encoded)).decode()
    assert "SENSITIVE" not in str(error.value)
    assert "SENSITIVE" not in "".join(traceback.format_exception(error.value))



def test_provider_transport_rejects_old_native_shape():
    from pydantic import ValidationError
    payload = transport_payload('"gene A"')
    proposed = payload["entries"][0]["proposed_values"][0]
    proposed["value"] = proposed.pop("value_json")
    with pytest.raises(ValidationError) as error:
        TargetedFollowupTransport.model_validate(payload)
    assert {item["type"] for item in error.value.errors()} == {"missing", "extra_forbidden"}


def test_collection_mapping_is_preserved():
    payload = transport_payload('[{"name":"new"}]')
    mapping = {"collection_path": "details", "source_collection_id": "collection",
        "items": [{"item_key": "0", "relationship": "revise", "predecessor_ids": ["old"], "reason": "Correct source"}],
        "retired_item_ids": [], "retirement_reason": ""}
    payload["entries"][0]["proposed_values"][0]["collection_mappings"] = [mapping]
    result = TargetedFollowupTransport.model_validate(payload).decode()
    assert result.entries[0].proposed_values[0].collection_mappings[0].model_dump() == mapping


def assert_closed_typed_schema(schema):
    """Focused provider subset regression, not a complete provider emulator.

    https://developers.openai.com/api/docs/guides/structured-outputs
    minLength/minItems are supported for the non-fine-tuned models used here.
    """
    allowed = {"type", "properties", "required", "additionalProperties", "items",
               "$defs", "$ref", "anyOf", "title", "description", "enum", "minLength", "minItems"}
    def visit(node):
        assert set(node) <= allowed, f"Unexpected schema keywords: {set(node) - allowed}"
        assert any(key in node for key in ("type", "$ref", "anyOf")), "Untyped schema node"
        if "$ref" in node:
            assert node["$ref"].startswith("#/$defs/")
            assert node["$ref"].removeprefix("#/$defs/") in schema["$defs"]
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                visit(child)
        if node.get("type") == "array":
            visit(node["items"])
        for child in node.get("anyOf", []):
            visit(child)
        for child in node.get("$defs", {}).values():
            visit(child)
    visit(schema)


def test_actual_phase_responses_schema_is_typed_and_old_any_fails(phase_trace):
    agent = Agent(name="Saved extractor", model="gpt-test", instructions="Domain extraction policy")
    phase = create_phase(agent,
        scope=HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
        artifact_id="artifact", packet={"history": {"records": [{"fact_id": "context", "sha256": "exact"}]}}, packet_bytes=2048,
        evidence_records=[])
    wire = Converter.get_response_format(AgentOutputSchema(phase.output_type))
    assert wire["format"]["type"] == "json_schema" and wire["format"]["strict"] is True
    assert_closed_typed_schema(wire["format"]["schema"])
    old_wire = Converter.get_response_format(AgentOutputSchema(TargetedFollowupResult))
    with pytest.raises(AssertionError, match="Untyped schema node"):
        assert_closed_typed_schema(old_wire["format"]["schema"])


def test_whole_record_retirement_is_distinct_from_field_replacement():
    from pydantic import ValidationError
    from src.schemas.validation_followup import FollowupEntryResult
    payload = transport_payload('[]')
    entry = payload["entries"][0]
    entry.update(outcome="propose_retirement", proposed_values=[])
    decoded = TargetedFollowupTransport.model_validate(payload).decode()
    assert FollowupEntryResult.model_validate(decoded.entries[0].model_dump()).outcome == "propose_retirement"
    entry["proposed_values"] = transport_payload('[]')["entries"][0]["proposed_values"]
    decoded = TargetedFollowupTransport.model_validate(payload).decode()
    with pytest.raises(ValidationError, match="no field replacements"):
        FollowupEntryResult.model_validate(decoded.entries[0].model_dump())


def test_incident_shape_is_retained_for_ingress_but_never_valid_selection():
    from pydantic import ValidationError
    from src.schemas.validation_followup import FollowupEntryResult
    payload = transport_payload('[]')
    proposed = payload["entries"][0]["proposed_values"][0]
    proposed["field_path"] = "entries"
    proposed["collection_mappings"] = [{"collection_path": "entries", "source_collection_id": None,
        "items": [], "retired_item_ids": ["entry"], "retirement_reason": "Outside task scope"}]
    decoded = TargetedFollowupTransport.model_validate(payload).decode()
    assert decoded.entries[0].assessment == payload["entries"][0]["assessment"]
    with pytest.raises(ValidationError, match="new collection cannot refer to old items"):
        FollowupEntryResult.model_validate(decoded.entries[0].model_dump())
