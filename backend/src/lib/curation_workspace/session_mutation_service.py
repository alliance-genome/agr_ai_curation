"""Write/mutation behavior for curation workspace sessions and candidates."""

from __future__ import annotations

import copy
import json
from hashlib import sha256
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from src.lib.domain_envelopes.patches import (
    EnvelopeFieldPatch,
    EnvelopeFieldPatchOperation,
    EnvelopeFieldPatchStatus,
    apply_curator_field_patch,
)
from src.lib.domain_envelopes.persistence import (
    DomainEnvelopeCheckpointRequest,
    DomainEnvelopePersistenceError,
    write_domain_envelope_checkpoint,
)
from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate,
    CurationDraft as DraftModel,
    CurationReviewSession as ReviewSessionModel,
)
from src.lib.curation_workspace.session_common import (
    _actor_claims_payload,
    _draft_values_equal,
    _metadata_allows_curator_override,
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
    load_domain_envelope_row_for_patch,
    load_projection_candidates_for_patch,
)
from src.lib.curation_workspace.session_serializers import (
    _action_log_entry,
    _candidate_payload,
    _draft_payload,
    _session_detail,
    build_action_log_entry,
    build_envelope_field_patch_response,
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
    CurationEnvelopeFieldPatchRequest,
    CurationEnvelopeFieldPatchResponse,
    CurationEvidenceRecord as CurationEvidenceRecordPayload,
    CurationEvidenceSource,
    CurationManualCandidateCreateRequest,
    CurationManualCandidateCreateResponse,
    CurationSessionStatus,
    CurationSessionUpdateRequest,
    CurationSessionUpdateResponse,
    CurationValidationFindingWaiveRequest,
    CurationValidationFindingWaiveResponse,
    CurationValidationSnapshot as CurationValidationSnapshotSchema,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    FieldRef,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ObjectRef,
    ValidationFinding,
    ValidationFindingStatus,
    parse_field_path,
)


_MISSING = object()


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
    draft_materialized_to_envelope = False
    if changed_field_keys and _domain_envelope_candidate(candidate):
        _materialize_candidate_draft_changes_into_envelope(
            db,
            candidate,
            draft_fields=draft_fields,
            changed_field_keys=changed_field_keys,
            actor_claims=actor_claims,
            updated_at=now,
        )
        draft_materialized_to_envelope = True

    if not draft_materialized_to_envelope:
        draft_row.fields = [
            field.model_dump(mode="json")
            for field in draft_fields
        ]
    if notes_changed:
        draft_row.notes = request.notes
    if not draft_materialized_to_envelope or notes_changed:
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


def _domain_envelope_candidate(candidate: CurationCandidate) -> bool:
    return (
        candidate.envelope_id is not None
        and candidate.object_id is not None
        and candidate.envelope_revision is not None
    )


