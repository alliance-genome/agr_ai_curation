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
    CurationCandidate, CurationDraft, DomainEnvelopeModel,
)
from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext
from src.models.sql.agent import Agent
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.schemas.agent_execution_revision import AgentOutputContract
from src.schemas.curation_workspace import CurationCandidateSource, CurationDraftField
from tests.unit.lib.domain_packs.test_profile_validation import example as example
from .test_domain_envelope_persistence import (
    migrated_database as migrated_database, _create_review_session_for_action_log,
    RETAINED_DOMAIN_OWNER,
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
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda key: pack)
    from auth_runtime.base import AuthPrincipal
    monkeypatch.setattr("src.lib.curation_workspace.review_authorization.get_current_subject_resolver",
        lambda: lambda subject: AuthPrincipal(subject=subject, provider="fixture", groups=[]))
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
            review.created_by_id = RETAINED_DOMAIN_OWNER
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
def test_explicit_manual_review_persists_receipt_and_plan_without_paid_calls(manual_profile_record, monkeypatch, available):
    from src.lib.curation_workspace import review_operations as operations
    from src.lib.validation_review import validator_adapter
    from src.models.sql.validation_review import WorkspaceReviewOperation
    from src.models.sql.validation_history import ValidationFact
    db, candidate, receipt = manual_profile_record
    if not available:
        monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kw: [])
    monkeypatch.setattr(validator_adapter, "binding_contract", lambda match: {"binding_id": match.binding.binding_id})
    monkeypatch.setattr(validator_adapter, "_default_package_scoped_validator_runner", lambda: pytest.fail("Preparation must not call a model"))
    # Structural refresh never creates a source or queues a review by itself.
    validation._apply_candidate_validation(db, candidate, force=True,
        validated_at=datetime.now(timezone.utc), runtime_context=_history_context(candidate))
    assert candidate.envelope_id is None
    db.commit()
    saved = _submit(db, candidate)
    row = db.get(DomainEnvelopeModel, candidate.envelope_id)
    assert row.execution_receipt == candidate.execution_receipt == receipt.model_dump(mode="json")
    assert row.agent_revision_id == receipt.agent_revision_id
    assert row.envelope_json["metadata"]["source"] == "manual"
    assert row.envelope_json["extracted_objects"][0]["payload"]["attributes"] == {"paper_name": "A"}
    operation = db.get(WorkspaceReviewOperation, saved["operation_id"])
    operation.state, operation.lease_token = "preparing", "manual-test"
    db.commit()
    operations.prepare(saved["operation_id"], "manual-test")
    db.expire_all()
    operation = db.get(WorkspaceReviewOperation, saved["operation_id"])
    assert operation.state == "running"
    artifact_id = operation.artifacts[0]["artifact_id"]
    facts = list(db.scalars(select(ValidationFact).where(ValidationFact.artifact_id == artifact_id)))
    assert any(f.kind == "review_chat_plan" for f in facts)
    from src.lib.validation_history import selections, store
    manifest = selections.read_manifest(
        store.HistoryScope(_history_context(candidate).user_id, str(candidate.session.document_id), str(candidate.session_id)),
        artifact_id, episode_id=operation.artifacts[0]["episode_id"])
    unavailable = [b for b in manifest["blockers"] if b["code"] == "required_validator_unavailable"]
    assert bool(unavailable) == (not available)
    assert not any(f.kind == "attempt" for f in facts)
    assert candidate.draft.fields[0]["value"] == {"paper_name": "A"}
    operations.cancel(saved["operation_id"], owner_id=_history_context(candidate).user_id)


def _submit(db, candidate, *, request_id=None):
    from src.lib.curation_workspace import review_operations as operations
    return operations.submit(db, owner_id=_history_context(candidate).user_id,
        session_id=str(candidate.session_id), request_id=request_id or str(uuid4()),
        candidate_ids=[str(candidate.id)], guidance="Recheck the supplied mention", groups=[])


def _history_context(candidate):
    return ValidatorRuntimeContext(document_id=str(candidate.session.document_id),
        user_id=RETAINED_DOMAIN_OWNER, authenticated_groups=())


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
    db.commit()  # Autosave completes before an explicit revalidation request.
    validation._ensure_manual_profile_envelope(db, candidate, runtime_context=_history_context(candidate))
    row = db.get(DomainEnvelopeModel, candidate.envelope_id)
    second = row.envelope_json["metadata"]["validation_history"]
    assert first["artifact_id"] != second["artifact_id"]
    scope = store.HistoryScope(_history_context(candidate).user_id, str(candidate.session.document_id), str(candidate.session_id))
    assert store.read_baseline(scope, first["artifact_id"])["typed"]["extracted_objects"][0]["payload"]["attributes"]["paper_name"] == "A"
    assert store.read_baseline(scope, second["artifact_id"])["typed"]["extracted_objects"][0]["payload"]["attributes"]["paper_name"] == "Edited after rollback"


