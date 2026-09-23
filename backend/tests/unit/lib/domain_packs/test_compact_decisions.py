"""Scientific decisions preserve authoritative facts and request isolation."""
from copy import deepcopy

import pytest
from pydantic import TypeAdapter, ValidationError

from src.lib.domain_packs.compact_decisions import (
    CanonicalValidatorRecord, CompactValidatorDecision, DecisionContract,
    ValidatorDecisionWorkspace,
)
from src.schemas.domain_validator import (
    DomainValidationRequest, DomainValidatorResultBase, ValidatorCandidate,
    ValidatorLookupAttempt,
)


def request(identifier="first"):
    return DomainValidationRequest(
        request_id=identifier, validator_binding_id="fixture.identity",
        validator_agent={"package_id": "fixture", "agent_id": "validator"},
        target={"domain_pack_id": "fixture", "object_id": identifier},
        selected_inputs={"mention": "conditional allele"},
        expected_result_fields={"identifier": "identity.id", "design_matches": "identity.matches"},
        evidence=[{"evidence_record_id": "evidence-1", "source_document_id": "paper-1"}],
    )


def workspace():
    result = ValidatorDecisionWorkspace([
        DecisionContract(request(), profile_mapped=True,
                         scientific_slots={"design_matches": TypeAdapter(bool)}),
        DecisionContract(request("second")),
    ])
    references = result.record_lookup("first", call_id="call-1", attempt=ValidatorLookupAttempt(
        provider="fixture", method="search", query={"mention": "conditional allele"},
        result_count=26, outcome="ambiguous", coverage={"returned_count": 26, "display_capped": True},
    ), records=[CanonicalValidatorRecord(
        candidate=ValidatorCandidate(value="EX:101", label="Conditional allele",
                                     details={"provider": None, "synonyms": ["flox"]}),
        values={"identifier": "EX:101", "provider": None},
        resolved_object={"identifier": "EX:101", "symbol": "Conditional allele"},
    )])
    return result, references[0]


def decision(reference):
    return {
        "request_id": "first", "status": "resolved",
        "candidates": [{"record_ref": reference, "disposition": "selected",
                        "explanation": "Design and source are consistent.", "evidence_record_ids": ["evidence-1"]}],
        "slots": {
            "identifier": {"kind": "record", "record_ref": reference, "field": "identifier"},
            "design_matches": {"kind": "scientific", "value": True,
                               "explanation": "The evidence describes the conditional design.",
                               "evidence_record_ids": ["evidence-1"]},
        },
        "explanation": "Identity established from the supplied evidence.",
    }


def test_canonical_assembly_keeps_counts_facts_assessments_and_typed_profile_slots():
    store, ref = workspace()
    result = store.assemble(CompactValidatorDecision.model_validate(decision(ref)))
    assert result.request_id == "first"
    assert result.target.object_id == "first"
    assert result.resolved_values == {"identifier": "EX:101", "design_matches": True}
    assert result.resolved_objects == []
    assert result.lookup_attempts[0].result_count == 26
    assert result.lookup_attempts[0].coverage["display_capped"] is True
    assert len(result.candidates) == 1
    assert result.candidates[0].details["provider"] is None
    assert result.candidates[0].details["scientific_assessment"]["evidence_record_ids"] == ["evidence-1"]
    assert result.candidates[0].details["source_call_id"] == "call-1"
    # Output mutation cannot poison a later assembly from authoritative records.
    result.candidates[0].details["synonyms"].clear()
    repeated = store.assemble(CompactValidatorDecision.model_validate(decision(ref)))
    assert repeated.candidates[0].details["synonyms"] == ["flox"]


@pytest.mark.parametrize("mutation, message", [
    (lambda d: d.update(request_id="second"), "foreign"),
    (lambda d: d["candidates"].append(deepcopy(d["candidates"][0])), "Duplicate"),
    (lambda d: d["candidates"][0].update(evidence_record_ids=["another-paper"]), "Evidence"),
    (lambda d: d["candidates"][0].update(disposition="excluded"), "selected candidate"),
    (lambda d: d["slots"]["identifier"].update(field="invented"), "does not supply"),
    (lambda d: d["slots"]["identifier"].update(field="provider"), "declared source"),
    (lambda d: d["slots"].update(identifier={"kind":"scientific", "value":"EX:999", "explanation":"guessed"}), "authoritative"),
    (lambda d: d.update(unresolved_questions=["Which supplier?"]), "unresolved questions"),
])
def test_rejects_foreign_contradictory_or_invented_decisions(mutation, message):
    store, ref = workspace()
    payload = decision(ref)
    mutation(payload)
    with pytest.raises(ValueError, match=message):
        store.assemble(CompactValidatorDecision.model_validate(payload))


def test_cross_run_refs_and_untyped_scientific_values_are_rejected():
    store, ref = workspace()
    other_store, _ = workspace()
    with pytest.raises(ValueError, match="foreign"):
        other_store.assemble(CompactValidatorDecision.model_validate(decision(ref)))
    payload = decision(ref)
    payload["slots"]["design_matches"]["value"] = "yes"
    with pytest.raises(ValidationError):
        store.assemble(CompactValidatorDecision.model_validate(payload))


def test_no_candidates_remains_unresolved_without_invented_records():
    store, _ = workspace()
    result = store.assemble(CompactValidatorDecision(
        request_id="first", status="unresolved", explanation="No candidate fits the source.",
        unresolved_questions=["Supplier relationship unknown."],
    ))
    assert isinstance(result, DomainValidatorResultBase)
    assert result.candidates == []
    assert result.resolved_values == {}
    assert result.missing_expected_fields == ["identifier", "design_matches"]
    assert result.lookup_attempts[0].result_count == 26


def _composite_store(assemble_domain):
    store = ValidatorDecisionWorkspace([DecisionContract(request(), assemble_domain=assemble_domain)])
    reference = store.record_lookup("first", call_id="call-1", attempt=ValidatorLookupAttempt(
        provider="fixture", method="search", query={"mention": "conditional allele"},
        result_count=1, outcome="success",
    ), records=[CanonicalValidatorRecord(
        candidate=ValidatorCandidate(value="EX:101", label="Conditional allele"),
        values={"identifier": "EX:101"},
    )])[0]
    payload = decision(reference)
    payload["slots"].pop("design_matches")
    return store, payload


def test_a_composite_result_reports_the_missing_fields_of_the_values_it_decided():
    """ALL-1283: with field_resolutions the domain assembly's missing fields stand; an expected
    field of a value it did not decide (not written) is not missing."""

    def decide_identifier_only(payload, decision, workspace):
        return {
            "field_resolutions": {"identifier": {
                "status": "resolved", "lookup_outcome": "matched",
                "resolved_values": {"identifier": "EX:101"},
            }},
            "missing_expected_fields": [],
        }

    store, payload = _composite_store(decide_identifier_only)
    result = store.assemble(CompactValidatorDecision.model_validate(payload))

    assert result.status == "resolved"
    assert result.missing_expected_fields == []
    assert set(result.field_resolutions) == {"identifier"}

    # Without per-value decisions every expected field must still come back.
    plain_store, plain_payload = _composite_store(lambda payload, decision, workspace: {})
    with pytest.raises(ValueError, match="missing fields"):
        plain_store.assemble(CompactValidatorDecision.model_validate(plain_payload))
