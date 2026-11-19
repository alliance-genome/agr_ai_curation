"""A proposed null on a may-be-absent, may-not-be-null field means "leave it out" (ALL-1561).

Only the saved declaration decides, at any depth of a proposed value; no value is read.
"""
from uuid import uuid4

import pytest

from src.lib.agent_studio.profile_conformance import ResolvedGenericProfile
from src.lib.validation_review.extraction_results import structure_check
from src.lib.validation_review.source_projection import apply_value, plan
from src.schemas.agent_execution_revision import GenericProfilePin
from src.schemas.generic_extraction_profile import GenericProfileContract
from src.schemas.validation_followup import ProposedSourceValue


def _structure():
    contract = GenericProfileContract.model_validate({
        "name": "Reagents", "semantic_class": "reagent", "fields": [
            {"key": "name", "required": True, "value_schema": {"kind": "string"}},
            {"key": "note", "value_schema": {"kind": "string"}},
            {"key": "source", "value_schema": {"kind": "string"}},
            {"key": "remark", "nullable": True, "value_schema": {"kind": "string"}},
            {"key": "stage", "value_schema": {"kind": "enum", "values": ["embryo", "larva"]}},
            {"key": "details", "value_schema": {"kind": "object", "fields": [
                {"key": "lab", "value_schema": {"kind": "string"}},
                {"key": "room", "value_schema": {"kind": "string"}},
                {"key": "code", "required": True, "value_schema": {"kind": "string"}},
            ]}},
            {"key": "genes", "value_schema": {"kind": "array", "items": {"kind": "object", "fields": [
                {"key": "symbol", "required": True, "value_schema": {"kind": "string"}},
                {"key": "note", "value_schema": {"kind": "string"}},
            ]}}},
        ],
    })
    pin = GenericProfilePin(profile_id=uuid4(), profile_revision_id=uuid4(), revision=1,
                            fingerprint=contract.fingerprint())
    return ResolvedGenericProfile(pin, contract)


def _attributes():
    return {"name": "old name", "note": "old note", "source": "a supplier", "remark": "a remark",
            "details": {"lab": "a lab", "room": "a room", "code": "c1"},
            "genes": [{"symbol": "g1", "note": "first"}, {"symbol": "g2"}]}


def _entry():
    attributes = _attributes()
    fields = {"attributes": "t-attributes", **{f"attributes.{key}": f"t-{key}" for key in attributes},
              "attributes.details.lab": "t-lab", "attributes.details.room": "t-room",
              "attributes.details.code": "t-code", "attributes.genes[0].symbol": "t-g1-symbol",
              "attributes.genes[0].note": "t-g1-note", "attributes.genes[1].symbol": "t-g2-symbol"}
    return {"payload": {"attributes": attributes},
            "metadata": {"validation_history": {"target_id": "t-record", "fields": fields,
                                                "elements": {"attributes.genes": ["t-g1", "t-g2"]}}}}


def _proposals(*pairs):
    return [ProposedSourceValue(field_path=path, value=value, evidence_record_ids=[]) for path, value in pairs]


def _applied(*pairs):
    payload = {"attributes": _attributes()}
    for proposal in _proposals(*pairs):
        apply_value(payload, proposal, _structure())
    return payload["attributes"]


def test_declaration_alone_decides_which_nulls_are_removals():
    structure = _structure()
    assert structure.absent_when_null("attributes.source")
    assert structure.absent_when_null("attributes.details.lab")
    assert structure.absent_when_null("attributes.genes[0].note")  # a list item's field
    assert not structure.absent_when_null("attributes.genes[0].symbol")  # required in the item
    assert not structure.absent_when_null("attributes.genes[0]")  # a list element, not a field
    assert not structure.absent_when_null("attributes.name")  # required
    assert not structure.absent_when_null("attributes.remark")  # may be null
    assert not structure.absent_when_null("attributes.unknown")
    assert not structure.absent_when_null("attributes")


def test_reference_case_applies_fully_and_leaves_the_emptied_field_out():
    structure = _structure()
    entry = _entry()
    proposals = _proposals(("attributes.name", "new name"), ("attributes.note", "new note"),
                           ("attributes.source", None))
    assert structure_check(structure, "t-record", entry, proposals) == ({}, [], [])
    planned = plan(entry, proposals, contribution_id="contribution", structure=structure)
    attributes = planned["entry"]["payload"]["attributes"]
    expected = {**_attributes(), "name": "new name", "note": "new note"}
    del expected["source"]
    assert attributes == expected
    assert structure.validate_attributes(attributes) == []
    assert planned["retired_ids"] == ["t-source"]
    assert "attributes.source" not in planned["entry"]["metadata"]["validation_history"]["fields"]
    assert entry["payload"]["attributes"]["source"] == "a supplier"  # the original is untouched


