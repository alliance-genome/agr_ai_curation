from copy import deepcopy
from uuid import uuid4
from unittest.mock import Mock
import pytest

from src.lib.flows.profile_projection import profile_projection_fields
from src.schemas.generic_extraction_profile import GenericProfileContract
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.lib.agent_studio.profile_conformance import ResolvedGenericProfile, ProfileIdentityError
from src.lib.flows.output_projection import build_flow_output_artifact_bundle, apply_projection_plan, FlowOutputProjectionPlan


def _contract():
    return GenericProfileContract.model_validate({
        "name": "Records", "semantic_class": "record", "fields": [
            {"key": "count", "required": True, "value_schema": {"kind": "integer"}},
            {"key": "status", "value_schema": {"kind": "enum", "values": ["new", "known"]}},
            {"key": "sources", "value_schema": {"kind": "array", "items": {"kind": "object", "fields": [
                {"key": "name", "value_schema": {"kind": "string"}},
                {"key": "identifier", "nullable": True, "value_schema": {"kind": "string"}},
            ]}}},
            {"key": "details", "value_schema": {"kind": "object", "fields": [
                {"key": "enabled", "value_schema": {"kind": "boolean"}},
            ]}},
        ],
    })


def test_declared_paths_include_types_even_when_optional_values_are_absent():
    fields = {field.profile_path: field for field in profile_projection_fields(_contract())}
    assert set(fields) == {"attributes.count", "attributes.status", "attributes.sources",
                           "attributes.sources[].name", "attributes.sources[].identifier",
                           "attributes.details", "attributes.details.enabled"}
    assert fields["attributes.count"].value_type == "integer"
    assert fields["attributes.count"].required is True
    assert fields["attributes.status"].enum_values == ("new", "known")
    assert fields["attributes.sources"].schema_kind == "array"
    child = fields["attributes.sources[].identifier"]
    assert child.row_ref == "object.attribute.sources[].identifier"
    assert child.value_type == "list"
    assert child.schema_kind == "string"
    assert child.array_depth == 1
    assert child.nullable is True
    assert child.value_from({}) is None


def test_object_array_projection_preserves_missing_and_null_slots_and_source():
    fields = {field.profile_path: field for field in profile_projection_fields(_contract())}
    attributes = {"count": 0, "details": {"enabled": False}, "sources": [
        {"name": "A", "identifier": "A:1"}, {"name": "B"},
        {"identifier": "C:3"}, {"name": "D", "identifier": None}, {},
    ]}
    original = deepcopy(attributes)
    assert fields["attributes.sources[].name"].value_from(attributes) == ["A", "B", None, "D", None]
    assert fields["attributes.sources[].identifier"].value_from(attributes) == ["A:1", None, "C:3", None, None]
    assert fields["attributes.count"].value_from(attributes) == 0
    assert fields["attributes.details.enabled"].value_from(attributes) is False
    projected = fields["attributes.sources"].value_from(attributes)
    projected[0]["name"] = "display-only change"
    assert attributes == original


def test_nested_arrays_retain_each_level_instead_of_flattening():
    contract = GenericProfileContract.model_validate({
        "name": "Matrix", "semantic_class": "record", "fields": [
            {"key": "groups", "value_schema": {"kind": "array", "items": {"kind": "array", "items": {
                "kind": "object", "fields": [{"key": "name", "value_schema": {"kind": "string"}}],
            }}}},
        ],
    })
    fields = {field.profile_path: field for field in profile_projection_fields(contract)}
    child = fields["attributes.groups[][].name"]
    assert child.array_depth == 2
    assert child.value_from({"groups": [[{"name": "A"}, {}], [], [{"name": "C"}]]}) == [["A", None], [], ["C"]]


