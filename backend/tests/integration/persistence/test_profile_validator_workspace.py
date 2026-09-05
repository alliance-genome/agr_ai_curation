"""Real migrated PostgreSQL manual-profile validation and checkpoint write-back.

Only validator responses are deterministic fixtures. Profile/revision creation,
receipt triggers, envelope checkpoints, drafts, findings and snapshots are real.
Each test rolls back its data; no application data is refreshed or deleted.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.lib.agent_studio import generic_profile_service
from src.lib.agent_studio.execution_revision_service import append_execution_revision, current_execution_receipt
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.lib.curation_workspace import session_validation_service as validation
from src.lib.curation_workspace.models import (
    CurationCandidate, CurationDraft, CurationSessionAgentRevision, DomainEnvelopeModel,
)
from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
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
def manual_profile_record(migrated_database, example, monkeypatch):
    raw, capability, pack = example
    raw["validator_mappings"][0]["policy"]["blocks_readiness"] = True
    monkeypatch.setattr("src.lib.agent_studio.profile_mapping_service.capability_catalog", lambda **kw: [capability])
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kw: [capability])
    monkeypatch.setattr(validation, "resolve_curation_domain_pack_by_id", lambda key: pack)
    monkeypatch.setattr(validation, "resolve_curation_domain_envelope_validator_by_id", lambda key: None)
    with SessionLocal() as db:
        try:
            review = _create_review_session_for_action_log(db)
            owner = db.get(PDFDocument, review.document_id).user_id
            _, revision = generic_profile_service.create_profile(db, owner, raw)
            agent = Agent(id=uuid4(), agent_key="ca_" + uuid4().hex, user_id=owner,
                name="Profile validation fixture", instructions="Fixture", model_id="test-model",
                model_temperature=0.0, visibility="private", tool_ids=[], group_rules_enabled=False)
            db.add(agent)
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
            yield db, candidate, receipt
            db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        finally:
            db.rollback()


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
        db, candidate, force=True, validated_at=datetime.now(timezone.utc))
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
        assert attributes == {"paper_name": "A", "resolved_id": "EX:1"}
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
    assert bool(ready_context.blockers) is not available
    if available:
        exported = submission._domain_envelope_candidate_payload(candidate, ready_context)
        assert exported["payload"]["attributes"] == attributes


def test_profile_validation_refreshes_sibling_drafts_in_same_envelope(manual_profile_record, monkeypatch):
    from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest, write_domain_envelope_checkpoint
    db, candidate, receipt = manual_profile_record
    validation._ensure_manual_profile_envelope(db, candidate)
    row = db.get(DomainEnvelopeModel, candidate.envelope_id)
    envelope = DomainEnvelope.model_validate(row.envelope_json)
    second = envelope.extracted_objects[0].model_copy(deep=True)
    second.object_id = "second"
    second.payload["attributes"] = {"paper_name": "B"}
    envelope.extracted_objects.append(second)
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
    db.flush()

    def dispatch(source, pack, **kwargs):
        fixtures = results(source, kwargs["profile_context"], [{"identifier": "EX:1"}, {"identifier": "EX:2"}])
        by_request = {item.request.request_id: item.result for item in fixtures}
        return dispatch_active_validator_bindings(source, pack, **kwargs,
            runner=lambda request, **kw: by_request[request.request_id].model_dump(mode="json"))

    monkeypatch.setattr(validation, "dispatch_active_validator_bindings", dispatch)
    validation._apply_candidate_validation(db, candidate, force=True, validated_at=datetime.now(timezone.utc))
    candidate_id, sibling_id = candidate.id, sibling.id
    db.flush()
    db.expire_all()
    candidate, sibling = db.get(CurationCandidate, candidate_id), db.get(CurationCandidate, sibling_id)
    assert candidate.envelope_revision == sibling.envelope_revision == 3
    assert candidate.draft.fields[0]["value"]["resolved_id"] == "EX:1"
    assert sibling.draft.fields[0]["value"]["resolved_id"] == "EX:2"
    assert sibling.validation_snapshots == []