def _materialize_candidate_draft_changes_into_envelope(
    db: Session,
    candidate: CurationCandidate,
    *,
    draft_fields: Sequence[CurationDraftFieldSchema],
    changed_field_keys: Sequence[str],
    actor_claims: dict[str, Any],
    updated_at: datetime,
) -> None:
    envelope_id = str(candidate.envelope_id)
    object_id = str(candidate.object_id)
    envelope_row = load_domain_envelope_row_for_patch(db, envelope_id)
    if envelope_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Domain envelope {envelope_id} not found",
        )
    if envelope_row.session_id is not None and envelope_row.session_id != candidate.session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Domain envelope does not belong to the candidate session",
        )

    envelope = DomainEnvelope.model_validate(envelope_row.envelope_json)
    domain_pack = resolve_curation_domain_pack_by_id(envelope.domain_pack_id)
    if domain_pack is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Domain pack {envelope.domain_pack_id} is not available for draft materialization",
        )

    domain_object = _envelope_object_by_stable_id(envelope, object_id)
    if domain_object is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Domain envelope object {object_id} not found",
        )

    actor = _actor_claims_payload(actor_claims)
    working_envelope = envelope
    field_by_key = {field.field_key: field for field in draft_fields}
    materialized_field_paths: list[str] = []
    current_revision = envelope_row.revision

    for field_key in changed_field_keys:
        draft_field = field_by_key[field_key]
        field_path = _draft_field_projection_paths(draft_field)[0]
        materialized_value = _draft_field_materialized_value(draft_field)
        current_object = _envelope_object_by_stable_id(working_envelope, object_id)
        if current_object is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Draft materialization target disappeared from the envelope",
            )
        before = _payload_value(current_object.payload, field_path)
        patch_result = apply_curator_field_patch(
            working_envelope,
            domain_pack,
            EnvelopeFieldPatch(
                envelope_id=envelope_id,
                expected_revision=current_revision,
                object_id=object_id,
                field_path=field_path,
                before=None if before is _MISSING else before,
                value=copy.deepcopy(materialized_value),
                operation=EnvelopeFieldPatchOperation.REPLACE,
                reason="draft_materialization",
            ),
            current_revision=current_revision,
            actor_id=actor["actor_id"],
        )
        if patch_result.status is EnvelopeFieldPatchStatus.STALE_REVISION:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=patch_result.errors[0],
            )
        if not patch_result.accepted:
            raise HTTPException(
                status_code=_rejected_patch_status_code(patch_result),
                detail="; ".join(patch_result.errors),
            )
        working_envelope = patch_result.envelope
        materialized_field_paths.append(field_path)
        for projected_field_path in _draft_field_materializes_to_paths(draft_field):
            if projected_field_path == field_path:
                continue
            _ensure_domain_pack_declares_field_path(
                domain_pack,
                object_type=domain_object.object_type,
                field_path=projected_field_path,
            )
            working_envelope = _replace_envelope_object_payload_value(
                working_envelope,
                object_id=object_id,
                field_path=projected_field_path,
                value=copy.deepcopy(materialized_value),
            )
            materialized_field_paths.append(projected_field_path)

    checkpoint_revision = _checkpoint_patch_result(
        db,
        envelope_row=envelope_row,
        patched_envelope=working_envelope,
        expected_revision=current_revision,
    )
    updated_object = _envelope_object_by_stable_id(working_envelope, object_id)
    if updated_object is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Draft materialization target disappeared during projection refresh",
        )
    _refresh_candidate_projection_from_envelope(
        candidate,
        updated_object,
        envelope_revision=checkpoint_revision,
        changed_field_path=materialized_field_paths,
        updated_at=updated_at,
    )


