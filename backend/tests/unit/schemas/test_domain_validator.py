"""Tests for shared domain validator result contracts."""

import pytest
from pydantic import BaseModel, ValidationError, create_model, model_validator

from src.schemas.domain_validator import (
    DomainValidatorResultBase,
    ValidationTarget,
    ValidatorAgentRef,
    ValidatorOutputProjection,
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


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("lookup_query", "https://www.ebi.ac.uk/chebi/backend/api/public/compound/17160/"),
        ("lookup_query", "64153"),
        ("lookup_query", None),
        ("candidate_score", 48.159214),
        ("candidate_score", -0.01),
    ],
)
def test_domain_validator_rejects_legacy_lookup_queries_and_scores(
    field: str,
    invalid_value: object,
):
    payload = _base_payload()
    payload["candidates"] = [
        {
            "value": "CHEBI:17160",
            "label": "17alpha-estradiol",
            "object_type": "ChemicalTerm",
            "score": invalid_value if field == "candidate_score" else 0.8,
            "matched_fields": {"name": "estradiol"},
            "details": {"source": "ebi_chebi"},
        }
    ]
    payload["lookup_attempts"] = [
        {
            "provider": "ebi_chebi",
            "method": "compound",
            "query": invalid_value if field == "lookup_query" else {"value": "64153"},
            "result_count": 1,
            "outcome": "success",
        },
    ]

    with pytest.raises(ValidationError):
        DomainValidatorResultBase.model_validate(payload)


def test_domain_validator_requires_lookup_query_metadata():
    payload = _base_payload()
    payload["lookup_attempts"] = [
        {
            "provider": "ebi_chebi",
            "method": "compound",
            "result_count": 1,
            "outcome": "success",
        }
    ]

    with pytest.raises(ValidationError):
        DomainValidatorResultBase.model_validate(payload)


@pytest.mark.parametrize("score", [None, 0.0, 0.75, 1.0])
def test_domain_validator_accepts_null_or_confidence_range_scores(score: float | None):
    payload = _base_payload()
    payload["candidates"] = [
        {
            "value": "CHEBI:17160",
            "score": score,
        }
    ]

    assert DomainValidatorResultBase.model_validate(payload).candidates[0].score == score


def test_domain_validator_accepts_blocked_lookup_attempt_outcome():
    payload = _base_payload("unresolved")
    payload["resolved_values"] = {}
    payload["missing_expected_fields"] = ["entity_id"]
    payload["lookup_attempts"] = [
        {
            "provider": "agr_curation_query",
            "method": "search_alleles",
            "query": {"allele_symbol": "N fa-g"},
            "result_count": 0,
            "outcome": "blocked",
        }
    ]

    result = DomainValidatorResultBase.model_validate(payload)

    assert result.lookup_attempts[0].outcome == "blocked"


def test_domain_validator_rejects_missing_status():
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

    with pytest.raises(ValidationError):
        DomainValidatorResultBase.model_validate(payload)


def test_domain_validator_schema_detection_requires_inheritance():
    class InheritedResult(DomainValidatorResultBase):
        pass

    IncompatibleInheritedResult = create_model(
        "IncompatibleInheritedResult",
        __base__=DomainValidatorResultBase,
        status=(str | None, None),
    )

    class EmbeddedResult(BaseModel):
        result: DomainValidatorResultBase

    class BehaviorWeakeningInheritedResult(DomainValidatorResultBase):
        @model_validator(mode="after")
        def _clear_status(self):
            object.__setattr__(self, "status", None)
            return self

    class SummaryOnly(BaseModel):
        summary: str

    assert is_domain_validator_result_schema(InheritedResult)
    assert not is_domain_validator_result_schema(IncompatibleInheritedResult)
    assert not is_domain_validator_result_schema(BehaviorWeakeningInheritedResult)
    assert not is_domain_validator_result_schema(EmbeddedResult)
    assert not is_domain_validator_result_schema(SummaryOnly)


def test_support_models_are_strict():
    with pytest.raises(ValidationError):
        ValidatorAgentRef.model_validate(
            {"package_id": "demo.validators", "agent_id": "entity_validation", "extra": True}
        )

    with pytest.raises(ValidationError):
        ValidationTarget.model_validate({"domain_pack_id": "demo.entity", "extra": True})


def test_validator_output_projection_requires_explicit_safe_field_names():
    projection = ValidatorOutputProjection.model_validate(
        {
            "row_list_field": "projected_records",
            "identity_fields": ["record_key"],
            "label_fields": ["label"],
            "inherited_parent_fields": ["source_name"],
        }
    )

    assert projection.row_list_field == "projected_records"
    assert projection.identity_fields == ("record_key",)
    assert projection.label_fields == ("label",)
    assert projection.inherited_parent_fields == ("source_name",)

    for invalid in (
        {"row_list_field": "", "identity_fields": ["record_key"]},
        {"row_list_field": "records", "identity_fields": []},
        {"row_list_field": "records", "identity_fields": ["record.key"]},
        {
            "row_list_field": "records",
            "identity_fields": ["record_key", "record_key"],
        },
        {
            "row_list_field": "records",
            "identity_fields": ["record_key"],
            "unexpected": True,
        },
    ):
        with pytest.raises(ValidationError):
            ValidatorOutputProjection.model_validate(invalid)
