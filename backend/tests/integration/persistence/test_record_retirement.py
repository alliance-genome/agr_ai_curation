"""Retirement preserves source history and requires an exact extractor proposal."""
from copy import deepcopy

import pytest

from src.lib.validation_history import store, selections
from src.lib.validation_review import extraction_results, extraction_scheduling, work
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.record_retirement import decide_in_transaction
from tests.integration.persistence.test_chat_review_adapter import planned as planned, scope as scope, migrated as migrated
from tests.integration.persistence.test_extraction_followup_admission import request_args


def proposal(item, *, malformed=False):
    args = request_args(item)
    context = ReviewContext(item.scope, item.ep, "supervisor")
    wid = extraction_scheduling.request(context, **args)
    claim = work.claim(item.scope, wid)
    assert claim is not None
    work.mark_submitted(item.scope, wid, claim["lease_token"])
    entry = {"entry_id": args["entry_ids"][0], "outcome": "propose_retirement",
        "assessment": "This item is outside the requested scope.",
        "remaining_uncertainty": "Absence alone is not evidence of non-use.",
        "proposed_values": [], "evidence_record_ids": []}
    if malformed:
        entry["proposed_values"] = [{"field_path": "entries", "value": [],
            "evidence_record_ids": [], "collection_mappings": [{"collection_path": "entries",
                "source_collection_id": None, "items": [], "retired_item_ids": args["entry_ids"],
                "retirement_reason": "Not part of this experiment"}]}]
    retained = extraction_results.retain(item.scope, wid, claim["lease_token"],
        {"entries": [entry]}, evidence_records=[])
    work.finish(item.scope, wid, claim["lease_token"], outcome=retained)
    return context, retained["contributions"][0], args["entry_ids"][0]


def decide(db, item, context, ref, decision, *, publish=None, operation_id="disposition"):
    return decide_in_transaction(db, context, operation_id=operation_id,
        contribution_ref=ref, retirement_decision=decision,
        expected_source_sha=selections.source_state_digest(db, item.aid),
        reason="Assessed the proposed scope and uncertainty", source_envelope=item.plan.envelope,
        rebuild_inputs=lambda db, envelope: {},
        publish=publish or (lambda *args: {}))


@pytest.mark.parametrize("decision", ["approve", "decline"])
def test_explicit_decision_preserves_original_and_siblings(planned, decision):
    item = planned
    baseline = deepcopy(store.read_baseline(item.scope, item.aid))
    context, ref, target = proposal(item)
    projected = []
    with store.SessionLocal.begin() as db:
        before = store._current(db, item.aid, target)
        result = decide(db, item, context, ref, decision,
            publish=lambda db, envelope, *args: projected.append(envelope) or {})
        state = store._current(db, item.aid, target)
        assert state["active"] is (decision == "decline")
        if decision == "decline":
            assert state == before
            assert projected == []
        else:
            assert all(obj.metadata["validation_history"]["target_id"] != target
                       for obj in projected[0].extracted_objects)
            fact = store._fact(db, item.aid, result["decision_id"])
            assert fact.parent_id == ref["fact_id"]
    assert store.read_baseline(item.scope, item.aid) == baseline


def test_curator_authority_blocks_retirement_without_writes(planned):
    item = planned
    context, ref, target = proposal(item)
    store.apply_decisions(item.scope, item.aid, operation_id="curator-review",
        actor={"type": "curator", "id": item.scope.owner_id}, entries=[{
            "target_id": target, "expected_version": 0, "action": "defer", "reason": "Keep for review"}])
    with store.SessionLocal.begin() as db:
        state = store._current(db, item.aid, target)
        digest = selections.source_state_digest(db, item.aid)
        with pytest.raises(store.HistoryConflict, match="Curator authority"):
            decide(db, item, context, ref, "approve")
        assert store._current(db, item.aid, target) == state
        assert selections.source_state_digest(db, item.aid) == digest


