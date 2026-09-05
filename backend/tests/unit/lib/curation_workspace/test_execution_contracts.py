from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.lib.agent_studio.profile_conformance import (
    ProfileConformanceError,
    ProfileIdentityError,
)
from src.lib.curation_workspace.execution_contracts import (
    require_extraction_conformance,
    resolve_manual_candidate_receipt,
    require_candidate_conformance,
)
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.schemas.generic_extraction_profile import GenericProfileContract


@pytest.fixture
def extraction():
    contract = GenericProfileContract.model_validate(
        {
            "name": "Records",
            "semantic_class": "record",
            "fields": [
                {"key": "count", "required": True, "value_schema": {"kind": "integer"}}
            ],
        }
    )
    pin = {
        "profile_id": uuid4(),
        "profile_revision_id": uuid4(),
        "revision": 1,
        "fingerprint": contract.fingerprint(),
    }
    receipt = AgentExecutionReceipt(
        agent_id=uuid4(),
        agent_key="ca_fixture",
        agent_revision_id=uuid4(),
        revision=1,
        fingerprint="sha256:" + "a" * 64,
        output_contract={
            "output_state": "structured_extraction",
            "output_mode": "profile_bound_generic",
            "generic_profile_ref": pin,
        },
    )
    pin_json = receipt.output_contract.generic_profile_ref.model_dump(mode="json")
    db = Mock()
    db.get.return_value = SimpleNamespace(
        profile_id=pin["profile_id"],
        revision=1,
        fingerprint=contract.fingerprint(),
        contract=contract.model_dump(mode="json"),
    )
    payload = {
        "curatable_objects": [
            {
                "object_type": "generic_object",
                "pending_ref_id": "record-1",
                "payload": {"semantic_class": "record", "attributes": {"count": 1}},
                "metadata": {
                    "generic_profile_ref": pin_json,
                    "generic_extraction": {"class_key": "generic:generic_object"},
                },
            }
        ],
        "metadata": {
            "provenance": {
                "produced_by": receipt.agent_key,
                "generic_profile_ref": pin_json,
                "execution_receipt": receipt.model_dump(mode="json"),
            }
        },
    }
    return db, receipt, payload


@pytest.mark.parametrize("existing_row", [False, True])
def test_identical_payload_under_different_profile_cannot_reuse_result(extraction, existing_row):
    from src.lib.curation_workspace import extraction_results as results
    from src.schemas.curation_workspace import CurationExtractionPersistenceRequest, CurationExtractionSourceKind

    db, receipt, payload = extraction
    original = deepcopy(payload)
    require_extraction_conformance(db, receipt, payload, agent_key=receipt.agent_key)
    next_receipt = receipt.model_copy(deep=True)
    next_receipt.agent_revision_id = uuid4()
    next_receipt.revision = 2
    next_receipt.fingerprint = "sha256:" + "b" * 64
    next_pin = next_receipt.output_contract.generic_profile_ref
    assert next_pin is not None
    next_pin.profile_revision_id = uuid4()
    next_pin.revision = 2
    old_row = db.get.return_value
    new_row = SimpleNamespace(**vars(old_row))
    new_row.revision = 2
    db.get.side_effect = lambda _model, key: new_row if key == next_pin.profile_revision_id else old_row
    first = CurationExtractionPersistenceRequest(
        document_id=str(uuid4()), adapter_key="generic", agent_key=receipt.agent_key,
        source_kind=CurationExtractionSourceKind.FLOW, origin_session_id="profile-idempotency", flow_run_id="profile-run",
        user_id="fixture-user", payload_json=payload, execution_receipt=receipt,
        idempotency_key="profile-idempotency:1",
        payload_hash=results.canonical_extraction_payload_hash(payload),
    )
    second = first.model_copy(update={"execution_receipt": next_receipt})
    record = results._build_extraction_result_record(first)
    db.execute.return_value.scalars.return_value.all.return_value = [record]
    # Same-call collisions fail at receipt comparison. An existing-row retry
    # fails even earlier: identical JSON still names the old pinned profile.
    error = ProfileIdentityError if existing_row else results.ExtractionResultPayloadMismatchError
    with pytest.raises(error):
        results.persist_idempotent_extraction_results([second] if existing_row else [first, second], db=db)
    db.add.assert_not_called()
    db.flush.assert_not_called()
    db.commit.assert_not_called()
    assert payload == original
    assert record.execution_receipt == receipt.model_dump(mode="json")