def patch_envelope_field(
    db: Session,
    session_id: str | UUID,
    request: CurationEnvelopeFieldPatchRequest,
    actor_claims: dict[str, Any],
) -> CurationEnvelopeFieldPatchResponse:
    """Patch the persisted envelope source of truth and refresh workspace projections."""

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
    session_row = sessions[0]

    envelope_row = load_domain_envelope_row_for_patch(db, request.envelope_id)
    if envelope_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Domain envelope {request.envelope_id} not found",
        )
    if envelope_row.session_id is not None and envelope_row.session_id != normalized_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Domain envelope does not belong to the requested session",
        )

    envelope = DomainEnvelope.model_validate(envelope_row.envelope_json)
    previous_revision = envelope_row.revision
    domain_pack = resolve_curation_domain_pack_by_id(envelope.domain_pack_id)
    if domain_pack is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Domain pack {envelope.domain_pack_id} is not available for patch validation",
        )

    patch_payload = {
        "envelope_id": request.envelope_id,
        "expected_revision": request.expected_revision,
        "object_id": request.object_id,
        "field_path": request.field_path,
        "before": request.before,
        "value": request.value,
        "operation": EnvelopeFieldPatchOperation(request.operation.value),
        "reason": request.reason,
    }
    if request.patch_id is not None:
        patch_payload["patch_id"] = request.patch_id

    patch_result = apply_curator_field_patch(
        envelope,
        domain_pack,
        EnvelopeFieldPatch(**patch_payload),
        current_revision=previous_revision,
        actor_id=_actor_claims_payload(actor_claims)["actor_id"],
    )

    if patch_result.status is EnvelopeFieldPatchStatus.STALE_REVISION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=patch_result.errors[0],
        )

    if not patch_result.accepted:
        rejected_checkpoint_revision = _checkpoint_patch_result(
            db,
            envelope_row=envelope_row,
            patched_envelope=patch_result.envelope,
            expected_revision=previous_revision,
        )
        _record_envelope_patch_action(
            db,
            session_row=session_row,
            candidate_id=None,
            draft_id=None,
            request=request,
            patch_result=patch_result,
            actor_claims=actor_claims,
            envelope_revision=rejected_checkpoint_revision,
        )
        db.commit()
        raise HTTPException(
            status_code=_rejected_patch_status_code(patch_result),
            detail="; ".join(patch_result.errors),
        )

    checkpoint_revision = _checkpoint_patch_result(
        db,
        envelope_row=envelope_row,
        patched_envelope=patch_result.envelope,
        expected_revision=previous_revision,
    )

    updated_object = _envelope_object_by_stable_id(
        patch_result.envelope,
        request.object_id,
    )
    if updated_object is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Patched envelope object disappeared during projection regeneration",
        )

    now = datetime.now(timezone.utc)
    projection_candidates = load_projection_candidates_for_patch(
        db,
        session_id=normalized_session_id,
        envelope_id=request.envelope_id,
        object_id=request.object_id,
    )
    projection_candidate_ids: list[str] = []
    for candidate in projection_candidates:
        projection_candidate_ids.append(str(candidate.id))
        _refresh_candidate_projection_from_envelope(
            candidate,
            updated_object,
            envelope_revision=checkpoint_revision,
            changed_field_path=request.field_path,
            updated_at=now,
        )
        db.add(candidate)
        if candidate.draft is not None:
            db.add(candidate.draft)

    session_row.session_version += 1
    session_row.updated_at = now
    session_row.last_worked_at = now
    action_log_row = _record_envelope_patch_action(
        db,
        session_row=session_row,
        candidate_id=projection_candidates[0].id if projection_candidates else None,
        draft_id=(
            projection_candidates[0].draft.id
            if projection_candidates and projection_candidates[0].draft is not None
            else None
        ),
        request=request,
        patch_result=patch_result,
        actor_claims=actor_claims,
        envelope_revision=checkpoint_revision,
    )
    db.add(session_row)
    db.commit()

    refreshed_candidates = load_projection_candidates_for_patch(
        db,
        session_id=normalized_session_id,
        envelope_id=request.envelope_id,
        object_id=request.object_id,
    )
    refreshed_sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    refreshed_session = refreshed_sessions[0] if refreshed_sessions else None
    document_map, user_map = (
        _session_context_maps(db, [refreshed_session])
        if refreshed_session is not None
        else ({}, {})
    )

    return build_envelope_field_patch_response(
        db=db,
        accepted=True,
        envelope_id=request.envelope_id,
        previous_revision=previous_revision,
        envelope_revision=checkpoint_revision,
        object_id=request.object_id,
        object_type=patch_result.object_type,
        field_path=request.field_path,
        operation=request.operation,
        before=patch_result.before,
        value=patch_result.after,
        projection_candidate_ids=projection_candidate_ids,
        history_event_ids=patch_result.history_event_ids,
        candidate=refreshed_candidates[0] if refreshed_candidates else None,
        session=refreshed_session,
        action_log_entry=action_log_row,
        document_map=document_map,
        user_map=user_map,
    )


