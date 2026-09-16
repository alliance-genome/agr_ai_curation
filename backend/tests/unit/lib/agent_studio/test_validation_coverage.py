"""Configuration-specific consent must never be confused with validation."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from pydantic import ValidationError
from src.lib.agent_studio.validation_coverage import (
    ValidationAcknowledgmentRequired, profile_coverage_scope, require_acknowledgments,
    flow_coverage_scopes, annotate_unvalidated_candidate,
)
from src.schemas.generic_extraction_profile import GenericProfileContract


def contract(mapped=True):
    value = {"name": "Reference identity", "semantic_class": "reference", "fields": [
        {"key": key, "display_name": label, "value_schema": {"kind": "string"}}
        for key, label in [("name", "Paper name"), ("identifier", "Database identifier"), ("notes", "Free notes")]],
        "validator_mappings": []}
    if mapped:
        value["validator_mappings"] = [{"mapping_id": "identity", "capability_ref": {
            "package_id": "provider", "package_version": "1", "domain_pack_id": "identity", "domain_pack_version": "1", "binding_id": "lookup"},
            "capability_fingerprint": "sha256:" + "a" * 64,
            "inputs": {"name": {"source": "field", "field_path": "attributes.name"}},
            "outputs": {"id": "attributes.identifier"}, "policy": {"unresolved": "requires_curator_review", "blocks_readiness": False}}]
    return GenericProfileContract.model_validate(value)


def history_db():
    db = Mock()
    db.scalars.return_value = [contract().model_dump(mode="json")]
    db.get.return_value = None
    return db


def test_last_mapping_removal_requires_exact_actor_and_configuration_acknowledgment():
    db = history_db()
    scope = profile_coverage_scope(db, contract(False), profile_id=uuid4())
    assert [field.path for field in scope.unvalidated_fields] == ['attributes.identifier', 'attributes.name']
    assert 'Free notes' not in str(scope)
    with pytest.raises(ValidationAcknowledgmentRequired):
        require_acknowledgments(db, 8, [scope])
    accepted = {(8, scope.fingerprint()): object()}
    db.get.side_effect = lambda _model, key: accepted.get(key)
    require_acknowledgments(db, 8, [scope])
    require_acknowledgments(db, 8, [scope])  # unchanged repeated run
    with pytest.raises(ValidationAcknowledgmentRequired):
        require_acknowledgments(db, 9, [scope])
    changed = contract(False).model_copy(update={"description": "Changed extraction guidance"})
    changed_scope = profile_coverage_scope(db, changed, profile_id=uuid4())
    with pytest.raises(ValidationAcknowledgmentRequired):
        require_acknowledgments(db, 8, [changed_scope])


def test_partial_coverage_and_restored_mapping():
    db = history_db()
    partial = contract()
    partial.validator_mappings[0].outputs = {}
    scope = profile_coverage_scope(db, partial, profile_id=uuid4())
    assert [field.label for field in scope.unvalidated_fields] == ['Database identifier']
    assert profile_coverage_scope(db, contract(), profile_id=uuid4()) is None
    disabled = profile_coverage_scope(db, contract(), profile_id=uuid4(), disabled_mapping_ids=frozenset({'identity'}))
    assert disabled.disabled_checks == ['identity']


def test_arbitrary_notes_do_not_acquire_a_guessed_database_requirement():
    db = Mock()
    db.scalars.return_value = [contract(False).model_dump(mode="json")]
    assert profile_coverage_scope(db, contract(False), profile_id=uuid4()) is None
    db.scalars.assert_called_once()


def test_unavailable_configured_mapping_is_not_an_acknowledgment(monkeypatch):
    from src.lib.agent_studio import generic_profile_service, profile_mapping_service
    from src.lib.agent_studio.profile_mapping_service import ProfileMappingError
    monkeypatch.setattr(generic_profile_service, 'get_profile_revision', lambda *a, **k: SimpleNamespace(contract=contract().model_dump(mode='json')))
    def unavailable(*a, **k):
        raise ProfileMappingError([{'path':'validator_mappings[0]', 'message':'Exact binding is unavailable'}])
    monkeypatch.setattr(profile_mapping_service, 'validate_profile_mappings', unavailable)
    receipt = SimpleNamespace(output_contract=SimpleNamespace(generic_profile_ref=SimpleNamespace(profile_id=uuid4(), revision=1)))
    definition = SimpleNamespace(nodes=[SimpleNamespace(data=SimpleNamespace(execution_receipt=receipt))])
    with pytest.raises(ProfileMappingError):
        flow_coverage_scopes(Mock(), definition, user_id=8, active_group_ids=[])


def test_api_requires_explicit_true_not_dismissal_or_default():
    from src.api.validation_acknowledgments import AcknowledgmentRequest
    scope = profile_coverage_scope(history_db(), contract(False), profile_id=uuid4())
    for payload in ({'scopes':[scope.model_dump()]}, {'scopes':[scope.model_dump()], 'acknowledge_extraction_only':False}):
        with pytest.raises(ValidationError):AcknowledgmentRequest.model_validate(payload)


def test_runtime_and_formatter_metadata_remain_unverified():
    from src.lib.curation_workspace.extraction_results import ExtractionEnvelopeCandidate
    from src.schemas.domain_envelope import DomainEnvelope
    from src.lib.flows.output_projection import build_flow_output_artifact_bundle
    scope = profile_coverage_scope(history_db(), contract(False), profile_id=uuid4()).model_dump(mode='json')
    payload = {'envelope_id':'env', 'domain_pack_id':'generic', 'extracted_objects':[
        {'object_type':'generic_object','pending_ref_id':'row1','payload':{'semantic_class':'reference','attributes':{'name':'Paper name'}}}]}
    candidate = ExtractionEnvelopeCandidate(agent_key='generic_extractor', payload_json=deepcopy(payload), adapter_key='generic')
    annotate_unvalidated_candidate(candidate, [scope])
    envelope = DomainEnvelope.model_validate(candidate.payload_json)
    assert envelope.validation_findings[0].code == 'not_database_validated'
    assert envelope.validation_findings[0].status.value == 'open'
    assert candidate.payload_json['extracted_objects'][0]['payload'] == payload['extracted_objects'][0]['payload']
    bundle = build_flow_output_artifact_bundle(completed_steps=[{'step':1,'node_id':'one','agent_id':'generic_extractor','candidate':candidate}],flow_name='Test',output_format='json')
    assert bundle.artifacts[0].database_validation_coverage == [scope]
    assert any('Not database-validated' in warning for warning in bundle.artifacts[0].warnings)