@pytest.fixture(params=[False, True], ids=["manual", "envelope"])
def review_candidate(extraction, request):
    from src.lib.curation_workspace.models import DomainEnvelopeModel
    from src.schemas.curation_workspace import CurationDraftField
    db, receipt, payload = extraction
    candidate = SimpleNamespace(
        id=uuid4(), session_id=uuid4(), agent_revision_id=receipt.agent_revision_id,
        execution_receipt=receipt.model_dump(mode="json"), envelope_id=None, object_id=None,
        normalized_payload={"object_type": "generic_object", "class_key": "generic:generic_object",
                            "semantic_class": "record", "attributes": {"count": 1}},
        draft=SimpleNamespace(fields=[CurationDraftField(
            field_key="attributes.count", label="Count", value=1,
        ).model_dump(mode="json")]),
    )
    if request.param:
        candidate.envelope_id = "env-review"
        candidate.object_id = "record-1"
        profile_row = db.get.return_value
        row = SimpleNamespace(
            session_id=candidate.session_id, execution_receipt=candidate.execution_receipt,
            envelope_json={"envelope_id": "env-review", "domain_pack_id": "generic",
                           "extracted_objects": payload["curatable_objects"],
                           "metadata": {"extraction_metadata": payload["metadata"]}},
        )
        db.get.side_effect = lambda model, key: row if model is DomainEnvelopeModel else profile_row
    return db, candidate


def test_candidate_conformance_checks_source_and_partial_draft_without_mutation(review_candidate):
    db, candidate = review_candidate
    original = deepcopy(candidate.draft.fields)
    require_candidate_conformance(db, candidate)
    assert candidate.draft.fields == original
    candidate.draft.fields[0]["value"] = "1"
    with pytest.raises(ProfileConformanceError):
        require_candidate_conformance(db, candidate)
    db.add.assert_not_called()


def test_candidate_conformance_rejects_wrong_normalized_revision(review_candidate):
    db, candidate = review_candidate
    candidate.agent_revision_id = uuid4()
    with pytest.raises(ProfileIdentityError, match="revision does not match"):
        require_candidate_conformance(db, candidate)


def test_revalidation_checks_profile_before_cached_result_or_validator(review_candidate):
    from src.lib.curation_workspace.session_validation_service import _compute_candidate_validation
    from datetime import datetime, timezone
    db, candidate = review_candidate
    candidate.draft.fields[0]["value"] = "1"
    with pytest.raises(ProfileConformanceError):
        _compute_candidate_validation(db, candidate, force=False, validated_at=datetime.now(timezone.utc))
    db.add.assert_not_called()
    db.flush.assert_not_called()


def test_export_payload_checks_profile_before_constructing_payload(review_candidate):
    from src.lib.curation_workspace.session_submission_service import _export_submission_payload_context
    db, candidate = review_candidate
    candidate.draft.fields[0]["value"] = "1"
    with pytest.raises(ProfileConformanceError):
        _export_submission_payload_context(db=db, session_row=SimpleNamespace(),
                                           ready_candidates=[candidate], session_validation=None)
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_valid_draft_cannot_hide_invalid_authoritative_source(review_candidate):
    from src.lib.curation_workspace.models import DomainEnvelopeModel
    db, candidate = review_candidate
    if candidate.envelope_id is None:
        candidate.normalized_payload["attributes"]["count"] = "1"
    else:
        row = db.get(DomainEnvelopeModel, candidate.envelope_id)
        row.envelope_json["extracted_objects"][0]["payload"]["attributes"]["count"] = "1"
    with pytest.raises(ProfileConformanceError):
        require_candidate_conformance(db, candidate)
    assert candidate.draft.fields[0]["value"] == 1


def test_session_validation_prechecks_all_candidates_before_first_write(review_candidate, monkeypatch):
    from src.lib.curation_workspace import session_validation_service as validation
    from src.schemas.curation_workspace import CurationSessionValidationRequest
    db, valid_candidate = review_candidate
    invalid_candidate = deepcopy(valid_candidate)
    invalid_candidate.id = uuid4()
    invalid_candidate.draft.fields[0]["value"] = "1"
    session = SimpleNamespace(id=valid_candidate.session_id, candidates=[valid_candidate, invalid_candidate])
    monkeypatch.setattr(validation, "_load_session_for_validation", lambda *args, **kwargs: session)
    apply_validation = Mock()
    monkeypatch.setattr(validation, "_apply_candidate_validation", apply_validation)
    request = CurationSessionValidationRequest(session_id=str(session.id))
    with pytest.raises(ProfileConformanceError):
        validation.validate_session(db, session.id, request)
    apply_validation.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.parametrize("canonical", [False, True])
