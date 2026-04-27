"""Validation behavior for curation workspace sessions and candidates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationReviewSession as ReviewSessionModel,
)
from src.lib.curation_workspace.session_common import _latest_snapshot_record, _normalize_uuid
from src.lib.curation_workspace.session_loading import DETAIL_LOAD_OPTIONS
from src.lib.curation_workspace.session_persistence import _validation_snapshot_row
from src.lib.curation_workspace.session_queries import _session_context_maps
from src.lib.curation_workspace.session_serializers import (
    _candidate_payload,
    _session_detail,
    _validation_snapshot,
)
from src.lib.curation_workspace.session_types import (
    CandidateValidationComputation,
    PreparedValidationSnapshotInput,
)
from src.lib.curation_workspace.validation_runtime import (
    dedupe,
    field_validation_status,
    increment_validation_count,
)
from src.schemas.curation_workspace import (
    CurationCandidateValidationRequest,
    CurationCandidateValidationResponse,
    CurationDraftField as CurationDraftFieldSchema,
    CurationSessionValidationRequest,
    CurationSessionValidationResponse,
    CurationValidationCounts,
    CurationValidationScope,
    CurationValidationSnapshot as CurationValidationSnapshotSchema,
    CurationValidationSnapshotState,
    CurationValidationSummary,
    FieldValidationResult,
)

def _load_candidate_for_write(
    db: Session,
    *,
    session_id: str | UUID,
    candidate_id: str | UUID,
) -> CurationCandidate:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    candidate = db.scalars(
        select(CurationCandidate)
        .where(CurationCandidate.id == normalized_candidate_id)
        .where(CurationCandidate.session_id == normalized_session_id)
        .options(
            selectinload(CurationCandidate.session).selectinload(
                ReviewSessionModel.validation_snapshots
            ),
            selectinload(CurationCandidate.draft),
            selectinload(CurationCandidate.evidence_anchors),
            selectinload(CurationCandidate.validation_snapshots),
        )
    ).first()
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
    return candidate


def _load_session_for_validation(
    db: Session,
    *,
    session_id: str | UUID,
) -> ReviewSessionModel:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    session_row = db.scalars(
        select(ReviewSessionModel)
        .where(ReviewSessionModel.id == normalized_session_id)
        .options(*DETAIL_LOAD_OPTIONS)
    ).first()
    if session_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )
    return session_row

def _validation_result_for_field(field: CurationDraftFieldSchema) -> FieldValidationResult:
    if field.dirty:
        return FieldValidationResult(
            status="overridden",
            resolver="curator_override",
            warnings=["Curator value differs from the AI-seeded draft."],
        )

    field_status, field_warnings = field_validation_status(field.value)
    return FieldValidationResult(
        status=field_status,
        resolver="deterministic_structural_validation",
        warnings=field_warnings,
    )


def _compute_candidate_validation(
    candidate: CurationCandidate,
    *,
    force: bool,
    validated_at: datetime,
    field_keys: Sequence[str] | None = None,
) -> CandidateValidationComputation:
    requested_field_keys = set(field_keys or [])
    draft_fields = [
        CurationDraftFieldSchema.model_validate(field_payload)
        for field_payload in (candidate.draft.fields or [])
    ]
    latest_snapshot = _latest_snapshot_record(candidate.validation_snapshots)
    latest_results = (
        {
            field_key: FieldValidationResult.model_validate(result_payload)
            for field_key, result_payload in (latest_snapshot.field_results or {}).items()
        }
        if latest_snapshot is not None
        else {}
    )

    def _existing_result(field: CurationDraftFieldSchema) -> FieldValidationResult | None:
        return latest_results.get(field.field_key) or field.validation_result

    if (
        not requested_field_keys
        and not force
        and latest_snapshot is not None
        and latest_snapshot.state == CurationValidationSnapshotState.COMPLETED
        and all(
            not field.stale_validation and _existing_result(field) is not None
            for field in draft_fields
        )
    ):
        return CandidateValidationComputation(existing_snapshot=latest_snapshot)

    counts = CurationValidationCounts()
    warnings: list[str] = []
    field_results: dict[str, FieldValidationResult] = {}
    updated_fields: list[dict[str, Any]] = []
    stale_field_keys: list[str] = []
    snapshot_missing_or_incomplete = (
        latest_snapshot is None
        or latest_snapshot.state != CurationValidationSnapshotState.COMPLETED
    )

    for draft_field in draft_fields:
        existing_result = _existing_result(draft_field)
        field_is_targeted = (
            not requested_field_keys
            or draft_field.field_key in requested_field_keys
        )
        should_refresh = (
            field_is_targeted
            and (
                force
                or draft_field.stale_validation
                or existing_result is None
                or snapshot_missing_or_incomplete
            )
        )
        next_result = (
            _validation_result_for_field(draft_field)
            if should_refresh
            else existing_result
        )
        if next_result is None:
            next_result = _validation_result_for_field(draft_field)

        field_results[draft_field.field_key] = next_result
        increment_validation_count(counts, next_result.status)
        warnings.extend(next_result.warnings)
        next_field = (
            draft_field.model_copy(
                update={
                    "stale_validation": False,
                    "validation_result": next_result,
                }
            )
            if field_is_targeted
            else draft_field
        )
        if next_field.stale_validation:
            stale_field_keys.append(next_field.field_key)
        updated_fields.append(next_field.model_dump(mode="json"))

    snapshot = PreparedValidationSnapshotInput(
        scope=CurationValidationScope.CANDIDATE,
        state=CurationValidationSnapshotState.COMPLETED,
        summary=CurationValidationSummary(
            state=CurationValidationSnapshotState.COMPLETED,
            counts=counts,
            last_validated_at=validated_at,
            stale_field_keys=stale_field_keys,
            warnings=dedupe(warnings),
        ),
        field_results=field_results,
        warnings=dedupe(warnings),
        requested_at=validated_at,
        completed_at=validated_at,
        adapter_key=candidate.adapter_key,
    )

    return CandidateValidationComputation(
        snapshot=snapshot,
        updated_fields=updated_fields,
    )


def _apply_candidate_validation(
    db: Session,
    candidate: CurationCandidate,
    *,
    force: bool,
    validated_at: datetime,
    field_keys: Sequence[str] | None = None,
) -> tuple[CurationValidationSnapshotSchema, bool]:
    computation = _compute_candidate_validation(
        candidate,
        force=force,
        validated_at=validated_at,
        field_keys=field_keys,
    )
    if computation.existing_snapshot is not None:
        return _validation_snapshot(computation.existing_snapshot), False

    if computation.snapshot is None or computation.updated_fields is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Candidate {candidate.id} validation could not be materialized",
        )

    candidate.draft.fields = computation.updated_fields
    candidate.draft.updated_at = validated_at
    candidate.updated_at = validated_at

    snapshot_row = _validation_snapshot_row(
        session_row=candidate.session,
        candidate_id=candidate.id,
        snapshot=computation.snapshot,
    )
    db.add(snapshot_row)
    db.flush()
    candidate.validation_snapshots.append(snapshot_row)
    return _validation_snapshot(snapshot_row), True


def _aggregate_session_validation_snapshot(
    *,
    session_row: ReviewSessionModel,
    candidate_validations: Sequence[CurationValidationSnapshotSchema],
    validated_at: datetime,
) -> PreparedValidationSnapshotInput:
    counts = CurationValidationCounts()
    warnings: list[str] = []

    for candidate_validation in candidate_validations:
        candidate_counts = candidate_validation.summary.counts
        counts.validated += candidate_counts.validated
        counts.ambiguous += candidate_counts.ambiguous
        counts.not_found += candidate_counts.not_found
        counts.invalid_format += candidate_counts.invalid_format
        counts.conflict += candidate_counts.conflict
        counts.skipped += candidate_counts.skipped
        counts.overridden += candidate_counts.overridden
        warnings.extend(candidate_validation.warnings)
        warnings.extend(candidate_validation.summary.warnings)

    deduped_warnings = dedupe(warnings)
    return PreparedValidationSnapshotInput(
        scope=CurationValidationScope.SESSION,
        state=CurationValidationSnapshotState.COMPLETED,
        summary=CurationValidationSummary(
            state=CurationValidationSnapshotState.COMPLETED,
            counts=counts,
            last_validated_at=validated_at,
            stale_field_keys=[],
            warnings=deduped_warnings,
        ),
        field_results={},
        warnings=deduped_warnings,
        requested_at=validated_at,
        completed_at=validated_at,
        adapter_key=session_row.adapter_key,
    )


def _prepared_validation_snapshot_schema(
    *,
    session_id: UUID,
    candidate_id: UUID | None,
    snapshot: PreparedValidationSnapshotInput,
) -> CurationValidationSnapshotSchema:
    return CurationValidationSnapshotSchema(
        snapshot_id=str(uuid4()),
        scope=snapshot.scope,
        session_id=str(session_id),
        candidate_id=str(candidate_id) if candidate_id is not None else None,
        adapter_key=snapshot.adapter_key,
        state=snapshot.state,
        field_results=snapshot.field_results,
        summary=snapshot.summary,
        requested_at=snapshot.requested_at,
        completed_at=snapshot.completed_at,
        warnings=list(snapshot.warnings),
    )

def validate_candidate(
    db: Session,
    candidate_id: str | UUID,
    request: CurationCandidateValidationRequest,
) -> CurationCandidateValidationResponse:
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    request_candidate_id = _normalize_uuid(request.candidate_id, field_name="candidate_id")
    if normalized_candidate_id != request_candidate_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path candidate_id does not match request body candidate_id",
        )

    candidate = _load_candidate_for_write(
        db,
        session_id=request.session_id,
        candidate_id=normalized_candidate_id,
    )
    available_field_keys = {
        field_payload.get("field_key")
        for field_payload in (candidate.draft.fields or [])
        if isinstance(field_payload, dict)
    }
    unknown_field_keys = [
        field_key
        for field_key in request.field_keys
        if field_key not in available_field_keys
    ]
    if unknown_field_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown draft field(s): {', '.join(sorted(unknown_field_keys))}",
        )

    validated_at = datetime.now(timezone.utc)
    validation_snapshot, changed = _apply_candidate_validation(
        db,
        candidate,
        force=request.force,
        validated_at=validated_at,
        field_keys=request.field_keys,
    )
    if changed:
        candidate.session.updated_at = validated_at
        db.add(candidate.session)
        db.add(candidate)
        db.add(candidate.draft)
        db.commit()

    updated_candidate = _load_candidate_for_write(
        db,
        session_id=request.session_id,
        candidate_id=normalized_candidate_id,
    )
    return CurationCandidateValidationResponse(
        candidate=_candidate_payload(updated_candidate),
        validation_snapshot=validation_snapshot,
    )


def validate_session(
    db: Session,
    session_id: str | UUID,
    request: CurationSessionValidationRequest,
) -> CurationSessionValidationResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )
    session_row = _load_session_for_validation(db, session_id=normalized_session_id)
    candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
    target_candidate_ids = request.candidate_ids or list(candidate_map.keys())
    unknown_candidate_ids = [
        candidate_id
        for candidate_id in target_candidate_ids
        if candidate_id not in candidate_map
    ]
    if unknown_candidate_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown candidate(s) for session: {', '.join(sorted(unknown_candidate_ids))}",
        )

    validated_at = datetime.now(timezone.utc)
    candidate_validations: list[CurationValidationSnapshotSchema] = []
    changed = False

    for target_candidate_id in target_candidate_ids:
        validation_snapshot, candidate_changed = _apply_candidate_validation(
            db,
            candidate_map[target_candidate_id],
            force=request.force,
            validated_at=validated_at,
        )
        candidate_validations.append(validation_snapshot)
        changed |= candidate_changed

    session_snapshot_input = _aggregate_session_validation_snapshot(
        session_row=session_row,
        candidate_validations=candidate_validations,
        validated_at=validated_at,
    )

    if not request.candidate_ids:
        session_snapshot_row = _validation_snapshot_row(
            session_row=session_row,
            candidate_id=None,
            snapshot=session_snapshot_input,
        )
        session_row.updated_at = validated_at
        db.add(session_snapshot_row)
        db.add(session_row)
        db.commit()
        session_validation = _validation_snapshot(session_snapshot_row)
    else:
        if changed:
            db.commit()
        session_validation = _prepared_validation_snapshot_schema(
            session_id=session_row.id,
            candidate_id=None,
            snapshot=session_snapshot_input,
        )

    updated_session = _load_session_for_validation(db, session_id=normalized_session_id)
    document_map, user_map = _session_context_maps(db, [updated_session])
    return CurationSessionValidationResponse(
        session=_session_detail(db, updated_session, document_map, user_map),
        session_validation=session_validation,
        candidate_validations=candidate_validations,
    )

__all__ = [
    "validate_candidate",
    "validate_session",
]
