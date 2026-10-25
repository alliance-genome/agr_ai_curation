"""Replay preserves registered typed fields and rejects contract drift."""

import pytest
from pydantic import Field, ValidationError, create_model

from src.lib.validation_history import result_schema as contracts
from src.lib.validation_history.store import HistoryConflict
from src.schemas.domain_validator import DomainValidatorResultBase
from src.lib.config.schema_discovery import resolve_output_schema
from tests.unit.lib.domain_packs.test_validator_dispatch import _validation_request, _result_payload


def gene_result():
    request = _validation_request()
    schema = resolve_output_schema("GeneResultEnvelope")
    result = schema.model_validate({
        **_result_payload(request),
        "gene_candidates": [{"gene_id": "FB:FBgn0259685", "symbol": "crb"}],
    })
    return request, result


def test_registered_gene_round_trip_preserves_concrete_nested_fields():
    request, result = gene_result()
    restored = contracts.restore_result_schema(
        result.model_dump(mode="json"), contracts.capture_result_schema(result), request=request,
    )
    assert type(restored) is type(result)
    assert restored == result
    assert restored.gene_candidates[0].gene_id == "FB:FBgn0259685"


def test_explicit_platform_base_contract_is_not_an_unknown_schema_fallback():
    request = _validation_request()
    result = DomainValidatorResultBase.model_validate(_result_payload(request))
    descriptor = contracts.capture_result_schema(result)
    assert descriptor["key"] == "DomainValidatorResultBase"
    assert contracts.restore_result_schema(result.model_dump(), descriptor, request=request) == result


def test_other_registered_specialized_schema_round_trips(monkeypatch):
    schema = create_model("AnotherResultEnvelope", __base__=DomainValidatorResultBase,
                          details=(list[str], Field(default_factory=list)))
    monkeypatch.setattr(contracts, "resolve_output_schema", lambda key: schema if key == schema.__name__ else None)
    request = _validation_request()
    result = schema.model_validate({**_result_payload(request), "details": ["retained detail"]})
    assert contracts.restore_result_schema(result.model_dump(), contracts.capture_result_schema(result),
                                           request=request) == result


@pytest.mark.parametrize("descriptor", [None, {}, {"key": 42, "sha256": "x"},
    {"key": "GeneResultEnvelope", "sha256": "x", "module": "untrusted"}])
def test_missing_or_malformed_descriptor_is_explicit_conflict(descriptor):
    request, result = gene_result()
    with pytest.raises(HistoryConflict, match="schema"):
        contracts.restore_result_schema(result.model_dump(), descriptor, request=request)


def test_unknown_schema_does_not_downcast():
    request, result = gene_result()
    descriptor = {**contracts.capture_result_schema(result), "key": "UnknownResultEnvelope"}
    with pytest.raises(HistoryConflict, match="unavailable"):
        contracts.restore_result_schema(result.model_dump(), descriptor, request=request)


def test_unregistered_same_name_class_cannot_capture_registered_identity():
    shadow = create_model("GeneResultEnvelope", __base__=DomainValidatorResultBase)
    result = shadow.model_validate(_result_payload(_validation_request()))
    with pytest.raises(HistoryConflict, match="registered schema"):
        contracts.capture_result_schema(result)


def test_same_key_schema_drift_is_rejected(monkeypatch):
    request, result = gene_result()
    descriptor = contracts.capture_result_schema(result)
    changed = create_model("GeneResultEnvelope", __base__=type(result),
                           new_field=(str | None, None))
    monkeypatch.setattr(contracts, "resolve_output_schema", lambda key: changed)
    with pytest.raises(HistoryConflict, match="schema changed"):
        contracts.restore_result_schema(result.model_dump(), descriptor, request=request)


def test_concrete_schema_still_rejects_undeclared_extra_fields():
    request, result = gene_result()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        contracts.restore_result_schema({**result.model_dump(), "invented": True},
                                        contracts.capture_result_schema(result), request=request)