def test_failed_manual_submit_keeps_hidden_source_and_exact_retry_adopts_it(manual_profile_record, monkeypatch):
    from src.lib.agent_studio.domain_envelope_tools import get_domain_envelope_state, list_domain_envelopes
    from src.lib.curation_workspace.benchmark_snapshots import CurationBenchmarkSnapshotError, create_benchmark_snapshot
    db, candidate, _ = manual_profile_record
    candidate_id, session_id = candidate.id, candidate.session_id
    prepared = []
    original_lock = validation._lock_unlinked_manual_candidate
    def fail(db, candidate_id):
        prepared.extend(db.scalars(select(DomainEnvelopeModel.envelope_id).where(DomainEnvelopeModel.session_id == session_id)))
        raise RuntimeError("synthetic linkage failure")
    monkeypatch.setattr(validation, "_lock_unlinked_manual_candidate", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        _submit(db, candidate)
    db.rollback()
    candidate = db.get(CurationCandidate, candidate_id)
    assert candidate.envelope_id is None
    source = db.get(DomainEnvelopeModel, prepared[0])
    assert source.revision == 1
    assert source.envelope_json["extracted_objects"][0]["payload"]["attributes"] == {"paper_name": "A"}
    owner = _history_context(candidate).user_id
    assert prepared[0] not in str(list_domain_envelopes(session_factory=SessionLocal, user_auth_sub=owner, session_id=str(session_id)))
    assert "was not found" in str(get_domain_envelope_state(session_factory=SessionLocal, user_auth_sub=owner, envelope_id=prepared[0]))
    with pytest.raises(CurationBenchmarkSnapshotError, match="Envelope was not found"):
        create_benchmark_snapshot(db, session_id=session_id, envelope_id=prepared[0], expected_revision=1, current_user_id=owner)
    monkeypatch.setattr(validation, "_lock_unlinked_manual_candidate", original_lock)
    saved = _submit(db, candidate)
    assert candidate.envelope_id == prepared[0]
    assert candidate.envelope_revision == 1
    db.commit()
    assert saved["state"] == "queued"
    assert prepared[0] in str(list_domain_envelopes(session_factory=SessionLocal, user_auth_sub=owner, session_id=str(session_id)))


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

    monkeypatch.setattr(manual_profile_preparation, "prepare_manual_source", concurrent_edit)
    with pytest.raises(HTTPException) as conflict:
        _submit(db, candidate)
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


@pytest.mark.asyncio
async def test_manual_profile_worker_selects_saved_answer_and_refreshes_draft(manual_profile_record, monkeypatch):
    from unittest.mock import AsyncMock
    from agents import Agent
    from src.lib.curation_workspace import review_operations as operations
    from src.lib.validation_history import store
    from src.lib.validation_review import validator_adapter, reviewer, decisions
    from src.models.sql.validation_history import ValidationFact
    from src.models.sql.validation_review import WorkspaceReviewOperation
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    db, candidate, _ = manual_profile_record
    owner = _history_context(candidate).user_id
    monkeypatch.setattr("src.lib.redis_client.check_cancel_signal", AsyncMock(return_value=False))
    monkeypatch.setattr(validator_adapter, "binding_contract", lambda match: {"binding_id": match.binding.binding_id})
    requests = []
    def answer(request, **kwargs):
        requests.append(request)
        return {**_result_payload(request, resolved_values={"identifier": "EX:1"}), "resolved_objects": []}
    monkeypatch.setattr(validator_adapter, "_default_package_scoped_validator_runner", lambda: answer)
    monkeypatch.setattr(validator_adapter, "_default_package_scoped_validator_batch_runner", lambda: lambda *a, **k: pytest.fail("Unexpected batch"))
    def supervisor(**kwargs):
        from src.lib.observability.cost_context import attach_agent_cost_identity
        agent = Agent(name="Supervisor", instructions="Review the supplied answer.")
        attach_agent_cost_identity(agent, {"agent_id": "supervisor", "agent_name": "Supervisor", "agent_role": "supervisor"})
        return agent
    monkeypatch.setattr(reviewer, "create_direct_supervisor", supervisor)
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    saved = _submit(db, candidate)
    operation = db.get(WorkspaceReviewOperation, saved["operation_id"])
    operation.state, operation.lease_token = "preparing", "manual-worker"
    db.commit()
    async def select_answer(context, agent, **kwargs):
        with SessionLocal() as inspect:
            row = inspect.get(WorkspaceReviewOperation, saved["operation_id"])
            item = row.artifacts[0]
            attempts = list(inspect.scalars(select(ValidationFact).where(
                ValidationFact.artifact_id == item["artifact_id"], ValidationFact.kind == "attempt")))
            entries = [{"target_id": a.target_id, "decision_unit_id": a.target_id,
                "expected_version": store._current(inspect, item["artifact_id"], a.target_id)["version"],
                "logical_attempt_id": a.id, "action": "accept", "reason": "Fake supervisor accepts supplied resolution"}
                for a in attempts]
        assert len(entries) == 1
        decisions.record_decisions(context, checkpoint_request=operations.checkpoint(owner, item, ()),
            operation_id="select-manual-answer", entries=entries)
        return {"output": "Accepted", "review": operations.admission.status(context.history_scope, context.episode_id)}
    monkeypatch.setattr(reviewer, "run", select_answer)
    await operations._execute(saved["operation_id"], "manual-worker", "manual-fixture-trace")
    db.expire_all()
    operation = db.get(WorkspaceReviewOperation, saved["operation_id"])
    assert operation.state == "complete", operation.outcome
    assert len(requests) == 1
    assert requests[0].selected_inputs == {"mention": "A"}
    assert candidate.draft.fields[0]["value"] == {"paper_name": "A", "resolved_id": "EX:1"}
    envelope = db.get(DomainEnvelopeModel, candidate.envelope_id)
    assert envelope.envelope_json["extracted_objects"][0]["payload"]["attributes"] == candidate.draft.fields[0]["value"]
