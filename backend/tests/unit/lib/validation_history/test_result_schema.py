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
        "gene_candidates": [{"gene_id": "GENE:example-1", "symbol": "geneA"}],
    })
    return request, result


def test_registered_gene_round_trip_preserves_concrete_nested_fields():
    request, result = gene_result()
    restored = contracts.restore_result_schema(
        result.model_dump(mode="json"), contracts.capture_result_schema(result), request=request,
    )
    assert type(restored) is type(result)
    assert restored == result
    assert restored.gene_candidates[0].gene_id == "GENE:example-1"


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
    with pytest.raises(HistoryConflict, match="no longer satisfies") as raised:
        contracts.restore_result_schema({**result.model_dump(), "invented": True},
                                        contracts.capture_result_schema(result), request=request)

    assert isinstance(raised.value.__cause__, ValidationError)
    assert "extra_forbidden" in str(raised.value.__cause__)


def test_registered_validator_schema_digests_require_explicit_upgrade_review():
    """Do not refresh this snapshot blindly: changes invalidate retained recipes.

    Review how existing workspaces will recover before accepting any digest
    change, including documentation-only and Pydantic schema output changes.
    """
    import json
    from pathlib import Path
    from src.lib.config.schema_discovery import discover_agent_schemas
    from src.schemas.domain_validator import is_domain_validator_result_schema

    schemas = {"DomainValidatorResultBase": DomainValidatorResultBase,
               **discover_agent_schemas()}
    actual = {key: contracts.store.canonical(schema.model_json_schema())[0]
              for key, schema in schemas.items() if is_domain_validator_result_schema(schema)}
    expected = json.loads(Path(__file__).with_name("validator_schema_digests.json").read_text())
    assert actual == expected, "Schema contract changed: review retained-history recovery before updating snapshot"


def test_policy_replay_enforces_retained_request_context():
    from src.schemas.domain_validator import DomainValidationRequest
    from tests.unit.lib.config.test_rgd_go_evidence_policy_validation_agent import (
        _result_payload as policy_payload, _selected_inputs_for_result,
    )
    payload = policy_payload()
    request = DomainValidationRequest(
        request_id=payload["request_id"], validator_binding_id=payload["validator_binding_id"],
        validator_agent=payload["validator_agent"], target=payload["target"],
        selected_inputs=_selected_inputs_for_result(payload),
    )
    schema = resolve_output_schema("RGDGOEvidencePolicyValidationResult")
    result = schema.model_validate(payload, context={"domain_validation_request": request})
    descriptor = contracts.capture_result_schema(result)
    assert contracts.restore_result_schema(payload, descriptor, request=request) == result
    changed = request.model_copy(deep=True)
    changed.selected_inputs["go_term"]["curie"] = "GO:DIFFERENT"
    with pytest.raises(HistoryConflict, match="new authenticated validation") as raised:
        contracts.restore_result_schema(payload, descriptor, request=changed)
    assert isinstance(raised.value.__cause__, ValidationError)
    assert "proposal fields must exactly copy" in str(raised.value.__cause__)


def test_composite_condition_result_preserves_component_details():
    from tests.unit.lib.config.test_experimental_condition_validation_agent import _base_payload
    from src.schemas.domain_validator import DomainValidationRequest
    payload = _base_payload()
    request = DomainValidationRequest(
        request_id=payload["request_id"], validator_binding_id=payload["validator_binding_id"],
        validator_agent=payload["validator_agent"], target=payload["target"], selected_inputs={},
    )
    result = resolve_output_schema("ExperimentalConditionValidationResult").model_validate(payload)
    restored = contracts.restore_result_schema(payload, contracts.capture_result_schema(result), request=request)
    assert type(restored) is type(result)
    assert restored == result
    assert restored.unresolved_components == ["condition_chemical"]