@pytest.fixture
def profile_step():
    contract = _contract()
    receipt = AgentExecutionReceipt(
        agent_id=uuid4(), agent_key="ca_fixture", agent_revision_id=uuid4(), revision=2,
        fingerprint="sha256:" + "a" * 64,
        output_contract={"output_state": "structured_extraction", "output_mode": "profile_bound_generic",
                         "generic_profile_ref": {"profile_id": uuid4(), "profile_revision_id": uuid4(),
                                                 "revision": 1, "fingerprint": contract.fingerprint()}},
    )
    pin = receipt.output_contract.generic_profile_ref
    profile = ResolvedGenericProfile(pin, contract)
    payload = {
        "curatable_objects": [{"object_type": "generic_object", "pending_ref_id": "record-1",
                               "payload": {"semantic_class": "record", "attributes": {"count": 0, "sources": [
                                   {"name": "A", "identifier": "A:1"}, {"name": "B"}, {"identifier": "C:3"},
                               ]}}, "metadata": {"generic_profile_ref": pin.model_dump(mode="json"),
                                                   "generic_extraction": {"class_key": "generic:generic_object"}}}],
        "metadata": {"provenance": {"produced_by": receipt.agent_key,
                                    "execution_receipt": receipt.model_dump(mode="json"),
                                    "generic_profile_ref": pin.model_dump(mode="json")}},
    }
    step = {"step": 1, "agent_id": receipt.agent_key,
            "candidate": {"agent_key": receipt.agent_key, "adapter_key": "generic", "candidate_count": 1,
                          "execution_receipt": receipt, "payload_json": payload}}
    return step, receipt, profile


@pytest.mark.parametrize("empty", [False, True])
def test_bundle_uses_saved_schema_for_types_and_optional_fields_even_without_records(profile_step, empty):
    step, receipt, profile = profile_step
    if empty:
        step["candidate"]["payload_json"]["curatable_objects"] = []
    loader = Mock(return_value=profile)
    bundle = build_flow_output_artifact_bundle(completed_steps=[step], flow_name="Profile", profile_resolver=loader)
    loader.assert_called_once_with(receipt)
    assert bundle.artifacts[0].execution_receipt == receipt
    fields = {field.ref: field for field in bundle.field_catalog}
    optional = fields["object.attribute.status"]
    assert optional.value_type == "string"
    assert optional.non_empty_count == 0
    assert optional.profile_bindings[0].enum_values == ["new", "known"]
    child = fields["object.attribute.sources[].identifier"]
    assert child.value_type == "list"
    assert child.profile_bindings[0].execution_receipt == receipt
    assert child.profile_bindings[0].array_depth == 1
    assert len(bundle.rows_for_source("object")) == (0 if empty else 1)


def test_existing_pair_join_and_conditional_consume_aligned_profile_values(profile_step):
    step, _, profile = profile_step
    before = deepcopy(step)
    bundle = build_flow_output_artifact_bundle(completed_steps=[step], flow_name="Profile", profile_resolver=lambda _: profile)
    plan = FlowOutputProjectionPlan.model_validate({
        "format": "tsv", "row_source": "object", "columns": [
            {"key": "sources", "transform": {"type": "pair_join", "field_refs": [
                "object.attribute.sources[].name", "object.attribute.sources[].identifier",
            ], "separator": "|", "pair_separator": ":"}},
            {"key": "status", "transform": {"type": "conditional", "field_ref": "object.attribute.status",
                "condition_op": "is_empty", "when_true": {"type": "literal", "value": "Not supplied"},
                "when_false": {"type": "literal", "value": "Supplied"}}},
        ],
    })
    result = apply_projection_plan(bundle, plan)
    assert result.rows == [{"sources": "A:A:1|B|C:3", "status": "Not supplied"}]
    assert step == before