def test_nested_object_null_is_left_out_and_matches_the_dotted_path():
    nested = _applied(("attributes.details", {"lab": None, "room": "new room", "code": "c1"}))
    dotted = _applied(("attributes.details.lab", None), ("attributes.details.room", "new room"))
    assert nested == dotted
    assert nested["details"] == {"room": "new room", "code": "c1"}


def test_list_item_null_is_left_out_and_matches_the_item_path():
    whole = _applied(("attributes.genes", [{"symbol": "g1", "note": None}, {"symbol": "g2"}]))
    item = _applied(("attributes.genes[0]", {"symbol": "g1", "note": None}))
    leaf = _applied(("attributes.genes[0].note", None))
    assert whole == item == leaf
    assert whole["genes"] == [{"symbol": "g1"}, {"symbol": "g2"}]


def test_null_on_a_required_field_is_refused_with_field_detail_at_any_depth():
    structure = _structure()
    proposals = _proposals(("attributes.note", "new note"), ("attributes.name", None),
                           ("attributes.details", {"lab": "x", "code": None}))
    fields, unexplained, _ = structure_check(structure, "t-record", _entry(), proposals)
    assert unexplained == []
    assert set(fields) == {"attributes.name", "attributes.details"}
    assert fields["attributes.name"] == {"structural_failure": "output_structure_conformance", "issues": [{
        "candidate_id": "t-record", "field_path": "attributes.name", "reason": "wrong_type",
        "expected": "string", "message": "Supply a string supported by evidence; values are not coerced."}]}
    assert [issue["field_path"] for issue in fields["attributes.details"]["issues"]] == ["attributes.details.code"]


def test_null_on_a_nullable_field_stays_a_null():
    structure = _structure()
    proposals = _proposals(("attributes.remark", None))
    assert structure_check(structure, "t-record", _entry(), proposals) == ({}, [], [])
    planned = plan(_entry(), proposals, contribution_id="contribution", structure=structure)
    assert planned["entry"]["payload"]["attributes"]["remark"] is None


def test_one_bad_field_leaves_its_siblings_selectable_and_they_apply():
    structure = _structure()
    entry = _entry()
    proposals = _proposals(("attributes.name", "new name"), ("attributes.note", "new note"),
                           ("attributes.stage", "adult"))
    fields, unexplained, _ = structure_check(structure, "t-record", entry, proposals)
    assert unexplained == [] and list(fields) == ["attributes.stage"]
    assert fields["attributes.stage"]["issues"][0]["reason"] == "invalid_enum"
    chosen = [item for item in proposals if item.field_path not in fields]
    planned = plan(entry, chosen, contribution_id="contribution", structure=structure)
    assert structure.validate_attributes(planned["entry"]["payload"]["attributes"]) == []


def test_removal_at_a_missing_list_index_cannot_be_written():
    with pytest.raises(ValueError, match="list index is missing"):
        _applied(("attributes.genes[5].note", None))
    structure = _structure()
    proposals = _proposals(("attributes.genes[5].note", None), ("attributes.note", "new note"))
    fields, unexplained, _ = structure_check(structure, "t-record", _entry(), proposals)
    assert unexplained == [] and list(fields) == ["attributes.genes[5].note"]
    assert fields["attributes.genes[5].note"]["structural_failure"] == "unwritable_path"


def test_removal_under_an_absent_object_changes_nothing():
    attributes = _attributes()
    del attributes["details"]
    payload = {"attributes": attributes}
    apply_value(payload, _proposals(("attributes.details.lab", None))[0], _structure())
    assert "details" not in payload["attributes"]


def test_payload_keys_outside_attributes_are_checked_as_at_publication():
    structure = _structure()
    fields, unexplained, _ = structure_check(structure, "t-record", _entry(),
                                          _proposals(("unknown_bag", "x"), ("attributes.note", "new note")))
    assert unexplained == [] and list(fields) == ["unknown_bag"]
    assert fields["unknown_bag"]["issues"][0]["reason"] == "undeclared_field"