def test_closed_profile_applies_before_and_after_envelope_conversion(
    extraction, canonical
):
    db, receipt, payload = extraction
    objects = payload["curatable_objects"]
    if canonical:
        payload = {
            "envelope_id": "env-1",
            "domain_pack_id": "generic",
            "extracted_objects": objects,
            "metadata": {"extraction_metadata": payload["metadata"]},
        }
    original = deepcopy(payload)
    require_extraction_conformance(db, receipt, payload, agent_key=receipt.agent_key)
    assert payload == original
    objects[0]["payload"]["attributes"]["count"] = "1"
    with pytest.raises(ProfileConformanceError) as exc:
        require_extraction_conformance(
            db, receipt, payload, agent_key=receipt.agent_key
        )
    assert exc.value.issues[0]["reason"] == "wrong_type"
    assert objects[0]["payload"]["attributes"]["count"] == "1"


def test_new_custom_extraction_cannot_omit_receipt():
    with pytest.raises(ProfileIdentityError, match="exact execution receipt"):
        require_extraction_conformance(Mock(), None, {}, agent_key="ca_fixture")
    require_extraction_conformance(Mock(), None, {}, agent_key="system_extractor")


def test_manual_candidate_selects_exact_session_source_not_current_head(extraction):
    _, receipt, _ = extraction
    other = receipt.model_copy(update={"agent_revision_id": uuid4(), "revision": 2})
    sources = [SimpleNamespace(agent_revision_id=item.agent_revision_id,
                               execution_receipt=item.model_dump(mode="json"))
               for item in (receipt, other)]
    session = SimpleNamespace(execution_revisions=sources)
    with pytest.raises(ProfileIdentityError, match="Select the saved"):
        resolve_manual_candidate_receipt(session, None)
    assert resolve_manual_candidate_receipt(session, receipt.agent_revision_id) == receipt
    assert resolve_manual_candidate_receipt(session, other.agent_revision_id) == other
    with pytest.raises(ProfileIdentityError, match="not a saved source"):
        resolve_manual_candidate_receipt(session, uuid4())
    session.execution_revisions = sources[:1]
    assert resolve_manual_candidate_receipt(session, None) == receipt
    session.execution_revisions = []
    assert resolve_manual_candidate_receipt(session, None) is None
    with pytest.raises(ProfileIdentityError, match="not a saved source"):
        resolve_manual_candidate_receipt(session, receipt.agent_revision_id)


@pytest.mark.parametrize("value", ["1", True, None, 1.5])
def test_manual_profile_fields_reject_wrong_types_without_coercion(extraction, value):
    from src.lib.curation_workspace.execution_contracts import profiled_draft_payload
    from src.lib.curation_workspace.session_types import PreparedDraftFieldInput
    db, receipt, _ = extraction
    fields = [PreparedDraftFieldInput(field_key="attributes.count", label="Count", value=value)]
    with pytest.raises(ProfileConformanceError):
        profiled_draft_payload(db, receipt, fields)
    assert fields[0].value == value
    db.add.assert_not_called()


def test_manual_profile_fields_build_canonical_record(extraction):
    from src.lib.curation_workspace.execution_contracts import profiled_draft_payload
    from src.lib.curation_workspace.session_types import PreparedDraftFieldInput
    db, receipt, _ = extraction
    fields = [PreparedDraftFieldInput(field_key="attributes.count", label="Count", value=1)]
    assert profiled_draft_payload(db, receipt, fields) == {
        "class_key": "generic:generic_object", "object_type": "generic_object",
        "semantic_class": "record", "attributes": {"count": 1},
    }


@pytest.mark.parametrize("path,value", [("attributes.extra", 2), ("semantic_class", "other"), ("extra_bag", {})])
def test_manual_profile_fields_cannot_add_unknown_data_or_change_identity(extraction, path, value):
    from src.lib.curation_workspace.execution_contracts import profiled_draft_payload
    from src.lib.curation_workspace.session_types import PreparedDraftFieldInput
    db, receipt, _ = extraction
    fields = [PreparedDraftFieldInput(field_key="attributes.count", label="Count", value=1),
              PreparedDraftFieldInput(field_key=path, label="Other", value=value)]
    with pytest.raises(ProfileConformanceError):
        profiled_draft_payload(db, receipt, fields)