@pytest.mark.parametrize("selector,shared_key", [
    ("source_keys", False), ("source_extraction_result_ids", False),
    ("source_extraction_result_ids", True),
])
@pytest.mark.parametrize("conditional", [False, True])
def test_numeric_predicate_checks_only_selected_saved_profiles(profile_step, selector, shared_key, conditional):
    from src.lib.flows.output_projection import validate_projection_plan
    numeric_step, numeric_receipt, numeric_profile = profile_step
    numeric_step.update(source_key="numeric-source", extraction_result_id="numeric-result")
    contract_json = numeric_profile.contract.model_dump(mode="json")
    contract_json["fields"][0]["value_schema"] = {"kind": "string"}
    text_contract = GenericProfileContract.model_validate(contract_json)
    text_pin = numeric_receipt.output_contract.generic_profile_ref.model_copy(update={
        "profile_revision_id": uuid4(), "fingerprint": text_contract.fingerprint(),
    })
    text_receipt = numeric_receipt.model_copy(update={
        "agent_revision_id": uuid4(),
        "output_contract": numeric_receipt.output_contract.model_copy(update={"generic_profile_ref": text_pin}),
    })
    text_profile = ResolvedGenericProfile(text_pin, text_contract)
    text_step = deepcopy(numeric_step)
    text_step.update(step=2, source_key="text-source", extraction_result_id="text-result")
    if shared_key:
        text_step["source_key"] = numeric_step["source_key"]
    text_step["candidate"]["execution_receipt"] = text_receipt
    payload = text_step["candidate"]["payload_json"]
    payload["curatable_objects"][0]["payload"]["attributes"]["count"] = "5"
    payload["curatable_objects"][0]["metadata"]["generic_profile_ref"] = text_profile.receipt
    payload["metadata"]["provenance"].update(
        execution_receipt=text_receipt.model_dump(mode="json"), generic_profile_ref=text_profile.receipt,
    )
    profiles = {numeric_receipt.agent_revision_id: numeric_profile, text_receipt.agent_revision_id: text_profile}
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[numeric_step, text_step], flow_name="Mixed profiles",
        profile_resolver=lambda receipt: profiles[receipt.agent_revision_id],
    )
    original = bundle.model_dump(mode="json")
    suffix = "source" if selector == "source_keys" else "result"
    plan_data = {"format": "tsv", "row_source": "object", selector: [f"numeric-{suffix}"],
                 "columns": [{"key": "count", "field_ref": "object.attribute.count"}]}
    if conditional:
        plan_data["columns"] = [{"key": "count", "transform": {
            "type": "conditional", "field_ref": "object.attribute.count", "condition_op": "gte", "value": 0,
            "when_true": {"type": "literal", "value": "Yes"},
            "when_false": {"type": "literal", "value": "No"},
        }}]
    else:
        plan_data["filters"] = [{"field_ref": "object.attribute.count", "op": "gte", "value": 0}]
    assert not validate_projection_plan(bundle, FlowOutputProjectionPlan.model_validate(plan_data))[0]
    if selector == "source_extraction_result_ids":
        union_plan = {**plan_data, "source_keys": [text_step["source_key"]], "row_strategy": "object_ledger"}
        assert any("not a scalar number" in error for error in validate_projection_plan(
            bundle, FlowOutputProjectionPlan.model_validate(union_plan),
        )[0])
    plan_data[selector] = [f"text-{suffix}"]
    errors = validate_projection_plan(bundle, FlowOutputProjectionPlan.model_validate(plan_data))[0]
    assert any("not a scalar number" in error for error in errors)
    assert bundle.model_dump(mode="json") == original


def test_profiled_bundle_cannot_use_missing_or_different_profile_resolver(profile_step):
    step, receipt, profile = profile_step
    with pytest.raises(ProfileIdentityError, match="resolver"):
        build_flow_output_artifact_bundle(completed_steps=[step], flow_name="Profile")
    different = ResolvedGenericProfile(receipt.output_contract.generic_profile_ref.model_copy(update={"profile_revision_id": uuid4()}), profile.contract)
    with pytest.raises(ProfileIdentityError):
        build_flow_output_artifact_bundle(completed_steps=[step], flow_name="Profile", profile_resolver=lambda _: different)


def test_saved_extraction_result_retains_profile_receipt_and_nested_values(profile_step):
    from datetime import datetime, timezone
    from src.schemas.curation_workspace import CurationExtractionResultRecord
    from src.lib.flows.output_projection import build_extraction_result_artifact_bundle
    step, receipt, profile = profile_step
    record = CurationExtractionResultRecord(
        extraction_result_id=str(uuid4()), document_id="doc-1", agent_key=receipt.agent_key,
        adapter_key="generic", source_kind="chat", created_at=datetime.now(timezone.utc),
        candidate_count=1, payload_json=step["candidate"]["payload_json"], execution_receipt=receipt,
    )
    before = record.model_dump(mode="json")
    bundle = build_extraction_result_artifact_bundle(extraction_results=[record], profile_resolver=lambda _: profile)
    assert bundle.artifacts[0].execution_receipt == receipt
    assert bundle.rows_for_source("object")[0]["object.attribute.sources[].identifier"] == ["A:1", None, "C:3"]
    assert record.model_dump(mode="json") == before
