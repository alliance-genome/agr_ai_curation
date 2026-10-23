"""Real migrated PostgreSQL manual-profile validation and checkpoint write-back.

Only validator responses are deterministic fixtures. Profile/revision creation,
receipt triggers, envelope checkpoints, drafts, findings and snapshots are real.
Fixtures use unique identities in the disposable test database. Each test removes
its own live projections and retires its immutable audit history during teardown.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text

from src.lib.agent_studio import generic_profile_service
from src.lib.agent_studio.execution_revision_service import append_execution_revision, current_execution_receipt
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.lib.curation_workspace import session_validation_service as validation
from src.lib.curation_workspace.models import (
    CurationCandidate, CurationDraft, CurationSessionAgentRevision, DomainEnvelopeModel,
)
from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings, ValidatorRuntimeContext
from src.models.sql.agent import Agent
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.schemas.agent_execution_revision import AgentOutputContract
from src.schemas.curation_workspace import CurationCandidateSource, CurationDraftField
from src.schemas.domain_envelope import DomainEnvelope
from tests.unit.lib.domain_packs.test_profile_validation import example as example
from tests.unit.lib.domain_packs.test_profile_materialization import results
from .test_domain_envelope_persistence import (
    migrated_database as migrated_database, _create_review_session_for_action_log,
)


@pytest.fixture
def manual_profile_record(migrated_database, example, monkeypatch, request):
    raw, capability, pack = example
    raw["validator_mappings"][0]["policy"]["blocks_readiness"] = True
    if getattr(request, "param", None) == "unmapped":
        raw["validator_mappings"] = []
    monkeypatch.setattr("src.lib.agent_studio.profile_mapping_service.capability_catalog", lambda **kw: [capability])
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kw: [capability])
    monkeypatch.setattr(validation, "resolve_curation_domain_pack_by_id", lambda key: pack)
    monkeypatch.setattr(validation, "resolve_curation_domain_envelope_validator_by_id", lambda key: None)
    # Independent history/preparation transactions need committed profile/session
    # fixtures, just as the real API receives. Bound lock waits expose self-blocks.
    engine = SessionLocal.kw["bind"]
    def bounded_locks(connection):
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
    event.listen(engine, "begin", bounded_locks)
    with SessionLocal() as db:
        document_id = None
        try:
            review = _create_review_session_for_action_log(db)
            document_id = review.document_id
            review.created_by_id = "test_pdf_owner_domain_envelope"
            review.adapter_key = "generic"
            owner = db.get(PDFDocument, review.document_id).user_id
            db.commit()  # Source document/owner must be visible to independent capture transactions.
            _, revision = generic_profile_service.create_profile(db, owner, raw)
            agent = Agent(id=uuid4(), agent_key="ca_" + uuid4().hex, user_id=owner,
                name="Profile validation fixture " + uuid4().hex, instructions="Fixture", model_id="test-model",
                model_temperature=0.0, visibility="private", tool_ids=[], group_rules_enabled=False)
            db.add(agent)
            agent.tool_ids = ["stage_generic_object", "finalize_generic_extraction"]
            db.flush()
            contract = AgentOutputContract.model_validate({"output_state": "structured_extraction",
                "output_mode": "profile_bound_generic", "generic_profile_ref": {
                    "profile_id": revision.profile_id, "profile_revision_id": revision.id,
                    "revision": revision.revision, "fingerprint": revision.fingerprint}})
            append_execution_revision(db, agent, capture_execution_snapshot(db, agent, contract),
                user_id=owner, expected_revision_id=None)
            receipt = current_execution_receipt(db, agent.agent_key, owner, active_group_ids=[])
            candidate = CurationCandidate(session=review, source=CurationCandidateSource.MANUAL,
                adapter_key=review.adapter_key, agent_revision_id=receipt.agent_revision_id,
                execution_receipt=receipt.model_dump(mode="json"), normalized_payload={
                    "object_type": "generic_object", "class_key": "generic:generic_object",
                    "semantic_class": "record", "attributes": {"paper_name": "A"}})
            db.add(candidate)
            db.flush()
            candidate.draft = CurationDraft(adapter_key=review.adapter_key, version=1, fields=[
                CurationDraftField(field_key="attributes", label="Record", field_type="object",
                    value={"paper_name": "A"}, seed_value={"paper_name": "A"}).model_dump(mode="json")])
            db.flush()
            db.commit()
            yield db, candidate, receipt
            db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        finally:
            db.rollback()
            try:
                if document_id is not None:
                    from src.lib.document_cleanup import cleanup_document_curation_dependencies
                    with SessionLocal.begin() as cleanup:
                        cleanup_document_curation_dependencies(cleanup, document_id)
                        document = cleanup.get(PDFDocument, document_id)
                        if document is not None:
                            cleanup.delete(document)
            finally:
                event.remove(engine, "begin", bounded_locks)


@pytest.mark.parametrize("available", [True, False])
def test_manual_profile_validation_persists_receipt_findings_and_current_draft(manual_profile_record, monkeypatch, available):
    db, candidate, receipt = manual_profile_record
    if not available:
        monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kw: [])

    def dispatch(envelope, pack, **kwargs):
        context = kwargs["profile_context"]
        fixtures = results(envelope, context, [{"identifier": "EX:1"}]) if available else []
        return dispatch_active_validator_bindings(envelope, pack, **kwargs,
            runner=lambda *args, **kw: fixtures[0].result.model_dump(mode="json"))

    monkeypatch.setattr(validation, "dispatch_active_validator_bindings", dispatch)
    snapshot, changed = validation._apply_candidate_validation(
        db, candidate, force=True, validated_at=datetime.now(timezone.utc), runtime_context=_history_context(candidate))
    assert changed
    candidate_id = candidate.id
    db.flush()
    db.expire_all()
    candidate = db.get(CurationCandidate, candidate_id)
    row = db.get(DomainEnvelopeModel, candidate.envelope_id)
    envelope = DomainEnvelope.model_validate(row.envelope_json)
    assert row.execution_receipt == candidate.execution_receipt == receipt.model_dump(mode="json")
    assert row.agent_revision_id == candidate.agent_revision_id == receipt.agent_revision_id
    membership = db.scalar(select(CurationSessionAgentRevision).where(
        CurationSessionAgentRevision.session_id == candidate.session_id,
        CurationSessionAgentRevision.agent_revision_id == receipt.agent_revision_id))
    assert membership.execution_receipt == row.execution_receipt
    assert row.source_extraction_result_id is None and envelope.metadata["source"] == "manual"
    assert snapshot.envelope_revision == candidate.envelope_revision == row.revision == 2
    assert candidate.normalized_payload == {}
    attributes = envelope.extracted_objects[0].payload["attributes"]
    assert candidate.draft.fields[0]["value"] == attributes
    if available:
        assert attributes == {"paper_name": "A"}
        assert envelope.validator_results
        assert envelope.validation_findings[0].details["machine_review"] == "pending"
    else:
        assert attributes == {"paper_name": "A"}
        finding, = envelope.validation_findings
        assert finding.code == "generic_profile.validator_unavailable"
        assert finding.severity.value == "blocker"
    assert candidate.validation_snapshots[-1].envelope_revision == row.revision
    from src.lib.curation_workspace import session_submission_service as submission
    ready_context = submission._build_domain_envelope_object_context(
        db=db, candidate=candidate, envelope_row=row, envelope=envelope,
        expected_revision=row.revision, projection_refs=())
    assert not any(blocker.field_path == "label" for blocker in ready_context.blockers)
    readiness = submission._build_domain_envelope_submission_context(
        db=db, candidates={str(candidate.id): candidate},
        target_candidate_ids=[str(candidate.id)],
        user_id=_history_context(candidate).user_id,
    )
    assert readiness.object_contexts[str(candidate.id)].blockers


def test_profile_validation_refreshes_sibling_drafts_in_same_envelope(manual_profile_record, monkeypatch):
    from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest, write_domain_envelope_checkpoint
    db, candidate, receipt = manual_profile_record
    validation._ensure_manual_profile_envelope(db, candidate, runtime_context=_history_context(candidate))
    row = db.get(DomainEnvelopeModel, candidate.envelope_id)
    envelope = DomainEnvelope.model_validate(row.envelope_json)
    second = envelope.extracted_objects[0].model_copy(deep=True)
    second.object_id = "second"
    second.metadata.pop("validation_history")
    second.payload["attributes"] = {"paper_name": "B"}
    envelope.extracted_objects.append(second)
    from src.lib.validation_history.runtime import bind_artifact, capture_curator_objects
    envelope = capture_curator_objects(bind_artifact(envelope, _history_context(candidate)), envelope,
                                      actor_id="test_pdf_owner_domain_envelope")
    checkpoint = write_domain_envelope_checkpoint(db, DomainEnvelopeCheckpointRequest(
        project_key=row.project_key, envelope=envelope, expected_revision=row.revision,
        document_id=row.document_id, session_id=row.session_id, flow_run_id=row.flow_run_id))
    candidate.envelope_revision = checkpoint.revision
    sibling = CurationCandidate(session=candidate.session, source=CurationCandidateSource.MANUAL,
        order=1, adapter_key=candidate.adapter_key, agent_revision_id=receipt.agent_revision_id,
        execution_receipt=receipt.model_dump(mode="json"), envelope_id=row.envelope_id,
        envelope_revision=checkpoint.revision, object_id=second.object_id, normalized_payload={})
    sibling.draft = CurationDraft(adapter_key=candidate.adapter_key, version=1, fields=[
        CurationDraftField(field_key="attributes", label="Record", field_type="object",
            value={"paper_name": "B"}, seed_value={"paper_name": "B"}).model_dump(mode="json")])
    db.add(sibling)
    db.commit()  # Source preparation ends before independent validator execution.

    def dispatch(source, pack, **kwargs):
        fixtures = results(source, kwargs["profile_context"], [{"identifier": "EX:1"}, {"identifier": "EX:2"}])
        by_request = {item.request.request_id: item.result for item in fixtures}
        return dispatch_active_validator_bindings(source, pack, **kwargs,
            runner=lambda request, **kw: by_request[request.request_id].model_dump(mode="json"))

    monkeypatch.setattr(validation, "dispatch_active_validator_bindings", dispatch)
    validation._apply_candidate_validation(db, candidate, force=True, validated_at=datetime.now(timezone.utc), runtime_context=_history_context(candidate))
    candidate_id, sibling_id = candidate.id, sibling.id
    db.flush()
    db.expire_all()
    candidate, sibling = db.get(CurationCandidate, candidate_id), db.get(CurationCandidate, sibling_id)
    assert candidate.envelope_revision == sibling.envelope_revision == 3
    assert candidate.draft.fields[0]["value"] == {"paper_name": "A"}
    assert sibling.draft.fields[0]["value"] == {"paper_name": "B"}
    assert sibling.validation_snapshots == []


def _history_context(candidate):
    return ValidatorRuntimeContext(document_id=str(candidate.session.document_id),
        user_id="test_pdf_owner_domain_envelope", authenticated_groups=())


def test_manual_capture_retry_after_checkpoint_rollback_and_draft_edit(manual_profile_record):
    from copy import deepcopy
    from src.lib.validation_history import store
    db, candidate, _ = manual_profile_record
    with db.begin_nested() as savepoint:
        validation._ensure_manual_profile_envelope(db, candidate, runtime_context=_history_context(candidate))
        row = db.get(DomainEnvelopeModel, candidate.envelope_id)
        first = row.envelope_json["metadata"]["validation_history"]
        savepoint.rollback()
    assert candidate.envelope_id is None
    fields = deepcopy(candidate.draft.fields)
    fields[0]["value"]["paper_name"] = "Edited after rollback"
    candidate.draft.fields = fields
    validation._ensure_manual_profile_envelope(db, candidate, runtime_context=_history_context(candidate))
    row = db.get(DomainEnvelopeModel, candidate.envelope_id)
    second = row.envelope_json["metadata"]["validation_history"]
    assert first["artifact_id"] != second["artifact_id"]
    scope = store.HistoryScope(_history_context(candidate).user_id, str(candidate.session.document_id), str(candidate.session_id))
    assert store.read_baseline(scope, first["artifact_id"])["typed"]["extracted_objects"][0]["payload"]["attributes"]["paper_name"] == "A"
    assert store.read_baseline(scope, second["artifact_id"])["typed"]["extracted_objects"][0]["payload"]["attributes"]["paper_name"] == "Edited after rollback"


@pytest.mark.parametrize("failure_phase", ["dispatch", "checkpoint"])
def test_failed_manual_validation_keeps_hidden_source_and_exact_retry_adopts_it(
    manual_profile_record, monkeypatch, failure_phase,
):
    from asyncio import CancelledError
    from src.lib.agent_studio.domain_envelope_tools import (
        get_domain_envelope_state, list_domain_envelopes,
    )
    from src.lib.curation_workspace.benchmark_snapshots import (
        CurationBenchmarkSnapshotError, create_benchmark_snapshot,
    )
    db, candidate, _ = manual_profile_record
    candidate_id, session_id = candidate.id, candidate.session_id
    prepared = []

    def fail(envelope, pack, **kwargs):
        prepared.append(envelope.envelope_id)
        fixtures = results(envelope, kwargs["profile_context"], [{"identifier": "EX:1"}])
        def runner(*args, **kw):
            if failure_phase == "dispatch":
                raise CancelledError("synthetic cancellation")
            return fixtures[0].result.model_dump(mode="json")
        return dispatch_active_validator_bindings(envelope, pack, **kwargs, runner=runner)

    monkeypatch.setattr(validation, "dispatch_active_validator_bindings", fail)
    if failure_phase == "checkpoint":
        def fail_checkpoint(*args, **kwargs):
            raise RuntimeError("synthetic checkpoint failure")
        monkeypatch.setattr(validation, "write_domain_envelope_checkpoint", fail_checkpoint)
    error = CancelledError if failure_phase == "dispatch" else RuntimeError
    with pytest.raises(error, match="synthetic"):
        validation._apply_candidate_validation(
            db, candidate, force=True, validated_at=datetime.now(timezone.utc),
            runtime_context=_history_context(candidate),
        )
    db.rollback()
    candidate = db.get(CurationCandidate, candidate_id)
    assert candidate.envelope_id is None
    assert candidate.draft.fields[0]["value"] == {"paper_name": "A"}
    source = db.get(DomainEnvelopeModel, prepared[0])
    assert source.revision == 1
    assert source.envelope_json["extracted_objects"][0]["payload"]["attributes"] == {"paper_name": "A"}

    from src.lib.validation_history import store
    from src.models.sql.validation_history import ValidationFact
    history = source.envelope_json["metadata"]["validation_history"]
    completion = db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == history["artifact_id"], ValidationFact.kind == "completion"))
    assert completion is not None  # Attempt outcome survives the outer rollback.
    scope = store.HistoryScope(_history_context(candidate).user_id,
        str(candidate.session.document_id), str(candidate.session_id))
    outcome = store.read_fact(scope, history["artifact_id"], completion.id)["content"]
    assert outcome["state"] == ("interrupted" if failure_phase == "dispatch" else "completed")
    owner = _history_context(candidate).user_id
    listing = list_domain_envelopes(
        session_factory=SessionLocal, user_auth_sub=owner, session_id=str(session_id),
    )
    assert prepared[0] not in str(listing)
    inspected = get_domain_envelope_state(
        session_factory=SessionLocal, user_auth_sub=owner, envelope_id=prepared[0],
    )
    assert "was not found" in str(inspected)
    with pytest.raises(CurationBenchmarkSnapshotError, match="Envelope was not found"):
        create_benchmark_snapshot(db, session_id=session_id, envelope_id=prepared[0],
                                  expected_revision=1, current_user_id=owner)
    validation._ensure_manual_profile_envelope(db, candidate, runtime_context=_history_context(candidate))
    assert candidate.envelope_id == prepared[0]
    assert candidate.envelope_revision == 1
    db.commit()
    listing = list_domain_envelopes(
        session_factory=SessionLocal, user_auth_sub=owner, session_id=str(session_id),
    )
    assert prepared[0] in str(listing)


def test_manual_validation_rejects_concurrently_advanced_source(manual_profile_record, monkeypatch):
    from fastapi import HTTPException
    from src.lib.domain_envelopes.persistence import (
        DomainEnvelopeCheckpointRequest, write_domain_envelope_checkpoint,
    )
    db, candidate, _ = manual_profile_record
    source_ids = []

    def dispatch(envelope, pack, **kwargs):
        fixtures = results(envelope, kwargs["profile_context"], [{"identifier": "EX:1"}])
        outcome = dispatch_active_validator_bindings(envelope, pack, **kwargs,
            runner=lambda *args, **kw: fixtures[0].result.model_dump(mode="json"))
        source_ids.append(envelope.envelope_id)
        with SessionLocal.begin() as concurrent:
            row = concurrent.get(DomainEnvelopeModel, envelope.envelope_id)
            newer = DomainEnvelope.model_validate(row.envelope_json)
            newer.metadata["concurrent_edit"] = "newer checkpoint wins"
            write_domain_envelope_checkpoint(concurrent, DomainEnvelopeCheckpointRequest(
                project_key=row.project_key, envelope=newer, expected_revision=row.revision,
                document_id=row.document_id, session_id=row.session_id,
                adapter_key=row.adapter_key, flow_run_id=row.flow_run_id,
            ))
        return outcome

    monkeypatch.setattr(validation, "dispatch_active_validator_bindings", dispatch)
    with pytest.raises(HTTPException) as failure:
        validation._apply_candidate_validation(
            db, candidate, force=True, validated_at=datetime.now(timezone.utc),
            runtime_context=_history_context(candidate),
        )
    assert failure.value.status_code == 409
    db.rollback()
    row = db.get(DomainEnvelopeModel, source_ids[0])
    assert row.revision == 2
    assert row.envelope_json["metadata"]["concurrent_edit"] == "newer checkpoint wins"
    assert candidate.envelope_id is None
    assert candidate.draft.fields[0]["value"] == {"paper_name": "A"}


@pytest.mark.parametrize("concurrent_change", ["draft", "linkage"])
def test_first_manual_validation_preserves_newer_unlinked_draft(manual_profile_record, monkeypatch, concurrent_change):
    from copy import deepcopy
    from fastapi import HTTPException
    from src.lib.curation_workspace import manual_profile_preparation
    db, candidate, _ = manual_profile_record
    candidate_id, draft_id = candidate.id, candidate.draft.id
    original_prepare = manual_profile_preparation.prepare_manual_source
    prepared_ids = []

    def concurrent_edit(**kwargs):
        prepared = original_prepare(**kwargs)
        prepared_ids.append(prepared.envelope_id)
        with SessionLocal.begin() as editor:
            if concurrent_change == "draft":
                draft = editor.get(CurationDraft, draft_id)
                fields = deepcopy(draft.fields)
                fields[0]["value"] = {"paper_name": "New curator wording"}
                draft.fields = fields
                draft.version += 1
            else:
                other = editor.get(CurationCandidate, candidate_id)
                other.envelope_id = prepared.envelope_id
                other.envelope_revision = prepared.revision
                other.object_id = f"manual:{candidate_id}"
                other.normalized_payload = {}
        return prepared

    def dispatch(envelope, pack, **kwargs):
        fixtures = results(envelope, kwargs["profile_context"], [{"identifier": "EX:1"}])
        return dispatch_active_validator_bindings(envelope, pack, **kwargs,
            runner=lambda *args, **kw: fixtures[0].result.model_dump(mode="json"))

    monkeypatch.setattr(manual_profile_preparation, "prepare_manual_source", concurrent_edit)
    monkeypatch.setattr(validation, "dispatch_active_validator_bindings", dispatch)
    with pytest.raises(HTTPException) as conflict:
        validation._apply_candidate_validation(
            db, candidate, force=True, validated_at=datetime.now(timezone.utc),
            runtime_context=_history_context(candidate),
        )
    assert conflict.value.status_code == 409
    assert ("draft changed" if concurrent_change == "draft" else "source changed") in conflict.value.detail
    db.rollback()
    candidate = db.get(CurationCandidate, candidate_id)
    if concurrent_change == "draft":
        assert candidate.envelope_id is None
        assert candidate.draft.version == 2
        assert candidate.draft.fields[0]["value"] == {"paper_name": "New curator wording"}
    else:
        assert candidate.envelope_id == prepared_ids[0]
        assert candidate.draft.version == 1
    source = db.get(DomainEnvelopeModel, prepared_ids[0])
    assert source.revision == 1
    assert source.envelope_json["extracted_objects"][0]["payload"]["attributes"] == {"paper_name": "A"}


def test_accepted_unvalidated_manual_profile_cannot_export(manual_profile_record):
    from src.lib.curation_workspace import session_submission_service as submission
    from src.lib.curation_workspace.export_adapters import DEFAULT_JSON_BUNDLE_TARGET_KEY
    from src.schemas.curation_workspace import (
        CurationCandidateStatus, CurationSubmissionPreviewRequest, SubmissionMode,
    )
    db, candidate, _ = manual_profile_record
    candidate.status = CurationCandidateStatus.ACCEPTED
    db.flush()
    response = submission.submission_preview(db, candidate.session_id,
        CurationSubmissionPreviewRequest(session_id=str(candidate.session_id),
            mode=SubmissionMode.EXPORT, target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY),
        actor_claims={"sub": _history_context(candidate).user_id})
    assert candidate.envelope_id is None
    readiness, = response.submission.readiness
    assert not readiness.ready
    assert "Explicit review and selected-value publication are required." in readiness.blocking_reasons


def test_autosave_loaded_before_source_linkage_is_refused(manual_profile_record, monkeypatch):
    from fastapi import HTTPException
    from src.lib.curation_workspace import session_mutation_service as mutation
    from src.schemas.curation_workspace import CurationCandidateDraftUpdateRequest
    db, candidate, _ = manual_profile_record
    candidate_id, session_id = candidate.id, candidate.session_id
    original_load = mutation._load_candidate_for_write

    def load_then_link(*args, **kwargs):
        stale = original_load(*args, **kwargs)
        assert stale.envelope_id is None
        with SessionLocal.begin() as validator:
            other = validator.get(CurationCandidate, candidate_id)
            validation._ensure_manual_profile_envelope(validator, other,
                runtime_context=_history_context(other))
        return stale

    monkeypatch.setattr(mutation, "_load_candidate_for_write", load_then_link)
    with pytest.raises(HTTPException) as conflict:
        mutation.update_candidate_draft(db, session_id, candidate_id,
            CurationCandidateDraftUpdateRequest(session_id=str(session_id),
                candidate_id=str(candidate_id), draft_id=str(candidate.draft.id),
                expected_version=1, field_changes=[{"field_key": "attributes", "value": {"paper_name": "Later edit"}}]),
            actor_claims={"sub": _history_context(candidate).user_id})
    assert conflict.value.status_code == 409
    assert "source changed" in conflict.value.detail
    db.rollback()
    candidate = db.get(CurationCandidate, candidate_id)
    assert candidate.envelope_id is not None
    assert candidate.draft.fields[0]["value"] == {"paper_name": "A"}