def waive_validation_finding(
    db: Session,
    session_id: str | UUID,
    request: CurationValidationFindingWaiveRequest,
    actor_claims: dict[str, Any],
) -> CurationValidationFindingWaiveResponse:
    """Apply a curator waiver to one envelope validation finding."""

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
    session_row = sessions[0]

    envelope_row = load_domain_envelope_row_for_patch(db, request.envelope_id)
    if envelope_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Domain envelope {request.envelope_id} not found",
        )
    if envelope_row.session_id is not None and envelope_row.session_id != normalized_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Domain envelope does not belong to the requested session",
        )
    if envelope_row.revision != request.expected_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Domain envelope {request.envelope_id} is at revision "
                f"{envelope_row.revision}, not expected revision {request.expected_revision}."
            ),
        )

    envelope = DomainEnvelope.model_validate(envelope_row.envelope_json)
    finding_index, finding = _validation_finding_by_id(envelope, request.finding_id)
    if finding.status is not ValidationFindingStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Validation finding {request.finding_id} cannot be waived from "
                f"status {finding.status.value}"
            ),
        )
    if not _finding_curator_override_allowed(finding):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Validation finding waiver requires "
                "validation_metadata.curator_override.allowed=true"
            ),
        )

    previous_revision = envelope_row.revision
    actor = _actor_claims_payload(actor_claims)
    target = _finding_review_target(envelope, finding)
    review_action = _curator_validation_override_payload(
        envelope=envelope,
        envelope_revision=previous_revision,
        finding=finding,
        target=target,
        actor=actor,
        comment=request.comment,
    )
    waived_finding = finding.model_copy(
        update={"status": ValidationFindingStatus.WAIVED}
    )
    findings = list(envelope.validation_findings)
    findings[finding_index] = waived_finding
    history_event = _curator_validation_override_history_event(
        envelope=envelope,
        finding=waived_finding,
        target=target,
        actor=actor,
        review_action=review_action,
    )
    patched_envelope = envelope.model_copy(
        update={
            "validation_findings": findings,
            "history": [*envelope.history, history_event],
        }
    )

    checkpoint_revision = _checkpoint_patch_result(
        db,
        envelope_row=envelope_row,
        patched_envelope=patched_envelope,
        expected_revision=previous_revision,
    )

    now = datetime.now(timezone.utc)
    projection_candidate_ids: list[str] = []
    if target["object_id"] is not None:
        projection_candidates = load_projection_candidates_for_patch(
            db,
            session_id=normalized_session_id,
            envelope_id=request.envelope_id,
            object_id=target["object_id"],
        )
        for candidate in projection_candidates:
            projection_candidate_ids.append(str(candidate.id))
            candidate.envelope_revision = checkpoint_revision
            candidate.updated_at = now
            db.add(candidate)

    session_row.session_version += 1
    session_row.updated_at = now
    session_row.last_worked_at = now
    review_action["history_event_id"] = history_event.event_id
    review_action["checkpoint_envelope_revision"] = checkpoint_revision
    review_action["projection_candidate_ids"] = projection_candidate_ids
    action_log_row = SessionActionLogModel(
        session_id=session_row.id,
        candidate_id=(
            UUID(projection_candidate_ids[0])
            if projection_candidate_ids
            else None
        ),
        draft_id=None,
        action_type=CurationActionType.CURATOR_VALIDATION_OVERRIDE,
        actor_type=CurationActorType.USER,
        actor=actor,
        occurred_at=now,
        changed_field_keys=[target["field_path"]] if target["field_path"] else [],
        message="Curator waived validation finding",
        action_metadata={"curator_validation_override": review_action},
        reason=request.comment,
    )
    db.add(session_row)
    db.add(action_log_row)
    db.commit()

    refreshed_sessions = _load_sessions_by_ids(db, [normalized_session_id], detailed=True)
    refreshed_session = refreshed_sessions[0]
    document_map, user_map = _session_context_maps(db, [refreshed_session])
    action_log_entry = _action_log_entry(action_log_row)
    if action_log_entry is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Validation waiver action log could not be serialized",
        )
    return CurationValidationFindingWaiveResponse(
        envelope_id=request.envelope_id,
        previous_revision=previous_revision,
        envelope_revision=checkpoint_revision,
        finding_id=request.finding_id,
        previous_status=finding.status.value,
        new_status=ValidationFindingStatus.WAIVED.value,
        action_log_entry=action_log_entry,
        session=_session_detail(db, refreshed_session, document_map, user_map),
    )


