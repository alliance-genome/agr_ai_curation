"""Tests for shared domain validator result contracts."""

import pytest
from pydantic import BaseModel, ValidationError

from src.schemas.domain_validator import (
    DomainValidatorResultBase,
    ValidationTarget,
    ValidatorAgentRef,
    is_domain_validator_result_schema,
)


def _base_payload(status: str = "resolved") -> dict:
    return {
        "status": status,
        "request_id": "request-1",
        "validator_binding_id": "entity.lookup",
        "validator_agent": {
            "package_id": "demo.validators",
            "agent_id": "entity_validation",
        },
        "target": {
            "domain_pack_id": "demo.entity",
            "object_type": "entity_evidence",
            "field_path": "entity_id",
        },
        "resolved_values": {"entity_id": "DEMO:Entity0001"},
        "resolved_objects": [],
        "missing_expected_fields": [],
        "candidates": [],
        "lookup_attempts": [],
        "curator_message": "Entity reference resolved.",
        "explanation": "The lookup returned an exact primary ID match.",
    }


def test_domain_validator_result_accepts_resolved_and_unresolved_only():
    assert DomainValidatorResultBase.model_validate(_base_payload("resolved")).status == "resolved"
    assert DomainValidatorResultBase.model_validate(_base_payload("unresolved")).status == "unresolved"

    with pytest.raises(ValidationError):
        DomainValidatorResultBase.model_validate(_base_payload("under_development"))


def test_domain_validator_normalizes_lookup_query_strings_and_raw_scores():
    payload = _base_payload()
    payload["candidates"] = [
        {
            "value": "CHEBI:17160",
            "label": "17alpha-estradiol",
            "object_type": "ChemicalTerm",
            "score": 48.159214,
            "matched_fields": {"name": "estradiol"},
            "details": {"source": "ebi_chebi"},
        }
    ]
    payload["lookup_attempts"] = [
        {
            "provider": "ebi_chebi",
            "method": "compound",
            "query": "https://www.ebi.ac.uk/chebi/backend/api/public/compound/17160/",
            "result_count": 1,
            "outcome": "success",
        },
        {
            "provider": "ebi_chebi",
            "method": "compound",
            "query": "64153",
            "result_count": 1,
            "outcome": "success",
        },
    ]

    result = DomainValidatorResultBase.model_validate(payload)

    assert result.candidates[0].score is None
    assert result.candidates[0].details["raw_score"] == 48.159214
    assert result.lookup_attempts[0].query == {
        "url": "https://www.ebi.ac.uk/chebi/backend/api/public/compound/17160/"
    }
    assert result.lookup_attempts[1].query == {"value": "64153"}


def test_domain_validator_infers_missing_status_from_resolved_lookup_output():
    payload = _base_payload()
    del payload["status"]
    payload["resolved_objects"] = [{"curie": "DOID:898", "name": "ADPKD"}]
    payload["lookup_attempts"] = [
        {
            "provider": "agr_curation_query",
            "method": "search_ontology_terms",
            "query": {"term": "autosomal dominant polycystic kidney disease"},
            "result_count": 1,
            "outcome": "success",
        }
    ]

    assert DomainValidatorResultBase.model_validate(payload).status == "resolved"


def test_domain_validator_schema_detection_allows_inheritance_and_embedding():
    class InheritedResult(DomainValidatorResultBase):
        pass

    class EmbeddedResult(BaseModel):
        result: DomainValidatorResultBase

    class SummaryOnly(BaseModel):
        summary: str

    assert is_domain_validator_result_schema(InheritedResult)
    assert is_domain_validator_result_schema(EmbeddedResult)
    assert not is_domain_validator_result_schema(SummaryOnly)


def test_support_models_are_strict():
    with pytest.raises(ValidationError):
        ValidatorAgentRef.model_validate(
            {"package_id": "demo.validators", "agent_id": "entity_validation", "extra": True}
        )

    with pytest.raises(ValidationError):
        ValidationTarget.model_validate({"domain_pack_id": "demo.entity", "extra": True})