def test_manual_profile_fields_cannot_overwrite_overlapping_values(extraction):
    from src.lib.curation_workspace.execution_contracts import profiled_draft_payload
    from src.lib.curation_workspace.session_types import PreparedDraftFieldInput
    db, receipt, _ = extraction
    fields = [PreparedDraftFieldInput(field_key="attributes", label="Data", value={"count": "bad"}),
              PreparedDraftFieldInput(field_key="attributes.count", label="Count", value=1)]
    with pytest.raises(ProfileIdentityError, match="overlap"):
        profiled_draft_payload(db, receipt, fields)


def test_manual_creation_checks_profile_before_any_insert(extraction, monkeypatch):
    from src.lib.curation_workspace import session_mutation_service as mutations
    from src.schemas.curation_workspace import CurationCandidateSource, CurationDraftField
    db, receipt, _ = extraction
    session_id = uuid4()
    source = SimpleNamespace(agent_revision_id=receipt.agent_revision_id,
                             execution_receipt=receipt.model_dump(mode="json"))
    session = SimpleNamespace(id=session_id, adapter_key="generic", execution_revisions=[source])
    monkeypatch.setattr(mutations, "_load_sessions_by_ids", lambda *args, **kwargs: [session])
    request = SimpleNamespace(
        session_id=session_id, agent_revision_id=None, source=CurationCandidateSource.MANUAL,
        adapter_key="generic", display_label="Record",
        draft=SimpleNamespace(adapter_key="generic", fields=[CurationDraftField(
            field_key="attributes.count", label="Count", value="1",
        )]),
    )
    with pytest.raises(ProfileConformanceError):
        mutations.create_manual_candidate(db, session_id, request, actor_claims={})
    db.add.assert_not_called()
    db.flush.assert_not_called()


def test_manual_draft_edit_checks_profile_before_mutating_saved_values(extraction, monkeypatch):
    from src.lib.curation_workspace import session_mutation_service as mutations
    from src.schemas.curation_workspace import (
        CurationCandidateSource, CurationDraftField, CurationCandidateDraftUpdateRequest,
    )
    db, receipt, _ = extraction
    session_id, candidate_id, draft_id = uuid4(), uuid4(), uuid4()
    fields = [CurationDraftField(field_key="attributes.count", label="Count", value=1,
                                seed_value=1).model_dump(mode="json")]
    draft = SimpleNamespace(id=draft_id, fields=fields, version=1, notes=None)
    candidate = SimpleNamespace(draft=draft, source=CurationCandidateSource.MANUAL,
                               execution_receipt=receipt.model_dump(mode="json"))
    monkeypatch.setattr(mutations, "_load_candidate_for_write", lambda *args, **kwargs: candidate)
    request = CurationCandidateDraftUpdateRequest(
        session_id=str(session_id), candidate_id=str(candidate_id), draft_id=str(draft_id),
        expected_version=1, field_changes=[{"field_key": "attributes.count", "value": "1"}],
    )
    before = deepcopy(fields)
    with pytest.raises(ProfileConformanceError):
        mutations.update_candidate_draft(db, session_id, candidate_id, request, actor_claims={})
    assert draft.fields == before
    assert draft.version == 1
    db.add.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.parametrize("seed", [1, "invalid"])
def test_profiled_manual_reset_restores_canonical_payload_or_rejects_atomically(extraction, seed):
    from datetime import datetime, timezone
    from src.lib.curation_workspace.session_mutation_service import _reset_candidate_state
    from src.schemas.curation_workspace import CurationCandidateSource, CurationDraftField
    db, receipt, _ = extraction
    fields = [CurationDraftField(field_key="attributes.count", label="Count", value=2,
                                seed_value=seed, dirty=True).model_dump(mode="json")]
    original_payload = {"class_key": "generic:generic_object", "object_type": "generic_object",
                        "semantic_class": "record", "attributes": {"count": 2}}
    draft = SimpleNamespace(fields=fields, notes="Edited note", version=2)
    candidate = SimpleNamespace(draft=draft, source=CurationCandidateSource.MANUAL,
                                evidence_anchors=[], normalized_payload=deepcopy(original_payload),
                                execution_receipt=receipt.model_dump(mode="json"))
    if seed == "invalid":
        with pytest.raises(ProfileConformanceError):
            _reset_candidate_state(candidate, db, occurred_at=datetime.now(timezone.utc))
        assert draft.fields == fields
        assert draft.version == 2
        assert draft.notes == "Edited note"
        assert candidate.normalized_payload == original_payload
        db.delete.assert_not_called()
    else:
        changed, removed, notes_reset = _reset_candidate_state(candidate, db, occurred_at=datetime.now(timezone.utc))
        assert changed == ["attributes.count"]
        assert removed == [] and notes_reset
        assert draft.fields[0]["value"] == 1
        assert candidate.normalized_payload["attributes"]["count"] == 1
        assert draft.version == 3
    assert candidate.execution_receipt == receipt.model_dump(mode="json")


