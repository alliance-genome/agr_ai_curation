"""Write/mutation behavior for curation workspace sessions and candidates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate,
    CurationDraft as DraftModel,
    CurationReviewSession as ReviewSessionModel,
)
from src.lib.curation_workspace.session_common import (
    _actor_claims_payload,
    _draft_values_equal,
    _normalize_uuid,
    _normalized_optional_string,
    _normalized_required_string,
)
from src.lib.curation_workspace.session_persistence import (
    _apply_candidate_progress_counts,
    _delete_candidate_children,
    _delete_session_validation_snapshots,
    _draft_field_payload,
    _persist_candidate_evidence_records,
)
from src.lib.curation_workspace.session_queries import (
    _load_sessions_by_ids,
    _session_context_maps,
    get_candidate_detail,
    get_session_detail,
)
from src.lib.curation_workspace.session_serializers import (
    _action_log_entry,
    _candidate_payload,
    _draft_payload,
    _session_detail,
    build_action_log_entry,
)
from src.lib.curation_workspace.session_types import (
    PreparedDraftFieldInput,
    PreparedEvidenceRecordInput,
)
from src.lib.curation_workspace.session_validation_service import (
    _apply_candidate_validation,
    _load_candidate_for_write,
)
from src.schemas.curation_workspace import (
    CurationActionType,
    CurationActorType,
    CurationCandidateAction,
    CurationCandidateDeleteResponse,
    CurationCandidateDecisionRequest,
    CurationCandidateDecisionResponse,
    CurationCandidateDraftUpdateRequest,
    CurationCandidateDraftUpdateResponse,
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationDraftField as CurationDraftFieldSchema,
    CurationEvidenceRecord as CurationEvidenceRecordPayload,
    CurationEvidenceSource,
    CurationManualCandidateCreateRequest,
    CurationManualCandidateCreateResponse,
    CurationSessionStatus,
    CurationSessionUpdateRequest,
    CurationSessionUpdateResponse,
    CurationValidationSnapshot as CurationValidationSnapshotSchema,
)

def update_session(
    db: Session,
    session_id: str | UUID,
    request: CurationSessionUpdateRequest,
    actor_claims: dict[str, Any],
) -> CurationSessionUpdateResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )

    sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )
    session = sessions[0]

    now = datetime.now(timezone.utc)
    action_log_row: SessionActionLogModel | None = None
    changed = False

    if "current_candidate_id" in request.model_fields_set:
        if request.current_candidate_id is None:
            if session.current_candidate_id is not None:
                session.current_candidate_id = None
                changed = True
        else:
            candidate_id = _normalize_uuid(request.current_candidate_id, field_name="current_candidate_id")
            if not any(candidate.id == candidate_id for candidate in session.candidates):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="current_candidate_id does not belong to this session",
                )
            if session.current_candidate_id != candidate_id:
                session.current_candidate_id = candidate_id
                session.last_worked_at = now
                changed = True

    if "notes" in request.model_fields_set and request.notes != session.notes:
        session.notes = request.notes
        changed = True

    if "curator_id" in request.model_fields_set and request.curator_id != session.assigned_curator_id:
        session.assigned_curator_id = request.curator_id
        changed = True
        if action_log_row is None:
            action_log_row = SessionActionLogModel(
                session_id=session.id,
                action_type=CurationActionType.SESSION_ASSIGNED,
                actor_type=CurationActorType.USER,
                actor=_actor_claims_payload(actor_claims),
                occurred_at=now,
                message="Session curator updated",
            )

    if "status" in request.model_fields_set and request.status != session.status:
        previous_status = session.status
        session.status = request.status
        if request.status == CurationSessionStatus.IN_PROGRESS:
            session.last_worked_at = now
        elif request.status == CurationSessionStatus.PAUSED:
            session.paused_at = now
        elif request.status == CurationSessionStatus.SUBMITTED:
            session.submitted_at = now
        changed = True
        action_log_row = SessionActionLogModel(
            session_id=session.id,
            action_type=CurationActionType.SESSION_STATUS_UPDATED,
            actor_type=CurationActorType.USER,
            actor=_actor_claims_payload(actor_claims),
            occurred_at=now,
            previous_session_status=previous_status,
            new_session_status=request.status,
            message=f"Session status updated from {previous_status.value} to {request.status.value}",
        )

    if changed:
        session.session_version += 1
        db.add(session)
        if action_log_row is not None:
            db.add(action_log_row)
        db.commit()

    updated_sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    updated_session = updated_sessions[0]
    document_map, user_map = _session_context_maps(db, [updated_session])
    return CurationSessionUpdateResponse(
        session=_session_detail(db, updated_session, document_map, user_map),
        action_log_entry=_action_log_entry(action_log_row),
    )


def _dedupe_non_empty_strings(values: Sequence[str], *, field_name: str) -> list[str]:
    normalized_values: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized = _normalized_optional_string(value, field_name=field_name)
        if normalized is None or normalized in seen:
            continue
        normalized_values.append(normalized)
        seen.add(normalized)

    return normalized_values


def _manual_candidate_field_inputs(
    fields: Sequence[CurationDraftFieldSchema],
) -> list[PreparedDraftFieldInput]:
    return [
        PreparedDraftFieldInput(
            field_key=_normalized_required_string(field.field_key, field_name="draft.fields.field_key"),
            label=_normalized_required_string(field.label, field_name="draft.fields.label"),
            value=field.value,
            seed_value=field.value,
            field_type=_normalized_optional_string(field.field_type, field_name="draft.fields.field_type"),
            group_key=_normalized_optional_string(field.group_key, field_name="draft.fields.group_key"),
            group_label=_normalized_optional_string(
                field.group_label,
                field_name="draft.fields.group_label",
            ),
            order=field.order,
            required=field.required,
            read_only=field.read_only,
            dirty=False,
            stale_validation=False,
            metadata=dict(field.metadata),
        )
        for field in fields
    ]


def _manual_candidate_evidence_inputs(
    evidence_records: Sequence[CurationEvidenceRecordPayload],
) -> list[PreparedEvidenceRecordInput]:
    prepared_records: list[PreparedEvidenceRecordInput] = []

    for evidence_record in evidence_records:
        field_keys = _dedupe_non_empty_strings(
            evidence_record.field_keys,
            field_name="evidence_anchors.field_keys",
        )
        field_group_keys = _dedupe_non_empty_strings(
            evidence_record.field_group_keys,
            field_name="evidence_anchors.field_group_keys",
        )

        if not field_keys and not field_group_keys:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Manual candidate evidence must include at least one field_key "
                    "or field_group_key"
                ),
            )

        prepared_records.append(
            PreparedEvidenceRecordInput(
                source=evidence_record.source,
                field_keys=field_keys,
                field_group_keys=field_group_keys,
                is_primary=evidence_record.is_primary,
                anchor=evidence_record.anchor.model_dump(mode="json"),
                warnings=list(evidence_record.warnings or []),
            )
        )

    return prepared_records


def _manual_candidate_normalized_payload(
    fields: Sequence[PreparedDraftFieldInput],
) -> dict[str, Any]:
    return {
        field.field_key: field.value
        for field in sorted(fields, key=lambda item: (item.order, item.field_key))
    }


def create_manual_candidate(
    db: Session,
    session_id: str | UUID,
    request: CurationManualCandidateCreateRequest,
    *,
    actor_claims: dict[str, Any],
) -> CurationManualCandidateCreateResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )

    if request.source != CurationCandidateSource.MANUAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manual candidate creation only supports source=manual",
        )

    adapter_key = _normalized_required_string(request.adapter_key, field_name="adapter_key")
    display_label = _normalized_optional_string(request.display_label, field_name="display_label")
    draft_adapter_key = _normalized_required_string(
        request.draft.adapter_key,
        field_name="draft.adapter_key",
    )

    if draft_adapter_key != adapter_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="draft.adapter_key must match adapter_key",
        )

    sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )
    session_row = sessions[0]

    if session_row.adapter_key != adapter_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="adapter_key must match the session adapter",
        )

    field_inputs = _manual_candidate_field_inputs(request.draft.fields)
    evidence_inputs = _manual_candidate_evidence_inputs(request.evidence_anchors)
    available_field_keys = {
        field_input.field_key
        for field_input in field_inputs
    }
    missing_evidence_field_keys = sorted(
        {
            field_key
            for evidence_input in evidence_inputs
            for field_key in evidence_input.field_keys
            if field_key not in available_field_keys
        }
    )
    if missing_evidence_field_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Manual candidate evidence references unknown field(s): "
                f"{', '.join(missing_evidence_field_keys)}"
            ),
        )

    now = datetime.now(timezone.utc)
    next_order = max((candidate.order for candidate in session_row.candidates), default=-1) + 1
    resolved_display_label = (
        display_label
        or _normalized_optional_string(request.draft.title, field_name="draft.title")
        or f"Manual candidate {next_order + 1}"
    )

    candidate_row = CurationCandidate(
        session_id=session_row.id,
        source=CurationCandidateSource.MANUAL,
        status=CurationCandidateStatus.PENDING,
        order=next_order,
        adapter_key=adapter_key,
        display_label=resolved_display_label,
        secondary_label=None,
        conversation_summary=None,
        extraction_result_id=None,
        normalized_payload=_manual_candidate_normalized_payload(field_inputs),
        candidate_metadata={},
        created_at=now,
        updated_at=now,
    )
    db.add(candidate_row)
    db.flush()

    evidence_anchor_ids_by_field = _persist_candidate_evidence_records(
        db,
        candidate_row,
        evidence_inputs,
        created_at=now,
    )

    draft_row = DraftModel(
        candidate_id=candidate_row.id,
        adapter_key=adapter_key,
        version=1,
        title=resolved_display_label,
        summary=_normalized_optional_string(request.draft.summary, field_name="draft.summary"),
        fields=[
            _draft_field_payload(
                field_input,
                evidence_anchor_ids_by_field=evidence_anchor_ids_by_field,
                field_results={},
            )
            for field_input in field_inputs
        ],
        notes=_normalized_optional_string(request.draft.notes, field_name="draft.notes"),
        created_at=now,
        updated_at=now,
        last_saved_at=now,
        draft_metadata=dict(request.draft.metadata),
    )
    db.add(draft_row)
    db.flush()

    session_row.total_candidates += 1
    session_row.pending_candidates += 1
    session_row.manual_candidates += 1
    session_row.current_candidate_id = candidate_row.id
    session_row.session_version += 1
    session_row.updated_at = now
    session_row.last_worked_at = now
    db.add(session_row)

    evidence_anchor_ids = [
        anchor_id
        for anchor_ids in evidence_anchor_ids_by_field.values()
        for anchor_id in anchor_ids
    ]
    action_log_row = SessionActionLogModel(
        session_id=session_row.id,
        candidate_id=candidate_row.id,
        draft_id=draft_row.id,
        action_type=CurationActionType.CANDIDATE_CREATED,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=now,
        new_candidate_status=CurationCandidateStatus.PENDING,
        changed_field_keys=[field_input.field_key for field_input in field_inputs],
        evidence_anchor_ids=evidence_anchor_ids,
        message="Manual candidate created",
        action_metadata={
            "adapter_key": adapter_key,
            "source": CurationCandidateSource.MANUAL.value,
            "display_label": resolved_display_label,
            "evidence_count": len(request.evidence_anchors),
        },
    )
    db.add(action_log_row)
    db.flush()

    response = CurationManualCandidateCreateResponse(
        candidate=get_candidate_detail(
            db,
            candidate_row.id,
            session_id=session_row.id,
        ),
        session=get_session_detail(db, session_row.id),
        action_log_entry=build_action_log_entry(action_log_row),
    )
    db.commit()
    return response

def update_candidate_draft(
    db: Session,
    session_id: str | UUID,
    candidate_id: str | UUID,
    request: CurationCandidateDraftUpdateRequest,
    actor_claims: dict[str, Any],
) -> CurationCandidateDraftUpdateResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    request_candidate_id = _normalize_uuid(request.candidate_id, field_name="candidate_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )
    if normalized_candidate_id != request_candidate_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path candidate_id does not match request body candidate_id",
        )

    candidate = _load_candidate_for_write(
        db,
        session_id=normalized_session_id,
        candidate_id=normalized_candidate_id,
    )
    draft_row = candidate.draft
    request_draft_id = _normalize_uuid(request.draft_id, field_name="draft_id")
    if draft_row.id != request_draft_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request draft_id does not match the candidate draft",
        )
    if request.expected_version is not None and draft_row.version != request.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Draft version mismatch: expected {request.expected_version}, "
                f"found {draft_row.version}"
            ),
        )

    draft_fields = [
        CurationDraftFieldSchema.model_validate(field_payload)
        for field_payload in (draft_row.fields or [])
    ]
    field_index = {
        field.field_key: index
        for index, field in enumerate(draft_fields)
    }
    changed_field_keys: list[str] = []

    for field_change in request.field_changes:
        field_position = field_index.get(field_change.field_key)
        if field_position is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown draft field {field_change.field_key}",
            )

        current_field = draft_fields[field_position]
        next_value = (
            current_field.seed_value
            if field_change.revert_to_seed
            else field_change.value
        )
        next_dirty = not _draft_values_equal(next_value, current_field.seed_value)
        next_field = current_field.model_copy(
            update={
                "value": next_value,
                "dirty": next_dirty,
                "stale_validation": next_dirty,
            }
        )

        if current_field != next_field:
            draft_fields[field_position] = next_field
            changed_field_keys.append(current_field.field_key)

    notes_changed = (
        "notes" in request.model_fields_set
        and request.notes != draft_row.notes
    )
    if not changed_field_keys and not notes_changed:
        return CurationCandidateDraftUpdateResponse(
            candidate=_candidate_payload(candidate),
            draft=_draft_payload(candidate),
            validation_snapshot=None,
            action_log_entry=None,
        )

    now = datetime.now(timezone.utc)
    draft_row.fields = [
        field.model_dump(mode="json")
        for field in draft_fields
    ]
    if notes_changed:
        draft_row.notes = request.notes
    draft_row.version += 1
    draft_row.updated_at = now
    draft_row.last_saved_at = now
    candidate.updated_at = now
    candidate.session.updated_at = now
    candidate.session.last_worked_at = now

    validation_snapshot: CurationValidationSnapshotSchema | None = None
    if changed_field_keys:
        validation_snapshot, _ = _apply_candidate_validation(
            db,
            candidate,
            force=True,
            validated_at=now,
            field_keys=changed_field_keys,
        )

    action_log_row = SessionActionLogModel(
        session_id=candidate.session_id,
        candidate_id=candidate.id,
        draft_id=draft_row.id,
        action_type=CurationActionType.CANDIDATE_UPDATED,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=now,
        changed_field_keys=changed_field_keys,
        message=(
            "Autosaved candidate draft changes"
            if request.autosave
            else "Candidate draft updated"
        ),
    )
    db.add(action_log_row)
    db.add(candidate.session)
    db.add(candidate)
    db.add(draft_row)
    db.commit()

    updated_candidate = _load_candidate_for_write(
        db,
        session_id=normalized_session_id,
        candidate_id=normalized_candidate_id,
    )
    return CurationCandidateDraftUpdateResponse(
        candidate=_candidate_payload(updated_candidate),
        draft=_draft_payload(updated_candidate),
        validation_snapshot=validation_snapshot,
        action_log_entry=_action_log_entry(action_log_row),
    )

def delete_candidate(
    db: Session,
    session_id: str | UUID,
    candidate_id: str | UUID,
    *,
    actor_claims: dict[str, Any],
) -> CurationCandidateDeleteResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )

    session = sessions[0]
    candidate = next(
        (
            session_candidate
            for session_candidate in session.candidates
            if session_candidate.id == normalized_candidate_id
        ),
        None,
    )
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Curation candidate {normalized_candidate_id} not found in session "
                f"{normalized_session_id}"
            ),
        )
    if candidate.draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curation candidate {normalized_candidate_id} is missing its draft payload",
        )

    now = datetime.now(timezone.utc)
    remaining_candidates = [
        session_candidate
        for session_candidate in session.candidates
        if session_candidate.id != normalized_candidate_id
    ]
    next_candidate_uuid = _current_candidate_id_after_delete(
        session=session,
        deleted_candidate=candidate,
        remaining_candidates=remaining_candidates,
    )

    _delete_session_validation_snapshots(db, session_id=session.id)
    _delete_candidate_children(
        db,
        session_id=session.id,
        candidate_ids=[candidate.id],
    )

    session.candidates = list(remaining_candidates)
    session.validation_snapshots = []
    session.action_log_entries = [
        action_log_entry
        for action_log_entry in session.action_log_entries
        if action_log_entry.candidate_id != candidate.id
    ]
    session.current_candidate_id = next_candidate_uuid
    session.last_worked_at = now
    session.updated_at = now
    session.session_version += 1
    _apply_candidate_progress_counts(session, remaining_candidates)

    action_log_row = SessionActionLogModel(
        session_id=session.id,
        action_type=CurationActionType.CANDIDATE_DELETED,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=now,
        previous_candidate_status=candidate.status,
        message="Candidate deleted from session",
        evidence_anchor_ids=[str(evidence_row.id) for evidence_row in candidate.evidence_anchors],
        action_metadata={
            "deleted_candidate_id": str(candidate.id),
            "deleted_draft_id": str(candidate.draft.id),
            "deleted_display_label": candidate.display_label,
            "deleted_evidence_anchor_count": len(candidate.evidence_anchors),
            "deleted_validation_snapshot_count": len(candidate.validation_snapshots),
            "next_candidate_id": str(next_candidate_uuid) if next_candidate_uuid else None,
            "session_validation_cleared": True,
        },
    )

    db.add(action_log_row)
    db.commit()

    action_log_entry = build_action_log_entry(action_log_row)
    db.expire_all()

    return CurationCandidateDeleteResponse(
        deleted_candidate_id=str(candidate.id),
        session=get_session_detail(db, session.id),
        action_log_entry=action_log_entry,
    )

def decide_candidate(
    db: Session,
    candidate_id: str | UUID,
    request: CurationCandidateDecisionRequest,
    actor_claims: dict[str, Any],
) -> CurationCandidateDecisionResponse:
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    normalized_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )

    session = sessions[0]
    candidate = next(
        (
            session_candidate
            for session_candidate in session.candidates
            if session_candidate.id == normalized_candidate_id
        ),
        None,
    )
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Curation candidate {normalized_candidate_id} not found in session "
                f"{normalized_session_id}"
            ),
        )

    now = datetime.now(timezone.utc)
    previous_candidate_status = candidate.status
    previous_session_status = session.status
    reason = _normalize_optional_reason(request.reason)
    changed_field_keys: list[str] = []
    removed_manual_evidence_ids: list[str] = []
    notes_reset = False

    if request.action == CurationCandidateAction.RESET:
        (
            changed_field_keys,
            removed_manual_evidence_ids,
            notes_reset,
        ) = _reset_candidate_state(candidate, db, occurred_at=now)
        candidate.status = CurationCandidateStatus.PENDING
    else:
        candidate.status = _decision_candidate_status(request.action)

    if session.status == CurationSessionStatus.NEW:
        session.status = CurationSessionStatus.IN_PROGRESS

    next_candidate_uuid = (
        _next_pending_candidate_id(session, normalized_candidate_id)
        if request.advance_queue and request.action != CurationCandidateAction.RESET
        else None
    )

    session.current_candidate_id = next_candidate_uuid or normalized_candidate_id
    session.last_worked_at = now
    session.updated_at = now
    session.session_version += 1

    candidate.last_reviewed_at = now
    candidate.updated_at = now

    _apply_candidate_progress_counts(session, session.candidates)

    action_log_row = SessionActionLogModel(
        session_id=session.id,
        candidate_id=candidate.id,
        draft_id=candidate.draft.id if candidate.draft is not None else None,
        action_type=_decision_action_type(request.action),
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=now,
        previous_session_status=(
            previous_session_status if previous_session_status != session.status else None
        ),
        new_session_status=(
            session.status if previous_session_status != session.status else None
        ),
        previous_candidate_status=previous_candidate_status,
        new_candidate_status=candidate.status,
        changed_field_keys=changed_field_keys,
        evidence_anchor_ids=removed_manual_evidence_ids,
        reason=reason,
        message=_decision_action_message(request.action, candidate.status),
        action_metadata={
            "advance_queue": request.advance_queue,
            "next_candidate_id": str(next_candidate_uuid) if next_candidate_uuid else None,
            "manual_evidence_removed_count": len(removed_manual_evidence_ids),
            "notes_reset": notes_reset,
        },
    )

    db.add(session)
    db.add(candidate)
    if candidate.draft is not None:
        db.add(candidate.draft)
    db.add(action_log_row)
    db.flush()

    response = CurationCandidateDecisionResponse(
        candidate=get_candidate_detail(db, candidate.id, session_id=session.id),
        session=get_session_detail(db, session.id),
        next_candidate_id=str(next_candidate_uuid) if next_candidate_uuid else None,
        action_log_entry=build_action_log_entry(action_log_row),
    )
    db.commit()
    return response


def _decision_candidate_status(action: CurationCandidateAction) -> CurationCandidateStatus:
    if action == CurationCandidateAction.ACCEPT:
        return CurationCandidateStatus.ACCEPTED
    if action == CurationCandidateAction.REJECT:
        return CurationCandidateStatus.REJECTED
    return CurationCandidateStatus.PENDING


def _decision_action_type(action: CurationCandidateAction) -> CurationActionType:
    if action == CurationCandidateAction.ACCEPT:
        return CurationActionType.CANDIDATE_ACCEPTED
    if action == CurationCandidateAction.REJECT:
        return CurationActionType.CANDIDATE_REJECTED
    return CurationActionType.CANDIDATE_RESET


def _decision_action_message(
    action: CurationCandidateAction,
    new_status: CurationCandidateStatus,
) -> str:
    if action == CurationCandidateAction.RESET:
        return "Candidate reset to pending"
    return f"Candidate marked as {new_status.value}"


def _normalize_optional_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    normalized = reason.strip()
    return normalized or None


def _current_candidate_id_after_delete(
    *,
    session: ReviewSessionModel,
    deleted_candidate: CurationCandidate,
    remaining_candidates: Sequence[CurationCandidate],
) -> UUID | None:
    if not remaining_candidates:
        return None

    if (
        session.current_candidate_id is not None
        and session.current_candidate_id != deleted_candidate.id
        and any(candidate.id == session.current_candidate_id for candidate in remaining_candidates)
    ):
        return session.current_candidate_id

    following_candidates = [
        candidate
        for candidate in remaining_candidates
        if candidate.order > deleted_candidate.order
    ]
    if following_candidates:
        return min(following_candidates, key=lambda candidate: candidate.order).id

    preceding_candidates = [
        candidate
        for candidate in remaining_candidates
        if candidate.order < deleted_candidate.order
    ]
    if preceding_candidates:
        return max(preceding_candidates, key=lambda candidate: candidate.order).id

    return remaining_candidates[0].id


def _reset_candidate_state(
    candidate: CurationCandidate,
    db: Session,
    *,
    occurred_at: datetime,
) -> tuple[list[str], list[str], bool]:
    draft = candidate.draft
    changed_field_keys: list[str] = []
    notes_reset = False
    draft_fields_changed = False
    manual_evidence_rows = [
        evidence_row
        for evidence_row in candidate.evidence_anchors
        if evidence_row.source == CurationEvidenceSource.MANUAL
    ]
    manual_evidence_ids = {str(evidence_row.id) for evidence_row in manual_evidence_rows}

    if draft is not None:
        updated_fields: list[dict[str, Any]] = []
        for field_payload in draft.fields or []:
            next_field_payload = dict(field_payload)
            seed_value = field_payload.get("seed_value")
            field_changed = (
                field_payload.get("value") != seed_value
                or bool(field_payload.get("dirty"))
                or bool(field_payload.get("stale_validation"))
            )
            if field_changed:
                changed_field_keys.append(str(field_payload.get("field_key")))

            next_field_payload["value"] = seed_value
            next_field_payload["dirty"] = False
            next_field_payload["stale_validation"] = False
            existing_anchor_ids = [
                str(anchor_id) for anchor_id in field_payload.get("evidence_anchor_ids") or []
            ]
            remaining_anchor_ids = [
                anchor_id
                for anchor_id in existing_anchor_ids
                if anchor_id not in manual_evidence_ids
            ]
            if field_changed or remaining_anchor_ids != existing_anchor_ids:
                next_field_payload["evidence_anchor_ids"] = remaining_anchor_ids
                draft_fields_changed = True
            updated_fields.append(next_field_payload)

        if draft.notes is not None:
            draft.notes = None
            notes_reset = True

        if draft_fields_changed or notes_reset:
            draft.fields = updated_fields
            draft.version += 1
            draft.updated_at = occurred_at
            draft.last_saved_at = occurred_at

    if manual_evidence_ids:
        candidate.evidence_anchors = [
            evidence_row
            for evidence_row in candidate.evidence_anchors
            if str(evidence_row.id) not in manual_evidence_ids
        ]
        for evidence_row in manual_evidence_rows:
            db.delete(evidence_row)

    return changed_field_keys, sorted(manual_evidence_ids), notes_reset


def _next_pending_candidate_id(
    session: ReviewSessionModel,
    current_candidate_id: UUID,
) -> UUID | None:
    ordered_candidates = sorted(session.candidates, key=lambda candidate_row: candidate_row.order)
    current_index = next(
        (
            index
            for index, candidate_row in enumerate(ordered_candidates)
            if candidate_row.id == current_candidate_id
        ),
        None,
    )
    if current_index is None or len(ordered_candidates) <= 1:
        return None

    candidate_count = len(ordered_candidates)
    for offset in range(1, candidate_count):
        next_index = (current_index + offset) % candidate_count
        candidate_row = ordered_candidates[next_index]
        if candidate_row.status == CurationCandidateStatus.PENDING:
            return candidate_row.id

    return None

__all__ = [
    "create_manual_candidate",
    "decide_candidate",
    "delete_candidate",
    "update_candidate_draft",
    "update_session",
]