def _validation_finding_by_id(
    envelope: DomainEnvelope,
    finding_id: str,
) -> tuple[int, ValidationFinding]:
    for index, finding in enumerate(envelope.validation_findings):
        if finding.finding_id == finding_id:
            return index, finding
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Validation finding {finding_id} not found in envelope {envelope.envelope_id}",
    )


def _finding_curator_override_allowed(finding: ValidationFinding) -> bool:
    details = finding.details or {}
    if _metadata_allows_curator_override(details):
        return True
    raw_validation_metadata = details.get("validation_metadata")
    return (
        isinstance(raw_validation_metadata, Mapping)
        and _metadata_allows_curator_override(raw_validation_metadata)
    )


def _object_id_by_ref(envelope: DomainEnvelope) -> dict[tuple[str, str], str]:
    object_ids: dict[tuple[str, str], str] = {}
    for domain_object in envelope.objects:
        stable_object_id = _stable_envelope_object_id(domain_object)
        if domain_object.object_id is not None:
            object_ids[("object_id", domain_object.object_id)] = stable_object_id
        if domain_object.pending_ref_id is not None:
            object_ids[("pending_ref_id", domain_object.pending_ref_id)] = stable_object_id
    return object_ids


def _stable_envelope_object_id(domain_object: CuratableObjectEnvelope) -> str:
    if domain_object.object_id is not None:
        return domain_object.object_id
    if domain_object.pending_ref_id is not None:
        return domain_object.pending_ref_id
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Domain envelope object is missing object_id and pending_ref_id",
    )


def _object_ref_payload(object_ref: ObjectRef | None) -> dict[str, str | None]:
    if object_ref is None:
        return {}
    return object_ref.model_dump(mode="json", exclude_none=True)


def _field_ref_payload(field_ref: FieldRef | None) -> dict[str, Any]:
    if field_ref is None:
        return {}
    return field_ref.model_dump(mode="json", exclude_none=True)


def _finding_review_target(
    envelope: DomainEnvelope,
    finding: ValidationFinding,
) -> dict[str, str | None]:
    object_ids = _object_id_by_ref(envelope)
    object_ref = None
    field_path = None
    if finding.field_ref is not None:
        object_ref = finding.field_ref.object_ref
        field_path = finding.field_ref.field_path
    elif finding.object_ref is not None:
        object_ref = finding.object_ref
    object_id = object_ids.get(object_ref.ref_key()) if object_ref is not None else None
    return {
        "object_id": object_id,
        "object_type": object_ref.object_type if object_ref is not None else None,
        "field_path": field_path,
    }


def _validator_metadata(finding: ValidationFinding) -> Mapping[str, Any]:
    raw_metadata = (finding.details or {}).get("validation_metadata")
    return raw_metadata if isinstance(raw_metadata, Mapping) else {}


def _validation_request(finding: ValidationFinding) -> Mapping[str, Any]:
    raw_request = (finding.details or {}).get("validation_request")
    return raw_request if isinstance(raw_request, Mapping) else {}


