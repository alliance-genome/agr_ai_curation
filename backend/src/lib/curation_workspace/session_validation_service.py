"""Validation behavior for curation workspace sessions and candidates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.lib.curation_workspace.adapter_registry import (
    resolve_curation_domain_envelope_validator_by_id,
    resolve_curation_domain_pack_by_id,
)
from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationReviewSession as ReviewSessionModel,
    DomainEnvelopeModel,
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
    domain_envelope_field_validation_results,
    field_validation_aliases,
    field_validation_status,
    increment_validation_count,
)
from src.lib.domain_envelopes.persistence import (
    DomainEnvelopeCheckpointRequest,
    DomainEnvelopePersistenceError,
    write_domain_envelope_checkpoint,
)
from src.lib.domain_packs.structural_checks import run_domain_envelope_structural_checks
from src.lib.domain_packs.validation_findings import (
    append_validation_findings_to_envelope,
    remove_open_validation_findings_for_scope,
    resolve_stale_validation_findings_after_refresh,
)
from src.lib.domain_packs.validator_dispatch import (
    ValidatorRuntimeContext,
    dispatch_active_validator_bindings,
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
    FieldValidationStatus,
)
from src.schemas.domain_envelope import DomainEnvelope


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
            selectinload(CurationCandidate.domain_envelope),
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

def _validation_result_for_field(
    field: CurationDraftFieldSchema,
    *,
    envelope_results: dict[str, FieldValidationResult] | None = None,
) -> FieldValidationResult:
    if envelope_results is not None:
        envelope_result = envelope_results.get(field.field_key)
        if envelope_result is not None:
            return envelope_result

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
    db: Session,
    candidate: CurationCandidate,
    *,
    force: bool,
    validated_at: datetime,
    runtime_context: ValidatorRuntimeContext | None = None,
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

    snapshot_matches_projection = (
        latest_snapshot is not None
        and latest_snapshot.envelope_id == candidate.envelope_id
        and latest_snapshot.envelope_revision == candidate.envelope_revision
    )

    if (
        not requested_field_keys
        and not force
        and latest_snapshot is not None
        and latest_snapshot.state == CurationValidationSnapshotState.COMPLETED
        and snapshot_matches_projection
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
    snapshot_projection_mismatch = latest_snapshot is not None and not snapshot_matches_projection
    (
        envelope_results,
        envelope_warnings,
        envelope_id,
        envelope_revision,
    ) = _envelope_validation_results_for_candidate(
        db,
        candidate,
        draft_fields=draft_fields,
        validated_at=validated_at,
        runtime_context=runtime_context,
    )
    warnings.extend(envelope_warnings)

    for draft_field in draft_fields:
        existing_result = _existing_result(draft_field)
        field_is_targeted = (
            not requested_field_keys
            or snapshot_projection_mismatch
            or draft_field.field_key in requested_field_keys
        )
        should_refresh = (
            field_is_targeted
            and (
                force
                or draft_field.stale_validation
                or existing_result is None
                or snapshot_missing_or_incomplete
                or snapshot_projection_mismatch
            )
        )
        next_result = (
            _validation_result_for_field(
                draft_field,
                envelope_results=envelope_results,
            )
            if should_refresh
            else existing_result
        )
        if next_result is None:
            next_result = _validation_result_for_field(
                draft_field,
                envelope_results=envelope_results,
            )

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
        envelope_id=envelope_id,
        envelope_revision=envelope_revision,
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
    runtime_context: ValidatorRuntimeContext | None = None,
    field_keys: Sequence[str] | None = None,
) -> tuple[CurationValidationSnapshotSchema, bool]:
    computation = _compute_candidate_validation(
        db,
        candidate,
        force=force,
        validated_at=validated_at,
        runtime_context=runtime_context,
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
    db.refresh(snapshot_row)
    candidate.validation_snapshots.append(snapshot_row)
    return _validation_snapshot(snapshot_row), True


def _envelope_validation_results_for_candidate(
    db: Session,
    candidate: CurationCandidate,
    *,
    draft_fields: Sequence[CurationDraftFieldSchema],
    validated_at: datetime,
    runtime_context: ValidatorRuntimeContext | None = None,
) -> tuple[dict[str, FieldValidationResult] | None, list[str], str | None, int | None]:
    if (
        candidate.envelope_id is None
        or candidate.object_id is None
        or candidate.envelope_revision is None
    ):
        return None, [], None, None

    envelope_row = candidate.domain_envelope
    if envelope_row is None:
        envelope_row = db.get(DomainEnvelopeModel, candidate.envelope_id)
    if envelope_row is None:
        warning = (
            f"Domain envelope {candidate.envelope_id} was unavailable during "
            "candidate validation."
        )
        return (
            {
                field.field_key: FieldValidationResult(
                    status=FieldValidationStatus.CONFLICT,
                    resolver="domain_envelope_validation_findings",
                    warnings=[warning],
                )
                for field in draft_fields
            },
            [warning],
            str(candidate.envelope_id),
            candidate.envelope_revision,
        )

    envelope = DomainEnvelope.model_validate(envelope_row.envelope_json)
    envelope, envelope_revision, dispatch_warnings = _dispatch_workspace_envelope_validation(
        db,
        candidate,
        envelope_row=envelope_row,
        envelope=envelope,
        field_paths=_candidate_validation_field_paths(draft_fields),
        validated_at=validated_at,
        runtime_context=runtime_context,
    )
    field_results, warnings = domain_envelope_field_validation_results(
        envelope,
        envelope_revision=envelope_revision,
        object_id=candidate.object_id,
        field_keys=[field.field_key for field in draft_fields],
        field_aliases_by_key={
            field.field_key: field_validation_aliases(field.field_key, field.metadata)
            for field in draft_fields
        },
    )
    warnings = [*dispatch_warnings, *warnings]
    if candidate.envelope_revision != envelope_row.revision:
        warnings = [
            *warnings,
            (
                f"Domain envelope {candidate.envelope_id} validation used revision "
                f"{envelope_row.revision}; candidate references revision "
                f"{candidate.envelope_revision}."
            ),
        ]
    return field_results, dedupe(warnings), envelope.envelope_id, envelope_revision


def _candidate_validation_field_paths(
    draft_fields: Sequence[CurationDraftFieldSchema],
) -> tuple[str, ...]:
    paths: list[str] = []
    for field in draft_fields:
        for path in field_validation_aliases(field.field_key, field.metadata):
            if path not in paths:
                paths.append(path)
    return tuple(paths)


def _dispatch_workspace_envelope_validation(
    db: Session,
    candidate: CurationCandidate,
    *,
    envelope_row: DomainEnvelopeModel,
    envelope: DomainEnvelope,
    field_paths: Sequence[str],
    validated_at: datetime,
    runtime_context: ValidatorRuntimeContext | None = None,
) -> tuple[DomainEnvelope, int, list[str]]:
    domain_pack = resolve_curation_domain_pack_by_id(envelope.domain_pack_id)
    if domain_pack is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Domain pack {envelope.domain_pack_id} is not available for "
                "workspace candidate validation."
            ),
        )

    source_revision = int(envelope_row.revision)
    refresh_scope = remove_open_validation_findings_for_scope(
        envelope,
        object_id=candidate.object_id,
        field_paths=field_paths,
    )
    structural_result = run_domain_envelope_structural_checks(
        refresh_scope.envelope,
        domain_pack,
    )
    package_validator = resolve_curation_domain_envelope_validator_by_id(
        envelope.domain_pack_id
    )
    package_appended_findings = ()
    validator_envelope = structural_result.envelope
    if package_validator is not None:
        validator_envelope, package_appended_findings = (
            append_validation_findings_to_envelope(
                structural_result.envelope,
                package_validator(structural_result.envelope),
                actor_id=f"{envelope.domain_pack_id}.domain_envelope_validator",
            )
        )
    dispatch_result = dispatch_active_validator_bindings(
        validator_envelope,
        domain_pack,
        actor_id="workspace_candidate_validation",
        registry=structural_result.registry,
        source_envelope_revision=source_revision,
        runtime_context=runtime_context,
    )
    stale_resolution = resolve_stale_validation_findings_after_refresh(
        original_envelope=envelope,
        refreshed_envelope=dispatch_result.envelope,
        removed_findings=refresh_scope.removed_findings,
        actor_id="workspace_candidate_validation",
    )
    appended_findings = (
        *structural_result.appended_findings,
        *package_appended_findings,
        *dispatch_result.appended_findings,
        *stale_resolution.resolved_findings,
    )
    if not appended_findings and not stale_resolution.changed:
        return stale_resolution.envelope, source_revision, []

    try:
        checkpoint = write_domain_envelope_checkpoint(
            db,
            DomainEnvelopeCheckpointRequest(
                project_key=envelope_row.project_key,
                envelope=stale_resolution.envelope,
                expected_revision=source_revision,
                document_id=envelope_row.document_id,
                session_id=envelope_row.session_id,
                flow_run_id=envelope_row.flow_run_id,
                object_model_ref_json=dict(envelope_row.object_model_ref_json),
                model_field_ref_json=dict(envelope_row.model_field_ref_json),
            ),
            manage_transaction=False,
        )
    except DomainEnvelopePersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    candidate.envelope_revision = checkpoint.revision
    candidate.updated_at = validated_at
    return stale_resolution.envelope, checkpoint.revision, []


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
        envelope_id=snapshot.envelope_id,
        envelope_revision=snapshot.envelope_revision,
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
    *,
    user_id: str | None = None,
) -> CurationCandidateValidationResponse:
    """Validate a candidate within the transaction owned by the caller.

    The supplied session is flushed but never committed or rolled back here.
    """

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
    runtime_context = _validator_runtime_context_for_candidate(
        candidate,
        user_id=user_id,
    )
    validation_snapshot, changed = _apply_candidate_validation(
        db,
        candidate,
        force=request.force,
        validated_at=validated_at,
        runtime_context=runtime_context,
        field_keys=request.field_keys,
    )
    if changed:
        candidate.session.updated_at = validated_at
        db.add(candidate.session)
        db.add(candidate)
        db.add(candidate.draft)
        db.flush()

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
    *,
    user_id: str | None = None,
) -> CurationSessionValidationResponse:
    """Validate a review session within the transaction owned by the caller.

    The supplied session is flushed but never committed or rolled back here.
    """

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
        runtime_context = _validator_runtime_context_for_candidate(
            candidate_map[target_candidate_id],
            user_id=user_id,
        )
        validation_snapshot, candidate_changed = _apply_candidate_validation(
            db,
            candidate_map[target_candidate_id],
            force=request.force,
            validated_at=validated_at,
            runtime_context=runtime_context,
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
        db.flush()
        session_validation = _validation_snapshot(session_snapshot_row)
    else:
        if changed:
            db.flush()
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


def _validator_runtime_context_for_candidate(
    candidate: CurationCandidate,
    *,
    user_id: str | None,
) -> ValidatorRuntimeContext | None:
    document_id: str | None = None
    if candidate.domain_envelope is not None and candidate.domain_envelope.document_id is not None:
        document_id = str(candidate.domain_envelope.document_id)
    elif candidate.session is not None and candidate.session.document_id is not None:
        document_id = str(candidate.session.document_id)

    if not document_id or not user_id:
        return None
    return ValidatorRuntimeContext(document_id=document_id, user_id=str(user_id))

__all__ = [
    "validate_candidate",
    "validate_session",
]