def test_malformed_incident_is_retained_but_cannot_retire(planned):
    item = planned
    context, ref, target = proposal(item, malformed=True)
    retained = store.read_fact(item.scope, item.aid, ref["fact_id"])["content"]
    assert retained["selectable"] is False
    assert retained["structural_failure"] == "invalid_proposal_structure"
    assert retained["proposal"]["remaining_uncertainty"]
    with store.SessionLocal.begin() as db:
        with pytest.raises(store.HistoryConflict, match="structural failure"):
            decide(db, item, context, ref, "approve")
        assert store._current(db, item.aid, target)["active"] is True


@pytest.mark.asyncio
async def test_adapter_publishes_empty_projection_and_exact_replay(planned):
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    item = planned
    context, ref, target = proposal(item)
    checkpoint = checkpoint_provider(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    arguments = dict(operation_id="retire-through-adapter", contribution_ref=ref,
        retirement_decision="approve", expected_source_sha=digest, reason="Outside requested task")
    result = await item.adapter.select_source(context, checkpoint, **arguments)
    assert result["retired"] is True
    assert checkpoint().envelope.extracted_objects == []
    assert result["publication"]["ready"] is True
    assert await item.adapter.select_source(context, checkpoint, **arguments) == result
    with store.SessionLocal() as db:
        manifest = selections.manifest_in_transaction(db, item.scope, item.aid, episode_id=item.ep)
        assert manifest["units"] == []
        assert store._current(db, item.aid, target)["active"] is False


@pytest.mark.asyncio
async def test_curator_restores_same_record_pending_without_new_attempt(planned):
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    from src.models.sql.validation_history import ValidationFact
    from sqlalchemy import select
    item = planned
    context, ref, target = proposal(item)
    checkpoint = checkpoint_provider(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    await item.adapter.select_source(context, checkpoint, operation_id="retire-for-restore",
        contribution_ref=ref, retirement_decision="approve", expected_source_sha=digest, reason="Outside task")
    with store.SessionLocal() as db:
        before = len(list(db.scalars(select(ValidationFact).where(ValidationFact.artifact_id == item.aid,
            ValidationFact.kind.in_(["attempt", "extraction_followup_request"])))))
        version = store._current(db, item.aid, target)["version"]
    curator = ReviewContext(item.scope, item.ep, item.scope.owner_id, actor_type="curator")
    arguments = dict(operation_id="curator-restore", restore_target_id=target,
                     expected_version=version, reason="Include for another review")
    result = await item.adapter.select_source(curator, checkpoint, **arguments)
    assert result["publication"]["ready"] is False
    current = checkpoint().envelope
    assert current.extracted_objects[0].metadata["validation_history"]["target_id"] == target
    assert current.metadata["record_counts"] == {"active": 1, "retired": 0, "pending": 1}
    assert await item.adapter.select_source(curator, checkpoint, **arguments) == result
    with store.SessionLocal() as db:
        assert store._current(db, item.aid, target)["active"] is True
        assert store._current(db, item.aid, target)["review"] == "pending"
        assert len(list(db.scalars(select(ValidationFact).where(ValidationFact.artifact_id == item.aid,
            ValidationFact.kind.in_(["attempt", "extraction_followup_request"]))))) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("opened_before", [True, False])
async def test_workspace_history_survives_retirement_and_restore(planned, opened_before, monkeypatch):
    from datetime import datetime, timezone
    from uuid import UUID, uuid4
    from sqlalchemy import select
    from src.lib.curation_workspace.models import CurationReviewSession, CurationCandidate, DomainEnvelopeModel
    from src.lib.curation_workspace.record_retirement_service import listing, restore, detail
    from src.schemas.workspace_review import WorkspaceRecordRestoreRequest
    from src.schemas.curation_workspace import CurationCandidateSource
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    item = planned
    from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
    monkeypatch.setattr("src.lib.domain_packs.materialization._registered_materializer_for",
                        lambda _: DomainPackMetadataReviewRowMaterializer(item.pack.metadata))
    context, ref, target = proposal(item)
    base_checkpoint = checkpoint_provider(item)
    def checkpoint():
        from dataclasses import replace
        value = base_checkpoint()
        with store.SessionLocal() as db:
            row = db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id)
            return replace(value, session_id=row.session_id if row is not None else None)
    # Materialize the initial checkpoint before attaching a workspace.
    from src.lib.domain_envelopes.persistence import write_domain_envelope_checkpoint
    initial_checkpoint = checkpoint()
    with store.SessionLocal.begin() as db:
        write_domain_envelope_checkpoint(db, initial_checkpoint)
    with store.SessionLocal.begin() as db:
        session = CurationReviewSession(adapter_key="fixture", document_id=UUID(item.scope.document_id),
            created_by_id=item.scope.owner_id, prepared_at=datetime.now(timezone.utc))
        db.add(session)
        db.flush()
        sid = session.id
        row = db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id)
        if opened_before:
            row.session_id = sid
            db.add(CurationCandidate(session_id=sid, adapter_key="fixture", source=CurationCandidateSource.EXTRACTED,
                envelope_id=row.envelope_id, object_id="one", envelope_revision=row.revision))
        digest = selections.source_state_digest(db, item.aid)
    await item.adapter.select_source(context, checkpoint, operation_id="workspace-retire",
        contribution_ref=ref, retirement_decision="approve", expected_source_sha=digest, reason="Outside task")
    with store.SessionLocal.begin() as db:
        row = db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id)
        row.session_id = sid
        db.flush()
        records = listing(db, sid)
        assert len(records) == 1
        record = records[0]
        page = detail(db, row.envelope_id, owner_id=item.scope.owner_id,
                      fact_id=record["proposal"]["fact_id"], sha256=record["proposal"]["sha256"], offset=0)
        assert 'propose_retirement' in page["content"]
        with pytest.raises(store.HistoryAccessError):
            detail(db, row.envelope_id, owner_id="other-owner", fact_id=record["proposal"]["fact_id"],
                   sha256=record["proposal"]["sha256"], offset=0)
        if opened_before:
            candidate = db.scalar(select(CurationCandidate).where(CurationCandidate.session_id == sid))
            assert candidate.candidate_metadata["record_retired"]
            from fastapi import HTTPException
            from src.lib.curation_workspace.session_mutation_service import decide_candidate
            from src.schemas.curation_workspace import CurationCandidateDecisionRequest
            with pytest.raises(HTTPException) as retired_error:
                decide_candidate(db, candidate.id,
                    CurationCandidateDecisionRequest(session_id=str(sid), candidate_id=str(candidate.id), action="accept"),
                    {"sub": item.scope.owner_id})
            assert retired_error.value.status_code == 409
            assert db.get(CurationReviewSession, sid).total_candidates == 0
    result = restore(item.plan.envelope.envelope_id, owner_id=item.scope.owner_id, groups=(),
        request=WorkspaceRecordRestoreRequest(request_id=uuid4(), target_id=UUID(target),
            expected_version=record["version"], reason="Curator requests inclusion"))
    assert not result["publication"]["ready"]
    with store.SessionLocal() as db:
        assert listing(db, sid) == []
        candidate = db.scalar(select(CurationCandidate).where(CurationCandidate.session_id == sid))
        assert candidate.object_id == "one"
        assert not candidate.candidate_metadata.get("record_retired", False)
        assert db.get(CurationReviewSession, sid).total_candidates == 1