def test_profiled_envelope_draft_materialization_preserves_json_kind():
    from src.lib.curation_workspace.session_mutation_service import _draft_field_materialized_value
    from src.schemas.curation_workspace import CurationDraftField
    field = CurationDraftField(field_key="attributes.count", label="Count", value="1", field_type="integer")
    assert _draft_field_materialized_value(field, profile_bound=True) == "1"
    assert _draft_field_materialized_value(field) == 1


@pytest.mark.parametrize("existing", [False, True])
def test_checkpoint_validates_profile_before_any_row_mutation(extraction, existing):
    from src.lib.domain_envelopes.persistence import (
        DomainEnvelopeCheckpointRequest,
        write_domain_envelope_checkpoint,
    )
    from src.schemas.domain_envelope import DomainEnvelope

    db, receipt, payload = extraction
    payload["curatable_objects"][0]["payload"]["attributes"]["count"] = "invalid"
    row = (
        SimpleNamespace(revision=3, execution_receipt=receipt.model_dump(mode="json"))
        if existing
        else None
    )
    db.scalars.return_value.first.return_value = row
    envelope = DomainEnvelope(
        envelope_id="env-1",
        domain_pack_id="generic",
        extracted_objects=payload["curatable_objects"],
        metadata={
            "execution_receipt": receipt.model_dump(mode="json"),
            "extraction_metadata": payload["metadata"],
        },
    )
    request = DomainEnvelopeCheckpointRequest(
        project_key="fixture",
        envelope=envelope,
        expected_revision=3 if existing else 0,
        execution_receipt=None if existing else receipt,
    )
    with pytest.raises(ProfileConformanceError):
        write_domain_envelope_checkpoint(db, request)
    db.add.assert_not_called()
    db.flush.assert_not_called()
    if existing:
        assert row.revision == 3


def test_inline_persistence_checks_profile_before_loading_existing_result(extraction):
    from src.lib.curation_workspace.extraction_results import (
        persist_inline_validated_extraction_result,
    )

    db, receipt, payload = extraction
    payload["curatable_objects"][0]["payload"]["attributes"]["count"] = True
    canonical = {
        "envelope_id": "env-1",
        "domain_pack_id": "generic",
        "extracted_objects": payload["curatable_objects"],
        "metadata": {"extraction_metadata": payload["metadata"]},
    }
    with pytest.raises(ProfileConformanceError):
        persist_inline_validated_extraction_result(
            payload_json=canonical,
            document_id=str(uuid4()),
            agent_key=receipt.agent_key,
            adapter_key="generic",
            tool_name="ask_custom_specialist",
            source_kind="chat",
            execution_receipt=receipt,
            db=db,
        )
    db.execute.assert_not_called()
    db.add.assert_not_called()


def test_envelope_conversion_uses_authoritative_result_receipt(extraction):
    from datetime import datetime, timezone
    from src.lib.curation_workspace.domain_envelope_normalization import (
        domain_envelope_from_extraction_result,
    )
    from src.schemas.curation_workspace import CurationExtractionResultRecord

    _, receipt, payload = extraction
    canonical = {
        "envelope_id": "env-1",
        "domain_pack_id": "generic",
        "extracted_objects": payload["curatable_objects"],
        "metadata": {
            "execution_receipt": {"forged": True},
            "extraction_metadata": payload["metadata"],
        },
    }
    result = CurationExtractionResultRecord(
        extraction_result_id=str(uuid4()),
        document_id=str(uuid4()),
        agent_key=receipt.agent_key,
        adapter_key="generic",
        source_kind="flow",
        execution_receipt=receipt,
        payload_json=canonical,
        created_at=datetime.now(timezone.utc),
    )
    envelope = domain_envelope_from_extraction_result(result)
    assert envelope.metadata["execution_receipt"] == receipt.model_dump(mode="json")
    assert (
        envelope.extracted_objects[0].payload
        == payload["curatable_objects"][0]["payload"]
    )


