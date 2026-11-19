"""A proposed null on a may-be-absent, may-not-be-null field means "leave it out" (ALL-1561).

Only the saved declaration decides; no value is read.
"""
from uuid import uuid4

from src.lib.agent_studio.profile_conformance import ResolvedGenericProfile
from src.lib.validation_review.extraction_results import _structure_issues
from src.lib.validation_review.source_projection import plan, removed_fields
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
            {"key": "details", "value_schema": {"kind": "object", "fields": [
                {"key": "lab", "value_schema": {"kind": "string"}},
            ]}},
        ],
    })
    pin = GenericProfilePin(profile_id=uuid4(), profile_revision_id=uuid4(), revision=1,
                            fingerprint=contract.fingerprint())
    return ResolvedGenericProfile(pin, contract)


def _entry():
    attributes = {"name": "old name", "note": "old note", "source": "a supplier",
                  "remark": "a remark", "details": {"lab": "a lab"}}
    fields = {"attributes": "t-attributes", **{f"attributes.{key}": f"t-{key}" for key in attributes},
              "attributes.details.lab": "t-lab"}
    return {"payload": {"attributes": attributes},
            "metadata": {"validation_history": {"target_id": "t-record", "fields": fields, "elements": {}}}}


def _proposals(*pairs):
    return [ProposedSourceValue(field_path=path, value=value, evidence_record_ids=[]) for path, value in pairs]


def test_declaration_alone_decides_which_nulls_are_removals():
    structure = _structure()
    assert structure.absent_when_null("attributes.source")
    assert structure.absent_when_null("attributes.details.lab")
    assert not structure.absent_when_null("attributes.name")  # required
    assert not structure.absent_when_null("attributes.remark")  # may be null
    assert not structure.absent_when_null("attributes.unknown")
    assert not structure.absent_when_null("attributes")
    proposals = _proposals(("attributes.source", None), ("attributes.note", ""), ("attributes.remark", None))
    assert removed_fields(structure, proposals) == {"attributes.source"}
    assert removed_fields(None, proposals) == frozenset()


def test_reference_case_applies_fully_and_leaves_the_emptied_field_out():
    structure = _structure()
    entry = _entry()
    proposals = _proposals(("attributes.name", "new name"), ("attributes.note", "new note"),
                           ("attributes.source", None))
    assert _structure_issues(structure, entry, proposals) == []
    planned = plan(entry, proposals, contribution_id="contribution",
                   removed=removed_fields(structure, proposals))
    attributes = planned["entry"]["payload"]["attributes"]
    assert attributes == {"name": "new name", "note": "new note", "remark": "a remark",
                          "details": {"lab": "a lab"}}
    assert structure.validate_attributes(attributes) == []
    assert planned["retired_ids"] == ["t-source"]
    assert "attributes.source" not in planned["entry"]["metadata"]["validation_history"]["fields"]
    assert entry["payload"]["attributes"]["source"] == "a supplier"  # the original is untouched


def test_null_on_a_required_field_is_refused_with_field_detail():
    structure = _structure()
    proposals = _proposals(("attributes.note", "new note"), ("attributes.name", None))
    assert removed_fields(structure, proposals) == frozenset()
    assert _structure_issues(structure, _entry(), proposals) == [{
        "field_path": "attributes.name", "reason": "wrong_type", "expected": "string",
        "message": "Supply a string supported by evidence; values are not coerced."}]


def test_null_on_a_nullable_field_stays_a_null():
    structure = _structure()
    proposals = _proposals(("attributes.remark", None))
    assert _structure_issues(structure, _entry(), proposals) == []
    planned = plan(_entry(), proposals, contribution_id="contribution",
                   removed=removed_fields(structure, proposals))
    assert planned["entry"]["payload"]["attributes"]["remark"] is None


def test_a_proposal_that_breaks_the_structure_otherwise_is_reported():
    issues = _structure_issues(_structure(), _entry(), _proposals(("attributes.note", 7)))
    assert [(issue["field_path"], issue["reason"]) for issue in issues] == [("attributes.note", "wrong_type")]