@pytest.mark.asyncio
async def test_restore_in_closed_episode_preserves_consumed_allowance(planned):
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    from src.models.sql.validation_review import ValidationReviewEpisode
    item = planned
    context, ref, target = proposal(item)
    checkpoint = checkpoint_provider(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    await item.adapter.select_source(context, checkpoint, operation_id="retire-closed",
        contribution_ref=ref, retirement_decision="approve", expected_source_sha=digest, reason="Outside task")
    with store.SessionLocal.begin() as db:
        episode = db.get(ValidationReviewEpisode, item.ep)
        episode.state = "complete"
        allowances = deepcopy(episode.allowances)
        version = store._current(db, item.aid, target)["version"]
    curator = ReviewContext(item.scope, item.ep, item.scope.owner_id, actor_type="curator")
    restored = await item.adapter.select_source(curator, checkpoint, operation_id="restore-closed",
        restore_target_id=target, expected_version=version, reason="Review again")
    assert not restored["publication"]["ready"]
    with store.SessionLocal() as db:
        assert db.get(ValidationReviewEpisode, item.ep).allowances == allowances
        assert db.get(ValidationReviewEpisode, item.ep).state == "complete"


@pytest.mark.parametrize("planned", ["three"], indirect=True)
def test_invalid_followup_sibling_does_not_discard_valid_entries(planned):
    from uuid import uuid4
    item = planned
    args = request_args(item)
    entries = {obj.metadata["validation_history"]["target_id"]: obj.model_dump(mode="json")
               for obj in item.plan.envelope.extracted_objects}
    with store.SessionLocal.begin() as db:
        op = uuid4().hex
        parent = store._append(db, item.aid, operation_id="decision:" + op, kind="decision",
            body={"actor": {"type": "agent", "id": "supervisor"}, "entries": [
                {"target_id": target, "action": "defer", "reason": "Investigate"} for target in entries]})
        store._append(db, item.aid, operation_id="episode-disposition:" + op,
            kind="episode_disposition", parent_id=item.ep, body={"selection_ids": []})
        args.update(parent_decision_id=parent.id, entry_ids=list(entries), current_entries=entries)
    context = ReviewContext(item.scope, item.ep, "supervisor")
    wid = extraction_scheduling.request(context, **args)
    claim = work.claim(item.scope, wid)
    assert claim is not None
    work.mark_submitted(item.scope, wid, claim["lease_token"])
    result = {"entries": [{"entry_id": target, "outcome": "no_new_information", "assessment": "No clarification",
        "remaining_uncertainty": "Unknown", "proposed_values": [], "evidence_record_ids": []} for target in entries]}
    result["entries"][1]["evidence_record_ids"] = ["unavailable-evidence"]
    retained = extraction_results.retain(item.scope, wid, claim["lease_token"], result, evidence_records=[])
    bodies = [store.read_fact(item.scope, item.aid, ref["fact_id"])["content"] for ref in retained["contributions"]]
    assert [body["selectable"] for body in bodies] == [True, False, True]
    assert bodies[1]["proposal"]["remaining_uncertainty"] == "Unknown"
    assert bodies[1]["proposal"]["evidence_record_ids"] == ["unavailable-evidence"]


def test_surviving_binding_missing_input_is_not_runnable_after_retirement(planned):
    from src.lib.validation_review import source_inputs, source_scheduling, admission
    from src.lib.validation_review.audit import append_fact
    item = planned
    context = ReviewContext(item.scope, item.ep, "supervisor")
    frozen = deepcopy(item.adapter.plan["initial_requests"][0])
    target = frozen["target_id"]
    with store.SessionLocal.begin() as db:
        decision = append_fact(db, item.aid, operation_id="retirement-input-test", kind="record_retirement_decision",
            body={"identity": {"decision": "approve"}})
        changed = source_inputs.update(db, item.aid, operation_id="missing-dependency", before={target: frozen},
            after={}, selection_id=decision.id)
        assert changed[0]["runnable"] is False
        assert not source_inputs.current(db, item.aid, frozen)
        decision_id = decision.id
    with pytest.raises(admission.ReviewStopped, match="required_inputs_unavailable"):
        source_scheduling.request(context, operation_id="try-missing-input", selection_id=decision_id,
            target_ids=[target], instructions="Assess surviving dependency", receipt={})


@pytest.mark.asyncio
async def test_all_retired_first_open_runs_real_workspace_pipeline(planned, monkeypatch):
    from uuid import UUID
    from src.lib.curation_workspace import pipeline
    from src.lib.curation_workspace.models import CurationExtractionResultRecord, DomainEnvelopeModel, CurationReviewSession
    from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
    from src.lib.curation_workspace.record_retirement_service import listing
    from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
    from src.schemas.curation_workspace import CurationExtractionSourceKind
    from src.schemas.curation_prep import CurationPrepAgentOutput
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    item = planned
    context, ref, _ = proposal(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    with store.SessionLocal.begin() as db:
        source_record = CurationExtractionResultRecord(document_id=UUID(item.scope.document_id), adapter_key="fixture",
            agent_key="fixture_extractor", source_kind=CurationExtractionSourceKind.CHAT,
            user_id=item.scope.owner_id, candidate_count=1, payload_json=item.plan.envelope.model_dump(mode="json"))
        db.add(source_record)
        db.flush()
        source_id = source_record.id
    from dataclasses import replace
    base_checkpoint = checkpoint_provider(item)
    def checkpoint():
        return replace(base_checkpoint(), adapter_key="fixture", source_extraction_result_id=str(source_id))
    await item.adapter.select_source(context, checkpoint, operation_id="retire-before-prep",
        contribution_ref=ref, retirement_decision="approve", expected_source_sha=digest, reason="Outside task")
    monkeypatch.setattr(pipeline, "resolve_curation_domain_pack_by_id", lambda _: item.pack)
    monkeypatch.setattr(pipeline, "resolve_curation_domain_envelope_validator_by_id", lambda _: None)
    monkeypatch.setattr("src.lib.domain_packs.materialization._registered_materializer_for",
        lambda _: DomainPackMetadataReviewRowMaterializer(item.pack.metadata))
    with store.SessionLocal.begin() as db:
        row = db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id)
        prep = CurationPrepAgentOutput.model_validate({"envelope_refs": [{
            "envelope_id": row.envelope_id, "envelope_revision": row.revision,
            "source_extraction_result_id": str(source_id),
            "domain_pack_id": item.pack.pack_id, "review_row_count": 0, "retired_record_count": 1}],
            "review_row_count": 0, "run_metadata": {"model_name": "deterministic_programmatic_mapper_v1",
                "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}})
        record = CurationExtractionResultRecord(document_id=UUID(item.scope.document_id), adapter_key="fixture",
            agent_key=CURATION_PREP_AGENT_ID, source_kind=CurationExtractionSourceKind.CHAT,
            user_id=item.scope.owner_id, candidate_count=0, payload_json=prep.model_dump(mode="json"),
            extraction_metadata={"final_run_metadata": prep.run_metadata.model_dump(mode="json")})
        db.add(record)
        db.flush()
        result = pipeline.execute_post_curation_pipeline(pipeline.PostCurationPipelineRequest(
            prep_output=prep, document_id=item.scope.document_id, source_kind=CurationExtractionSourceKind.CHAT,
            adapter_key="fixture", user_id=item.scope.owner_id, created_by_id=item.scope.owner_id,
            active_groups=(), prep_extraction_result_id=str(record.id)), db=db)
        session = db.get(CurationReviewSession, UUID(result.session_id))
        assert session.total_candidates == 0
        assert session.current_candidate_id is None
        assert len(listing(db, session.id)) == 1
        assert row.session_id == session.id


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["three"], indirect=True)
async def test_restore_during_later_scoped_round_adds_pending_coverage(planned):
    from dataclasses import replace
    from uuid import uuid4
    from src.lib.validation_review import admission
    from src.lib.validation_review.validator_adapter import save_plan, ValidatorAdapter
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    item = planned
    context, ref, target = proposal(item)
    checkpoint = checkpoint_provider(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    await item.adapter.select_source(context, checkpoint, operation_id="retire-before-new-round",
        contribution_ref=ref, retirement_decision="approve", expected_source_sha=digest, reason="Outside task")
    with store.SessionLocal() as db:
        previous = selections.effective_episode(db, store._fact(db, item.aid, item.ep))
        version = store._current(db, item.aid, target)["version"]
    requested = previous["obligations"][0]["target_id"]
    ep = selections.start_episode(item.scope, item.aid, episode={"episode_id": str(uuid4()),
        "reason": "Explicit curator round for a surviving record", "obligations": previous["obligations"],
        "review_target_ids": [requested]})
    parent_scope = admission.create_scope(item.scope, operation_id=str(uuid4()), kind="workspace_operation", targets=1, episodes=1)
    sid = admission.create_scope(item.scope, operation_id=str(uuid4()), kind="document_run", targets=1, episodes=1, parent_id=parent_scope)
    admission.attach_episode(item.scope, artifact_id=item.aid, episode_id=ep, scope_id=sid,
        predecessor_id=item.ep, renewal_actor_id=item.scope.owner_id, renewal_reason="Curator requested another round", curator_round=True)
    envelope = checkpoint().envelope.model_copy(deep=True)
    envelope.metadata["review_episode_id"] = ep
    matches = tuple(match for match in item.plan.matches if match.object_envelope.pending_ref_id == "B")
    plan = replace(item.plan, envelope=envelope, matches=matches, initial_jobs=())
    adapter = ValidatorAdapter(item.scope, item.aid, ep, save_plan(plan, context=item.adapter.context), authenticated_groups=())
    curator = ReviewContext(item.scope, ep, item.scope.owner_id, actor_type="curator")
    restored = await adapter.select_source(curator, checkpoint, operation_id="restore-in-later-round",
        restore_target_id=target, expected_version=version, reason="Bring back for review")
    assert restored["publication"]["ready"] is False
    with store.SessionLocal() as db:
        manifest = selections.manifest_in_transaction(db, item.scope, item.aid, episode_id=ep)
        covered = selections.effective_episode(db, store._fact(db, item.aid, ep))["review_target_ids"]
        assert any(target in store._target_lineage(db, item.aid, unit) for unit in covered)
        assert any(target in store._target_lineage(db, item.aid, blocker["target_id"])
                   for blocker in manifest["blockers"] if blocker["target_id"])


@pytest.mark.parametrize("descendant", [False, True])
def test_curator_descendant_decision_blocks_whole_record_retirement(planned, descendant):
    item = planned
    context, ref, root = proposal(item)
    target = item.adapter.plan["initial_requests"][0]["target_id"] if descendant else root
    store.apply_decisions(item.scope, item.aid, operation_id="curator-child-review", actor={"type": "curator", "id": item.scope.owner_id},
        entries=[{"target_id": target, "expected_version": 0, "action": "defer", "reason": "Keep for curator assessment"}])
    with store.SessionLocal.begin() as db:
        with pytest.raises(store.HistoryConflict, match="Curator authority"):
            decide(db, item, context, ref, "approve")
        assert store._current(db, item.aid, root)["active"]


def test_reconsideration_of_missing_surviving_input_is_recoverable(planned):
    from src.lib.validation_review import scheduling, source_inputs, admission
    item = planned
    context = ReviewContext(item.scope, item.ep, "supervisor")
    frozen = deepcopy(item.adapter.plan["initial_requests"][0])
    target = frozen["target_id"]
    with store.SessionLocal.begin() as db:
        source_inputs.update(db, item.aid, operation_id="retirement-missing-input", before={target: frozen}, after={})
        ep, _ = admission.lock_episode(db, item.scope, item.ep)
        with pytest.raises(admission.ReviewStopped, match="required_inputs_unavailable"):
            scheduling._refresh_prerequisites(db, context, ep, {"input": frozen}, lambda *a: pytest.fail("No refresh when input absent"))


@pytest.mark.asyncio
async def test_old_publication_without_record_counts_remains_finalizable(planned, monkeypatch):
    from src.lib.validation_history import projection
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider, entries_for
    from src.lib.validation_review import decisions
    from src.schemas.domain_envelope import DomainEnvelope
    item = planned
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: lambda request, binding, **kw: {**_result_payload(request, resolved_values={"curie": "ECO:correct"}), "resolved_objects": []})
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **k: pytest.fail("Unexpected batch"))
    await item.adapter.initial()
    decisions.record_decisions(ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint_provider(item)(), operation_id="accept-for-old-publication", entries=entries_for(item, "accept"))
    with store.SessionLocal.begin() as db:
        manifest = selections.manifest_in_transaction(db, item.scope, item.aid, episode_id=item.ep)
        assert "record_counts" not in manifest
        envelope = checkpoint_provider(item)().envelope.model_copy(deep=True)
        envelope.metadata.pop("record_counts", None)
        old = store._append(db, item.aid, operation_id="historical-publication-shape", kind="selected_projection", body={
            "manifest": manifest, "envelope_revision": 1, "projection": envelope.model_dump(mode="json")})
        ref = {"artifact_id": item.aid, "fact_id": old.id, "body_sha256": old.body_sha,
               "manifest_sha256": manifest["sha256"], "episode_id": item.ep, "envelope_revision": 1}
    read, verified = projection.read_published_projection(item.scope, ref, finalized=True)
    assert isinstance(read, DomainEnvelope)
    assert verified == manifest
    assert "record_counts" not in read.metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["three"], indirect=True)
async def test_retirement_changed_surviving_input_reaches_validator_with_history(planned, monkeypatch):
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    from src.lib.validation_review import source_inputs
    from src.lib.validation_review.validator_adapter import RECONSIDERATION_INPUT
    item = planned
    context, ref, root = proposal(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    result = await item.adapter.select_source(context, checkpoint_provider(item), operation_id="retire-for-dependency",
        contribution_ref=ref, retirement_decision="approve", expected_source_sha=digest, reason="Outside task")
    frozen = deepcopy(item.adapter.plan["initial_requests"][1])
    target = frozen["target_id"]
    before = deepcopy(frozen)
    frozen["request"]["selected_inputs"]["curie"] = "eco:new-context"
    with store.SessionLocal.begin() as db:
        source_inputs.update(db, item.aid, operation_id="retirement-dependent-input", before={target: before},
            after={target: frozen}, selection_id=result["decision_id"])
    contexts = []
    def run(request, binding, **kwargs):
        contexts.append(RECONSIDERATION_INPUT.get())
        return {**_result_payload(request, resolved_values={"curie": "ECO:correct"}), "resolved_objects": []}
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner", lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner", lambda: lambda *a, **k: pytest.fail("Unexpected batch"))
    outcome = await item.adapter.assess_source(context, operation_id="assess-retirement-dependency",
        selection_id=result["decision_id"], target_ids=[target], instructions="Recheck using changed context")
    assert outcome["state"] == "completed"
    assert contexts[0]["context"]["specialist_context"]["supervisor_request"]["instructions"] == "Recheck using changed context"


@pytest.mark.asyncio
async def test_historical_validator_generated_objects_do_not_inflate_retirement_counts(planned):
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    from src.lib.validation_history.record_lifecycle import counts
    item = planned
    context, ref, _ = proposal(item)
    with store.SessionLocal.begin() as db:
        store._append(db, item.aid, operation_id="old-derived-object", kind="target", body={
            "object_ref": "historical-derived", "payload": {"value": "old"},
            "origin": {"kind": "validator_contribution", "completion_id": "historical-completion"}})
        assert counts(db, item.aid, [])["active"] == 1
        digest = selections.source_state_digest(db, item.aid)
    result = await item.adapter.select_source(context, checkpoint_provider(item), operation_id="retire-last-original",
        contribution_ref=ref, retirement_decision="approve", expected_source_sha=digest, reason="Outside task")
    assert result["publication"]["ready"]
    assert checkpoint_provider(item)().envelope.metadata["record_counts"] == {"active": 0, "retired": 1, "pending": 0}


@pytest.mark.asyncio
async def test_curator_acceptance_serializes_before_concurrent_retirement(planned):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from uuid import UUID
    from datetime import datetime, timezone
    from src.lib.curation_workspace.models import CurationReviewSession, CurationCandidate, DomainEnvelopeModel, CurationDraft
    from src.lib.curation_workspace.session_mutation_service import decide_candidate
    from src.lib.domain_envelopes.persistence import write_domain_envelope_checkpoint
    from src.schemas.curation_workspace import CurationCandidateDecisionRequest, CurationCandidateSource
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    item = planned
    context, ref, target = proposal(item)
    checkpoint = checkpoint_provider(item)
    with store.SessionLocal.begin() as db:
        write_domain_envelope_checkpoint(db, checkpoint())
        session = CurationReviewSession(adapter_key="fixture", document_id=UUID(item.scope.document_id), created_by_id=item.scope.owner_id, prepared_at=datetime.now(timezone.utc))
        db.add(session)
        db.flush()
        row = db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id)
        row.session_id = session.id
        candidate = CurationCandidate(session_id=session.id, adapter_key="fixture", source=CurationCandidateSource.EXTRACTED,
            envelope_id=row.envelope_id, object_id="one", envelope_revision=row.revision)
        db.add(candidate)
        db.flush()
        db.add(CurationDraft(candidate_id=candidate.id, adapter_key="fixture", fields=[]))
        db.flush()
        sid, cid = session.id, candidate.id
        digest = selections.source_state_digest(db, item.aid)
    arguments = dict(operation_id="racing-retirement", contribution_ref=ref,
        retirement_decision="approve", expected_source_sha=digest, reason="Outside task")
    with ThreadPoolExecutor(max_workers=1) as executor:
        with store.SessionLocal.begin() as db:
            decide_candidate(db, cid, CurationCandidateDecisionRequest(session_id=str(sid), candidate_id=str(cid),
                action="accept", advance_queue=False), {"sub": item.scope.owner_id})
            future = executor.submit(item.adapter._select_source, context, checkpoint, arguments)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)  # Curator transaction still owns the shared artifact lock.
        with pytest.raises(store.HistoryConflict, match="Curator row decision"):
            future.result(timeout=10)
    with store.SessionLocal() as db:
        assert store._current(db, item.aid, target)["active"]


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["three"], indirect=True)
async def test_decision_refreshes_sibling_retired_while_workspace_was_loaded(planned, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from datetime import datetime, timezone
    from uuid import UUID
    from src.lib.curation_workspace import record_retirement_service as service
    from src.lib.curation_workspace.models import CurationReviewSession, CurationCandidate, CurationDraft, DomainEnvelopeModel
    from src.lib.curation_workspace.session_mutation_service import decide_candidate
    from src.lib.domain_envelopes.persistence import write_domain_envelope_checkpoint
    from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
    from src.schemas.curation_workspace import CurationCandidateDecisionRequest, CurationCandidateSource
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    item = planned
    context, ref, _ = proposal(item)
    checkpoint = checkpoint_provider(item)
    monkeypatch.setattr("src.lib.domain_packs.materialization._registered_materializer_for",
        lambda _: DomainPackMetadataReviewRowMaterializer(item.pack.metadata))
    with store.SessionLocal.begin() as db:
        write_domain_envelope_checkpoint(db, checkpoint())
        session = CurationReviewSession(adapter_key="fixture", document_id=UUID(item.scope.document_id),
            created_by_id=item.scope.owner_id, prepared_at=datetime.now(timezone.utc))
        db.add(session)
        db.flush()
        row = db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id)
        row.session_id = session.id
        ids = []
        for order, name in enumerate("AB"):
            candidate = CurationCandidate(session_id=session.id, adapter_key="fixture", source=CurationCandidateSource.EXTRACTED,
                envelope_id=row.envelope_id, object_id=name, order=order, envelope_revision=row.revision)
            db.add(candidate)
            db.flush()
            db.add(CurationDraft(candidate_id=candidate.id, adapter_key="fixture", fields=[]))
            ids.append(candidate.id)
        sid = session.id
        digest = selections.source_state_digest(db, item.aid)
    from dataclasses import replace
    original_checkpoint = checkpoint
    def checkpoint():
        return replace(original_checkpoint(), session_id=sid)
    loaded, retired = Event(), Event()
    original_guard = service.require_active_candidate_for_write
    def guard(db, candidate):
        loaded.set()
        assert retired.wait(10)
        return original_guard(db, candidate)
    monkeypatch.setattr(service, "require_active_candidate_for_write", guard)
    def accept_sibling():
        with store.SessionLocal.begin() as db:
            decide_candidate(db, ids[1], CurationCandidateDecisionRequest(session_id=str(sid), candidate_id=str(ids[1]),
                action="accept", advance_queue=True), {"sub": item.scope.owner_id})
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(accept_sibling)
        try:
            assert loaded.wait(10)
            await item.adapter.select_source(context, checkpoint, operation_id="retire-loaded-sibling",
                contribution_ref=ref, retirement_decision="approve", expected_source_sha=digest, reason="Outside task")
        finally:
            retired.set()
        future.result(timeout=10)
    with store.SessionLocal() as db:
        session = db.get(CurationReviewSession, sid)
        assert session.total_candidates == 1
        assert session.pending_candidates == 0
        assert session.current_candidate_id != ids[0]
