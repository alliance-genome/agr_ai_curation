"""Curator history and explicit restoration for retired extraction records."""
from sqlalchemy import select
from src.lib.validation_history import store
from src.lib.validation_history.episodes import latest_effective_episode
from src.lib.validation_history.record_lifecycle import retired_records
from src.models.sql.validation_history import ValidationFact
from src.schemas.domain_envelope import DomainEnvelope
from .models import DomainEnvelopeModel


def binding(db, envelope_id, owner_id):
    row = db.get(DomainEnvelopeModel, envelope_id)
    if row is None or row.session_id is None:
        raise store.HistoryAccessError("Workspace envelope unavailable")
    envelope = DomainEnvelope.model_validate(row.envelope_json)
    ref = envelope.metadata.get("validation_history")
    if ref is None:
        raise store.HistoryConflict("Envelope has no review history")
    scope = store.HistoryScope(owner_id, str(row.document_id) if row.document_id else None, ref["session_id"])
    store._artifact(db, ref["artifact_id"], scope)
    return row, envelope, scope, ref["artifact_id"]


def listing(db, session_id):
    result = []
    for row in db.scalars(select(DomainEnvelopeModel).where(DomainEnvelopeModel.session_id == session_id)):
        ref = row.envelope_json.get("metadata", {}).get("validation_history")
        if ref is not None:
            result.extend({**record, "envelope_id": row.envelope_id}
                          for record in retired_records(db, ref["artifact_id"]))
    return result


def detail(db, envelope_id, *, owner_id, fact_id, sha256, offset):
    from src.lib.validation_review.reads import exact_page
    from src.lib.validation_review.policy import ReviewLimits
    _, _, _, artifact_id = binding(db, envelope_id, owner_id)
    fact = store._fact(db, artifact_id, fact_id)
    if fact.body_sha != sha256:
        raise store.HistoryConflict("Historical reference changed")
    return exact_page(store._content(db, fact), fact_id=fact_id, sha256=sha256,
                      offset=offset, limit=ReviewLimits.configured().packet_bytes)


def restore(envelope_id, *, owner_id, request, groups):
    from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest
    from src.lib.validation_review.decisions import ReviewContext
    from src.lib.validation_review.validator_adapter import ValidatorAdapter
    with store.SessionLocal() as db:
        _, _, scope, artifact_id = binding(db, envelope_id, owner_id)
        episode = latest_effective_episode(db, artifact_id)
        if episode is None:
            raise store.HistoryConflict("Restoration requires a saved review plan")
        plan = db.scalar(select(ValidationFact).where(ValidationFact.artifact_id == artifact_id,
            ValidationFact.parent_id == episode.id, ValidationFact.kind == "review_chat_plan"))
        if plan is None:
            raise store.HistoryConflict("Review plan unavailable")
        episode_id, plan_id = episode.id, plan.id

    def checkpoint():
        with store.SessionLocal() as db:
            row, envelope, _, _ = binding(db, envelope_id, owner_id)
            envelope.metadata["review_episode_id"] = episode_id
            return DomainEnvelopeCheckpointRequest(project_key=row.project_key, envelope=envelope,
                expected_revision=row.revision, document_id=row.document_id, session_id=row.session_id,
                flow_run_id=row.flow_run_id, authenticated_groups=groups, curator_authority_groups=groups,
                object_model_ref_json=dict(row.object_model_ref_json), model_field_ref_json=dict(row.model_field_ref_json))
    adapter = ValidatorAdapter(scope, artifact_id, episode_id, plan_id, authenticated_groups=groups)
    return adapter._select_source(ReviewContext(scope, episode_id, owner_id, actor_type="curator"), checkpoint,
        {"operation_id": str(request.request_id), "restore_target_id": str(request.target_id),
         "expected_version": request.expected_version, "reason": request.reason})


def materialize_restored_candidate(db, envelope, revision, target_id, *, groups):
    """First-open-after-retirement workspaces may not have a draft cache yet."""
    from datetime import datetime, timezone
    from src.lib.domain_packs.materialization import materialize_persisted_envelope_review_rows
    from .models import CurationCandidate, CurationReviewSession
    from .pipeline import _prepared_candidate_input_from_review_row
    from .session_persistence import _persist_prepared_candidates
    row = db.get(DomainEnvelopeModel, envelope.envelope_id)
    if row.session_id is None:
        return
    obj = next(obj for obj in envelope.extracted_objects if obj.metadata["validation_history"]["target_id"] == target_id)
    object_id = obj.object_id or obj.pending_ref_id
    if db.scalar(select(CurationCandidate.id).where(CurationCandidate.envelope_id == envelope.envelope_id,
                                                   CurationCandidate.object_id == object_id)) is not None:
        return
    session = db.get(CurationReviewSession, row.session_id)
    materialized = materialize_persisted_envelope_review_rows(db, envelope.envelope_id, revision=revision,
        active_group_ids=groups, user_id=session.created_by_id)
    rows = [item for item in materialized.rows if item.object_id == object_id]
    if len(rows) != 1:
        raise store.HistoryConflict("Restored record has no unique workspace projection")
    review = rows[0]
    orders = list(db.scalars(select(CurationCandidate.order).where(CurationCandidate.session_id == session.id)))
    prepared = _prepared_candidate_input_from_review_row(review, adapter_key=session.adapter_key,
        prep_extraction_result_id=None, prep_output=None, candidate_index=max(orders, default=-1) + 1)
    _persist_prepared_candidates(db, session, [prepared], prepared_at=datetime.now(timezone.utc))
