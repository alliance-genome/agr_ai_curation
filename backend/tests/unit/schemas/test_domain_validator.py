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