@pytest.mark.parametrize(
    "change", ["missing", "revision", "fingerprint", "producer", "provenance"]
)
def test_wrong_or_unavailable_pins_fail_closed(extraction, change):
    db, receipt, payload = extraction
    if change == "missing":
        db.get.return_value = None
    elif change == "revision":
        db.get.return_value.revision = 2
    elif change == "fingerprint":
        db.get.return_value.fingerprint = "sha256:" + "b" * 64
    elif change == "provenance":
        payload["metadata"]["provenance"]["execution_receipt"] = None
    with pytest.raises(ProfileIdentityError):
        require_extraction_conformance(
            db,
            receipt,
            payload,
            agent_key="ca_other" if change == "producer" else receipt.agent_key,
        )


@pytest.mark.parametrize("method", ["single", "bulk", "idempotent"])
def test_persistence_checks_all_profiled_payloads_before_writes(extraction, method):
    from src.lib.curation_workspace import extraction_results as results
    from src.schemas.curation_workspace import CurationExtractionPersistenceRequest

    db, receipt, payload = extraction
    request = CurationExtractionPersistenceRequest(
        document_id=str(uuid4()),
        agent_key=receipt.agent_key,
        adapter_key="generic",
        source_kind="flow",
        execution_receipt=receipt,
        payload_json=payload,
        idempotency_key="key",
        payload_hash=results.canonical_extraction_payload_hash(payload),
    )
    invalid = request.model_copy(deep=True)
    invalid.idempotency_key = "other"
    invalid.payload_json["curatable_objects"][0]["payload"]["attributes"]["extra"] = (
        True
    )
    invalid.payload_hash = results.canonical_extraction_payload_hash(
        invalid.payload_json
    )
    with pytest.raises(ProfileConformanceError):
        if method == "single":
            results.persist_extraction_result(invalid, db=db)
        elif method == "bulk":
            results.persist_extraction_results([request, invalid], db=db)
        else:
            results.persist_idempotent_extraction_results([request, invalid], db=db)
    db.add.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.parametrize('schema', [{'kind': 'string'}, {'kind': 'object', 'fields': [
    {'key': 'number', 'required': True, 'value_schema': {'kind': 'string'}}
]}])
@pytest.mark.parametrize('source_state,dirty,accepted', [
    ('absent', False, True), ('absent', True, False), ('null', False, False),
    ('present', False, False),
])
def test_projected_optional_placeholder_preserves_absence_only(extraction, schema, source_state, dirty, accepted):
    from src.lib.curation_workspace.execution_contracts import profiled_draft_payload
    from src.schemas.curation_workspace import CurationDraftField
    db, receipt, _ = extraction
    contract = GenericProfileContract.model_validate({
        'name': 'Stocks', 'semantic_class': 'record', 'fields': [
            {'key': 'count', 'required': True, 'value_schema': {'kind': 'integer'}},
            {'key': 'supplier', 'required': False, 'nullable': False, 'value_schema': schema},
        ],
    })
    receipt.output_contract.generic_profile_ref.fingerprint = contract.fingerprint()
    db.get.return_value.fingerprint = contract.fingerprint()
    db.get.return_value.contract = contract.model_dump(mode='json')
    base = {'object_type': 'generic_object', 'class_key': 'generic:generic_object',
            'semantic_class': 'record', 'attributes': {'count': 1}}
    if source_state != 'absent':
        base['attributes']['supplier'] = None if source_state == 'null' else (
            'Provider' if schema['kind'] == 'string' else {'number': 'A:1'})
    fields = [CurationDraftField(field_key='attributes.count', label='Count', value=1),
              CurationDraftField(field_key='attributes.supplier', label='Supplier',
                                 value=None, seed_value=None, dirty=dirty)]
    before = deepcopy(base)
    if accepted:
        assert profiled_draft_payload(db, receipt, fields, base_payload=base) == base
    else:
        with pytest.raises(ProfileConformanceError):
            profiled_draft_payload(db, receipt, fields, base_payload=base)
    assert base == before
    # Without an authoritative source, supplied null remains an explicit value.
    with pytest.raises(ProfileConformanceError):
        profiled_draft_payload(db, receipt, fields)