def _input_fingerprint(finding: ValidationFinding) -> str | None:
    request = _validation_request(finding)
    selected_inputs = request.get("selected_inputs")
    if selected_inputs is None:
        return None
    digest = sha256(
        json.dumps(selected_inputs, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _curator_validation_override_payload(
    *,
    envelope: DomainEnvelope,
    envelope_revision: int,
    finding: ValidationFinding,
    target: Mapping[str, str | None],
    actor: Mapping[str, str],
    comment: str | None,
) -> dict[str, Any]:
    metadata = _validator_metadata(finding)
    request = _validation_request(finding)
    # Finding metadata is the canonical materialized validator identity; request
    # fields are kept only as audit backfill when a validator omitted metadata.
    return {
        "action": "waive_validation_finding",
        "finding_id": finding.finding_id,
        "validator_binding_id": metadata.get("validator_binding_id")
        or request.get("validator_binding_id"),
        "validator_agent": metadata.get("validator_agent")
        or request.get("validator_agent"),
        "validator_run_id": request.get("request_id"),
        "envelope_id": envelope.envelope_id,
        "envelope_revision": envelope_revision,
        "input_fingerprint": _input_fingerprint(finding),
        "target": {
            "object_id": target.get("object_id"),
            "object_type": target.get("object_type"),
            "field_path": target.get("field_path"),
        },
        "previous_status": finding.status.value,
        "new_status": ValidationFindingStatus.WAIVED.value,
        "actor": {
            "actor_type": "human",
            "actor_id": actor.get("actor_id"),
        },
        "comment": comment,
    }


def _curator_validation_override_history_event(
    *,
    envelope: DomainEnvelope,
    finding: ValidationFinding,
    target: Mapping[str, str | None],
    actor: Mapping[str, str],
    review_action: Mapping[str, Any],
) -> HistoryEvent:
    seed_payload = {
        "envelope_id": envelope.envelope_id,
        "envelope_revision": review_action.get("envelope_revision"),
        "finding_id": finding.finding_id,
        "event_type": "curator_validation_override",
        "new_status": ValidationFindingStatus.WAIVED.value,
    }
    digest = sha256(
        json.dumps(seed_payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    object_ref = finding.object_ref
    field_ref = finding.field_ref
    return HistoryEvent(
        event_type=HistoryEventKind.STATUS_CHANGED,
        event_id=f"curator-validation-override:{digest}",
        actor_type=HistoryActorType.HUMAN,
        actor_id=actor.get("actor_id"),
        message="Curator waived validation finding",
        object_ref=None if field_ref is not None else object_ref,
        field_ref=field_ref,
        details={
            "curator_validation_override": dict(review_action),
            "finding_id": finding.finding_id,
            "target": dict(target),
            "previous_status": review_action.get("previous_status"),
            "new_status": review_action.get("new_status"),
            "object_ref": _object_ref_payload(object_ref),
            "field_ref": _field_ref_payload(field_ref),
        },
    )


def _checkpoint_patch_result(
    db: Session,
    *,
    envelope_row: Any,
    patched_envelope: DomainEnvelope,
    expected_revision: int,
) -> int:
    try:
        checkpoint = write_domain_envelope_checkpoint(
            db,
            DomainEnvelopeCheckpointRequest(
                project_key=envelope_row.project_key,
                envelope=patched_envelope,
                expected_revision=expected_revision,
                document_id=envelope_row.document_id,
                session_id=envelope_row.session_id,
                flow_run_id=envelope_row.flow_run_id,
                object_model_ref_json=dict(envelope_row.object_model_ref_json or {}),
                model_field_ref_json=dict(envelope_row.model_field_ref_json or {}),
            ),
        )
    except DomainEnvelopePersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return checkpoint.revision


def _record_envelope_patch_action(
    db: Session,
    *,
    session_row: ReviewSessionModel,
    candidate_id: UUID | None,
    draft_id: UUID | None,
    request: CurationEnvelopeFieldPatchRequest,
    patch_result: Any,
    actor_claims: dict[str, Any],
    envelope_revision: int,
) -> SessionActionLogModel:
    action_log_row = SessionActionLogModel(
        session_id=session_row.id,
        candidate_id=candidate_id,
        draft_id=draft_id,
        action_type=CurationActionType.ENVELOPE_FIELD_PATCHED,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=datetime.now(timezone.utc),
        changed_field_keys=[request.field_path],
        message=(
            "Curator envelope field patch accepted"
            if patch_result.accepted
            else "Curator envelope field patch rejected"
        ),
        action_metadata={
            "envelope_id": request.envelope_id,
            "object_id": request.object_id,
            "object_type": patch_result.object_type,
            "field_path": request.field_path,
            "operation": request.operation.value,
            "expected_revision": request.expected_revision,
            "envelope_revision": envelope_revision,
            "accepted": patch_result.accepted,
            "before": patch_result.before,
            "after": patch_result.after,
            "errors": list(patch_result.errors),
            "history_event_ids": list(patch_result.history_event_ids),
            "reason": request.reason,
        },
    )
    db.add(action_log_row)
    return action_log_row


def _rejected_patch_status_code(patch_result: Any) -> int:
    if any("before does not match" in error for error in patch_result.errors):
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


def _envelope_object_by_stable_id(
    envelope: DomainEnvelope,
    object_id: str,
) -> CuratableObjectEnvelope | None:
    for domain_object in envelope.objects:
        if object_id in {
            value
            for value in (domain_object.object_id, domain_object.pending_ref_id)
            if value is not None
        }:
            return domain_object
    return None


def _refresh_candidate_projection_from_envelope(
    candidate: CurationCandidate,
    domain_object: CuratableObjectEnvelope,
    *,
    envelope_revision: int,
    changed_field_path: str | Sequence[str],
    updated_at: datetime,
) -> None:
    candidate.envelope_revision = envelope_revision
    # Envelope-backed candidates keep semantic truth in persisted envelopes.
    candidate.normalized_payload = {}
    candidate.updated_at = updated_at

    if candidate.draft is None:
        return

    draft_fields = [
        CurationDraftFieldSchema.model_validate(field_payload)
        for field_payload in (candidate.draft.fields or [])
    ]
    changed = False
    for index, draft_field in enumerate(draft_fields):
        projected_value = _projected_value_for_draft_field(
            domain_object.payload,
            draft_field,
        )
        if projected_value is _MISSING:
            continue
        next_field = draft_field.model_copy(
            update={
                "value": copy.deepcopy(projected_value),
                "seed_value": copy.deepcopy(projected_value),
                "dirty": False,
                "stale_validation": (
                    draft_field.stale_validation
                    or _draft_field_matches_any_path(draft_field, changed_field_path)
                ),
            }
        )
        if next_field != draft_field:
            draft_fields[index] = next_field
            changed = True

    if changed:
        candidate.draft.fields = [
            field.model_dump(mode="json")
            for field in draft_fields
        ]
        candidate.draft.version += 1
        candidate.draft.updated_at = updated_at
        candidate.draft.last_saved_at = updated_at


def _projected_value_for_draft_field(
    payload: dict[str, Any],
    draft_field: CurationDraftFieldSchema,
) -> Any:
    for field_path in _draft_field_projection_paths(draft_field):
        value = _payload_value(payload, field_path)
        if value is not _MISSING:
            return value
    return _MISSING


def _draft_field_matches_path(
    draft_field: CurationDraftFieldSchema,
    field_path: str,
) -> bool:
    return field_path in set(_draft_field_projection_paths(draft_field))


def _draft_field_matches_any_path(
    draft_field: CurationDraftFieldSchema,
    field_path: str | Sequence[str],
) -> bool:
    if isinstance(field_path, str):
        return _draft_field_matches_path(draft_field, field_path)
    return any(_draft_field_matches_path(draft_field, path) for path in field_path)


def _draft_field_projection_paths(
    draft_field: CurationDraftFieldSchema,
) -> tuple[str, ...]:
    paths: list[str] = []
    source_field_path = draft_field.metadata.get("source_field_path")
    if isinstance(source_field_path, str) and source_field_path.strip():
        paths.append(source_field_path.strip())
    paths.append(draft_field.field_key)

    expanded: list[str] = []
    for path in paths:
        if path not in expanded:
            expanded.append(path)
        bracket_path = _dot_numeric_path_to_brackets(path)
        if bracket_path not in expanded:
            expanded.append(bracket_path)
    return tuple(expanded)


def _draft_field_materializes_to_paths(
    draft_field: CurationDraftFieldSchema,
) -> tuple[str, ...]:
    raw_paths = draft_field.metadata.get("materializes_to_field_paths")
    if not isinstance(raw_paths, list):
        return ()
    return tuple(
        path.strip()
        for path in raw_paths
        if isinstance(path, str) and path.strip()
    )


def _draft_field_materialized_value(draft_field: CurationDraftFieldSchema) -> Any:
    value = copy.deepcopy(draft_field.value)
    field_type = (draft_field.field_type or "").strip().lower()
    if field_type == "integer":
        return _coerce_integer_draft_value(draft_field.field_key, value)
    return value


def _coerce_integer_draft_value(field_key: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Draft field {field_key} must be an integer",
        )
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Draft field {field_key} must be an integer",
        )
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return int(normalized, 10)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Draft field {field_key} must be an integer",
            ) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Draft field {field_key} must be an integer",
    )


def _ensure_domain_pack_declares_field_path(
    domain_pack: Any,
    *,
    object_type: str,
    field_path: str,
) -> None:
    for object_definition in domain_pack.metadata.object_definitions:
        if object_definition.object_type != object_type:
            continue
        if any(field.field_path == field_path for field in object_definition.fields):
            return
        break
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            f"Materialized draft projection field_path '{field_path}' is not "
            f"declared for object_type '{object_type}'"
        ),
    )