def test_a_missing_required_sibling_belongs_to_the_fields_written_in_its_object():
    structure = _structure()
    attributes = _attributes()
    del attributes["details"]
    entry = {**_entry(), "payload": {"attributes": attributes}}
    fields, unexplained, _ = structure_check(structure, "t-record", entry,
                                          _proposals(("attributes.details.lab", "a lab"), ("attributes.note", "n")))
    assert unexplained == [] and list(fields) == ["attributes.details.lab"]
    assert fields["attributes.details.lab"]["issues"][0]["field_path"] == "attributes.details.code"


def _resolvable_structure():
    from tests.unit.lib.domain_packs.test_profile_validation import example, resolve
    raw, _, _ = example.__wrapped__()
    raw["fields"] = [
        {"key": "gene", "required": True, "value_schema": {"kind": "object", "fields": [
            {"key": "mention", "required": True, "value_schema": {"kind": "string"}},
            {"key": "curie", "value_schema": {"kind": "string"}}]}},
        {"key": "note", "value_schema": {"kind": "string"}},
    ]
    raw["validator_mappings"][0].update(inputs={"mention": {"field_path": "attributes.gene.mention"}},
                                        outputs={"identifier": "attributes.gene.curie"})
    return resolve(raw)[1]


def _resolvable_entry(structure):
    attributes = structure.unresolved_attributes({"gene": {"mention": "abc"}, "note": "old note"})
    return {"payload": {"attributes": attributes},
            "metadata": {"validation_history": {"target_id": "t-record", "fields": {}, "elements": {}}}}


def test_an_issue_on_the_object_above_a_proposed_field_blocks_only_that_field():
    structure = _resolvable_structure()
    entry = _resolvable_entry(structure)
    assert structure.validate_attributes(entry["payload"]["attributes"]) == []
    # An identity written into a value that is still unresolved breaks the value
    # as a whole: the issue is reported on attributes.gene, above the field.
    proposals = _proposals(("attributes.gene.curie", "ID:1"), ("attributes.note", "new note"))
    fields, unexplained, overlaps = structure_check(structure, "t-record", entry, proposals)
    assert unexplained == [] and overlaps == []
    assert list(fields) == ["attributes.gene.curie"]
    assert [(issue["field_path"], issue["reason"]) for issue in fields["attributes.gene.curie"]["issues"]] == [
        ("attributes.gene", "invalid_resolution")]


def test_an_issue_below_a_proposed_object_blocks_that_object_only():
    structure = _structure()
    proposals = _proposals(("attributes.details", {"lab": 7, "code": "c1"}), ("attributes.note", "new note"))
    fields, unexplained, _ = structure_check(structure, "t-record", _entry(), proposals)
    assert unexplained == [] and list(fields) == ["attributes.details"]
    assert fields["attributes.details"]["issues"][0]["field_path"] == "attributes.details.lab"


@pytest.mark.parametrize("order", [1, -1])
def test_overlapping_proposals_are_reported_and_checked_each_on_its_own(order):
    structure = _structure()
    attributes = _attributes()
    del attributes["details"]
    entry = {**_entry(), "payload": {"attributes": attributes}}
    proposals = _proposals(("attributes.details", {"lab": "x"}), ("attributes.details.code", "c2"),
                           ("attributes.note", "new note"))[::order]
    fields, unexplained, overlaps = structure_check(structure, "t-record", entry, proposals)
    assert unexplained == []
    assert overlaps == [("attributes.details", "attributes.details.code")]
    # Each overlapping field is checked alone, whatever the proposal order: the
    # object without its required code cannot apply, the code alone can.
    assert set(fields) == {"attributes.details"}
    assert [issue["reason"] for issue in fields["attributes.details"]["issues"]] == ["missing_required"]


def test_selectability_uses_every_issue_and_only_the_shown_list_is_bounded(monkeypatch):
    monkeypatch.setenv("GENERIC_PROFILE_MAX_ISSUES", "2")
    structure = _structure()
    proposals = _proposals(("attributes.details", {"lab": 1, "room": 2, "code": 3}),
                           ("attributes.genes", [{"symbol": 5}]), ("attributes.note", "new note"))
    fields, unexplained, _ = structure_check(structure, "t-record", _entry(), proposals)
    assert unexplained == []
    assert set(fields) == {"attributes.details", "attributes.genes"}  # genes' issue comes after the first two
    assert len(fields["attributes.details"]["issues"]) == 2