def _replace_envelope_object_payload_value(
    envelope: DomainEnvelope,
    *,
    object_id: str,
    field_path: str,
    value: Any,
) -> DomainEnvelope:
    object_index = None
    domain_object = None
    for index, candidate_object in enumerate(envelope.objects):
        if object_id in {
            value
            for value in (candidate_object.object_id, candidate_object.pending_ref_id)
            if value is not None
        }:
            object_index = index
            domain_object = candidate_object
            break
    if domain_object is None or object_index is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Draft materialization target disappeared from the envelope",
        )

    payload = copy.deepcopy(domain_object.payload)
    try:
        _set_payload_value(payload, field_path, value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    updated_objects = list(envelope.objects)
    updated_objects[object_index] = domain_object.model_copy(
        update={"payload": payload}
    )
    return envelope.model_copy(update={"objects": updated_objects})


def _set_payload_value(payload: dict[str, Any], field_path: str, value: Any) -> None:
    parts = parse_field_path(field_path)
    current: Any = payload
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        if isinstance(part, str):
            if not isinstance(current, dict):
                raise ValueError(f"Cannot set '{field_path}' through non-object parent")
            if part not in current or current[part] is None:
                current[part] = [] if isinstance(next_part, int) else {}
            current = current[part]
            continue
        if not isinstance(current, list) or isinstance(
            current, (str, bytes, bytearray)
        ):
            raise ValueError(f"Cannot set '{field_path}' through non-array parent")
        if part == len(current):
            current.append([] if isinstance(next_part, int) else {})
        if part >= len(current):
            raise ValueError(
                f"Cannot set '{field_path}' because a list index is missing"
            )
        current = current[part]

    final_part = parts[-1]
    if isinstance(final_part, str):
        if not isinstance(current, dict):
            raise ValueError(f"Cannot set '{field_path}' on non-object parent")
        current[final_part] = value
        return

    if not isinstance(current, list) or isinstance(current, (str, bytes, bytearray)):
        raise ValueError(f"Cannot set '{field_path}' on non-array parent")
    if final_part == len(current):
        current.append(value)
        return
    if final_part >= len(current):
        raise ValueError(f"Cannot set '{field_path}' because a list index is missing")
    current[final_part] = value


def _dot_numeric_path_to_brackets(path: str) -> str:
    parts = path.split(".")
    if not parts:
        return path
    converted = parts[0]
    for part in parts[1:]:
        if part.isdigit():
            converted = f"{converted}[{part}]"
        else:
            converted = f"{converted}.{part}"
    return converted


def _payload_value(payload: dict[str, Any], field_path: str) -> Any:
    try:
        parts = parse_field_path(field_path)
    except ValueError:
        return _MISSING
    current: Any = payload
    for part in parts:
        if isinstance(part, str):
            if not isinstance(current, dict) or part not in current:
                return _MISSING
            current = current[part]
            continue
        if (
            not isinstance(current, list)
            or isinstance(current, (str, bytes, bytearray))
            or part >= len(current)
        ):
            return _MISSING
        current = current[part]
    return current


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
