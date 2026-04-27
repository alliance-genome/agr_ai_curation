"""Submission preview, execution, retry, and history service."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.curation_workspace.export_adapters import build_default_export_adapter_registry
from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate,
    CurationReviewSession as ReviewSessionModel,
    CurationSubmissionRecord as SubmissionModel,
)
from src.lib.curation_workspace.session_common import (
    _actor_claims_payload,
    _normalize_uuid,
    _normalized_optional_string,
)
from src.lib.curation_workspace.session_queries import get_session_detail
from src.lib.curation_workspace.session_serializers import (
    _action_log_entry,
    _candidate_payload,
    _document_ref,
    _draft_detail,
    _serialize_submission_payload_contract,
    _submission_payload,
    _submission_payload_model_input,
    _submission_record,
)
from src.lib.curation_workspace.session_validation_service import (
    _load_session_for_validation,
    validate_session,
)
from src.lib.curation_workspace.submission_adapters import (
    DIRECT_SUBMISSION_RESULT_STATUSES,
    SubmissionTransportAdapter,
    SubmissionTransportError,
    SubmissionTransportResult,
    build_default_submission_adapter_registry,
    coerce_submission_transport_result,
    normalize_submission_transport_result,
)
from src.lib.curation_workspace.validation_runtime import dedupe
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationActionLogEntry,
    CurationActionType,
    CurationActorType,
    CurationCandidateStatus,
    CurationCandidateSubmissionReadiness,
    CurationDraftField as CurationDraftFieldSchema,
    CurationSessionStatus,
    CurationSessionValidationRequest,
    CurationSubmissionExecuteRequest,
    CurationSubmissionExecuteResponse,
    CurationSubmissionHistoryResponse,
    CurationSubmissionPreviewRequest,
    CurationSubmissionPreviewResponse,
    CurationSubmissionRecord,
    CurationSubmissionRetryRequest,
    CurationSubmissionRetryResponse,
    CurationSubmissionStatus,
    CurationValidationSnapshot as CurationValidationSnapshotSchema,
    FieldValidationResult,
    SubmissionDomainAdapter,
    SubmissionMode,
    SubmissionPayloadContract,
)

logger = logging.getLogger(__name__)
SUBMISSION_TRANSPORT_FAILURE_MESSAGE = "Submission failed unexpectedly. Please try again."

@lru_cache(maxsize=1)
def _export_adapter_registry():
    return build_default_export_adapter_registry()


@lru_cache(maxsize=1)
def _submission_adapter_registry():
    return build_default_submission_adapter_registry()

def _load_submission_record(
    db: Session,
    *,
    session_id: str | UUID,
    submission_id: str | UUID,
) -> SubmissionModel:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_submission_id = _normalize_uuid(submission_id, field_name="submission_id")
    submission_row = db.scalars(
        select(SubmissionModel)
        .where(SubmissionModel.id == normalized_submission_id)
        .where(SubmissionModel.session_id == normalized_session_id)
    ).first()
    if submission_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Curation submission {normalized_submission_id} not found in session "
                f"{normalized_session_id}"
            ),
        )
    return submission_row

def _submission_validation_blocking_reason(
    field: CurationDraftFieldSchema | None,
    validation_result: FieldValidationResult,
) -> str | None:
    if _field_validation_is_warning_only(field):
        return None

    field_label = field.label if field is not None else "A submission field"

    if validation_result.status == "invalid_format":
        return f"{field_label} is empty or invalid."
    if validation_result.status == "ambiguous":
        return f"{field_label} is still ambiguous."
    if validation_result.status == "not_found":
        return f"{field_label} could not be resolved."
    if validation_result.status == "conflict":
        return f"{field_label} has conflicting validation results."

    return None


def _field_validation_is_warning_only(
    field: CurationDraftFieldSchema | None,
) -> bool:
    if field is None:
        return False

    validation_config = field.metadata.get("validation")
    if not isinstance(validation_config, Mapping):
        return False

    severity = validation_config.get("severity")
    return isinstance(severity, str) and severity.strip().lower() == "warning"


def _candidate_submission_readiness(
    candidate: CurationCandidate,
    validation_snapshot: CurationValidationSnapshotSchema | None,
) -> CurationCandidateSubmissionReadiness:
    draft = _draft_detail(candidate.draft)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curation candidate {candidate.id} is missing its draft payload",
        )

    blocking_reasons: list[str] = []
    warnings: list[str] = []

    if candidate.status == CurationCandidateStatus.PENDING:
        blocking_reasons.append("Candidate is still pending curator review.")
    elif candidate.status == CurationCandidateStatus.REJECTED:
        blocking_reasons.append("Candidate was rejected and is excluded from submission.")
    elif candidate.status != CurationCandidateStatus.ACCEPTED:
        blocking_reasons.append(
            f"Candidate status {candidate.status.value} is not eligible for submission."
        )

    field_map = {
        field.field_key: field
        for field in draft.fields
    }
    field_results = (
        validation_snapshot.field_results
        if validation_snapshot is not None
        else {}
    )
    for field_key, validation_result in field_results.items():
        blocking_reason = _submission_validation_blocking_reason(
            field_map.get(field_key),
            validation_result,
        )
        if blocking_reason is not None:
            blocking_reasons.append(blocking_reason)
        warnings.extend(validation_result.warnings)

    return CurationCandidateSubmissionReadiness(
        candidate_id=str(candidate.id),
        ready=candidate.status == CurationCandidateStatus.ACCEPTED and not blocking_reasons,
        blocking_reasons=dedupe(blocking_reasons),
        warnings=dedupe(warnings),
    )


def _submission_candidate_bundle(
    candidate: CurationCandidate,
) -> dict[str, Any]:
    draft = _draft_detail(candidate.draft)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curation candidate {candidate.id} is missing its draft payload",
        )

    return {
        "candidate_id": str(candidate.id),
        "adapter_key": candidate.adapter_key,
        "display_label": candidate.display_label,
        "secondary_label": candidate.secondary_label,
        "fields": {
            field.field_key: field.value
            for field in draft.fields
        },
        "draft_fields": [
            field.model_dump(mode="json")
            for field in draft.fields
        ],
        "metadata": dict(candidate.candidate_metadata or {}),
        "normalized_payload": dict(candidate.normalized_payload or {}),
    }


class _SharedSubmissionPreviewAdapter:
    """Default adapter-owned payload builder used when no custom builder is registered yet."""

    def __init__(self, adapter_key: str) -> None:
        self.adapter_key = adapter_key
        self.supported_submission_modes = tuple(SubmissionMode)
        self.supported_target_keys: tuple[str, ...] = ()

    def build_submission_payload(
        self,
        *,
        mode: SubmissionMode,
        target_key: str,
        payload_context: Mapping[str, Any],
    ) -> SubmissionPayloadContract:
        payload_json: dict[str, Any] = {
            "session_id": payload_context["session_id"],
            "adapter_key": self.adapter_key,
            "mode": mode.value,
            "target_key": target_key,
            "candidate_count": payload_context["candidate_count"],
            "candidates": payload_context["candidates"],
        }
        document = payload_context.get("document")
        if document is not None:
            payload_json["document"] = document
        session_validation = payload_context.get("session_validation")
        if session_validation is not None:
            payload_json["session_validation"] = session_validation

        payload_kwargs: dict[str, Any] = {
            "mode": mode,
            "target_key": target_key,
            "adapter_key": self.adapter_key,
            "candidate_ids": payload_context["candidate_ids"],
            "payload_json": payload_json,
            "warnings": payload_context["warnings"],
        }

        return SubmissionPayloadContract(**payload_kwargs)


def _resolve_submission_domain_adapter(adapter_key: str) -> SubmissionDomainAdapter:
    return _SharedSubmissionPreviewAdapter(adapter_key)


def _default_submission_target_key(adapter_key: str) -> str:
    return f"{adapter_key}.default"


def _resolve_submission_preview_target_key(
    *,
    adapter_key: str,
    requested_target_key: str | None,
) -> tuple[SubmissionDomainAdapter, str]:
    submission_adapter = _resolve_submission_domain_adapter(adapter_key)
    supported_target_keys = tuple(submission_adapter.supported_target_keys or ())

    if requested_target_key:
        if supported_target_keys and requested_target_key not in supported_target_keys:
            supported_targets = ", ".join(supported_target_keys)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unsupported submission target '{requested_target_key}' for "
                    f"adapter '{adapter_key}'. Supported targets: {supported_targets}"
                ),
            )

        return submission_adapter, requested_target_key

    if supported_target_keys:
        return submission_adapter, supported_target_keys[0]

    # Keep the shared substrate target-agnostic even before adapters publish
    # explicit target identifiers for preview/export flows.
    return submission_adapter, _default_submission_target_key(adapter_key)


def _resolve_export_preview_target_key(
    *,
    adapter_key: str,
    requested_target_key: str | None,
):
    export_adapter = _resolve_export_adapter(adapter_key)
    supported_target_keys = tuple(export_adapter.supported_target_keys or ())

    if requested_target_key:
        if supported_target_keys and requested_target_key not in supported_target_keys:
            supported_targets = ", ".join(supported_target_keys)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unsupported export target '{requested_target_key}' for "
                    f"adapter '{adapter_key}'. Supported targets: {supported_targets}"
                ),
            )

        return export_adapter, requested_target_key

    if supported_target_keys:
        return export_adapter, supported_target_keys[0]

    return export_adapter, _default_submission_target_key(adapter_key)


def _resolve_export_adapter(adapter_key: str):
    export_adapter = _export_adapter_registry().get(adapter_key)
    if export_adapter is not None:
        return export_adapter

    # Keep direct-submit payload building aligned with the shared submission
    # contract while domain-specific export adapters continue to roll out.
    return _resolve_submission_domain_adapter(adapter_key)


def _resolve_submission_transport_adapter(target_key: str) -> SubmissionTransportAdapter:
    try:
        return _submission_adapter_registry().require(target_key)
    except KeyError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submission target is not configured",
            log_message=f"Unknown submission target requested: {target_key}",
            exc=exc,
            level=logging.WARNING,
        )


def _base_submission_payload_context(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
) -> dict[str, Any]:
    document = db.get(PDFDocument, session_row.document_id)
    warnings: list[str] = []
    if not ready_candidates:
        warnings.append("No accepted candidates are ready for submission.")

    return {
        "session_id": str(session_row.id),
        "document": (
            _document_ref(document).model_dump(mode="json")
            if document is not None
            else None
        ),
        "session_validation": (
            session_validation.model_dump(mode="json")
            if session_validation is not None
            else None
        ),
        "warnings": dedupe(warnings),
    }


def _submission_payload_context(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
) -> dict[str, Any]:
    payload_context = _base_submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
    )

    return {
        **payload_context,
        "candidate_ids": [str(candidate.id) for candidate in ready_candidates],
        "candidate_count": len(ready_candidates),
        "candidates": [
            _submission_candidate_bundle(candidate)
            for candidate in ready_candidates
        ],
    }


def _export_submission_payload_context(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
) -> dict[str, Any]:
    payload_context = _base_submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
    )
    export_candidates = [
        _candidate_payload(candidate).model_dump(mode="json")
        for candidate in ready_candidates
    ]

    return {
        **payload_context,
        "candidate_ids": [candidate["candidate_id"] for candidate in export_candidates],
        "candidate_count": len(export_candidates),
        "candidates": export_candidates,
    }


def _build_submission_preview_payload(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    submission_adapter: SubmissionDomainAdapter,
    mode: SubmissionMode,
    target_key: str,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
) -> SubmissionPayloadContract:
    payload_context = _submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
    )
    return submission_adapter.build_submission_payload(
        mode=mode,
        target_key=target_key,
        payload_context=payload_context,
    )


def _build_submission_execute_payload(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    mode: SubmissionMode,
    target_key: str,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
    adapter_key: str | None = None,
) -> SubmissionPayloadContract:
    export_adapter = _resolve_export_adapter(adapter_key or session_row.adapter_key)
    payload_context = _export_submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
    )

    try:
        return export_adapter.build_submission_payload(
            mode=mode,
            target_key=target_key,
            payload_context=payload_context,
        )
    except ValueError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submission payload could not be built",
            log_message=f"Submission payload build failed for target {target_key}",
            exc=exc,
            level=logging.WARNING,
        )


def _coerce_failed_submission_result(
    *,
    adapter: SubmissionTransportAdapter,
    error: Exception,
) -> SubmissionTransportResult:
    if isinstance(error, SubmissionTransportError):
        return error.to_result()

    return normalize_submission_transport_result(
        status=CurationSubmissionStatus.FAILED,
        response_message=SUBMISSION_TRANSPORT_FAILURE_MESSAGE,
    )


def _submission_attempt_marks_session_submitted(status_value: CurationSubmissionStatus) -> bool:
    return status_value in {
        CurationSubmissionStatus.ACCEPTED,
        CurationSubmissionStatus.QUEUED,
        CurationSubmissionStatus.MANUAL_REVIEW_REQUIRED,
    }


def _submission_action_message(
    *,
    result_status: CurationSubmissionStatus,
    target_key: str,
) -> str:
    return (
        f"Submission to target '{target_key}' completed with status "
        f"'{result_status.value}'"
    )


def _submission_candidate_ids(record: SubmissionModel) -> list[str]:
    payload = _submission_payload(record)
    if payload is not None and payload.candidate_ids:
        return dedupe(payload.candidate_ids)

    return dedupe(
        [
            candidate_id
            for readiness_item in (record.readiness or [])
            if isinstance(readiness_item, dict)
            and readiness_item.get("ready") is True
            and isinstance(candidate_id := readiness_item.get("candidate_id"), str)
            and candidate_id
        ]
    )


def _execute_direct_submission_attempt(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    adapter_key: str,
    mode: SubmissionMode,
    target_key: str,
    payload: SubmissionPayloadContract,
    readiness: Sequence[CurationCandidateSubmissionReadiness],
    actor_claims: dict[str, Any],
    action_type: CurationActionType,
    action_metadata: Mapping[str, Any] | None = None,
) -> tuple[CurationSubmissionRecord, CurationActionLogEntry]:
    transport_adapter = _resolve_submission_transport_adapter(target_key)
    requested_at = datetime.now(timezone.utc)
    try:
        result = coerce_submission_transport_result(
            transport_adapter.submit(payload=payload)
        )
    except Exception as exc:
        logger.exception(
            "Submission transport adapter '%s' failed for session '%s' and target '%s'",
            transport_adapter.transport_key,
            str(session_row.id),
            target_key,
        )
        result = _coerce_failed_submission_result(
            adapter=transport_adapter,
            error=exc,
        )

    if result.status not in DIRECT_SUBMISSION_RESULT_STATUSES:
        result = normalize_submission_transport_result(
            status=CurationSubmissionStatus.FAILED,
            response_message=(
                f"Submission adapter '{transport_adapter.transport_key}' returned "
                f"unsupported direct-submit status '{result.status.value}'"
            ),
            warnings=result.warnings,
        )

    completed_at = result.completed_at or requested_at
    combined_warnings = dedupe([*payload.warnings, *result.warnings])
    submission_row = SubmissionModel(
        session_id=session_row.id,
        adapter_key=adapter_key,
        mode=mode,
        target_key=target_key,
        status=result.status,
        readiness=[item.model_dump(mode="json") for item in readiness],
        payload=_serialize_submission_payload_contract(payload),
        external_reference=result.external_reference,
        response_message=result.response_message,
        validation_errors=list(result.validation_errors),
        warnings=combined_warnings,
        requested_at=requested_at,
        completed_at=completed_at,
    )

    previous_session_status = session_row.status
    if _submission_attempt_marks_session_submitted(result.status):
        session_row.status = CurationSessionStatus.SUBMITTED
        if session_row.submitted_at is None:
            session_row.submitted_at = completed_at
    session_row.updated_at = completed_at
    session_row.last_worked_at = completed_at
    session_row.session_version += 1

    action_log_payload = {
        "target_key": target_key,
        "mode": mode.value,
        "submission_status": result.status.value,
        "submitted_candidate_ids": list(payload.candidate_ids),
        "submitted_candidate_count": len(payload.candidate_ids),
        "external_reference": result.external_reference,
        "validation_error_count": len(result.validation_errors),
    }
    if action_metadata:
        action_log_payload.update(dict(action_metadata))

    action_log_row = SessionActionLogModel(
        session_id=session_row.id,
        action_type=action_type,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=completed_at,
        previous_session_status=(
            previous_session_status if previous_session_status != session_row.status else None
        ),
        new_session_status=(
            session_row.status if previous_session_status != session_row.status else None
        ),
        message=_submission_action_message(
            result_status=result.status,
            target_key=target_key,
        ),
        action_metadata=action_log_payload,
    )

    db.add(session_row)
    db.add(submission_row)
    db.add(action_log_row)
    db.flush()

    response_submission = _submission_record(submission_row).model_copy(
        update={
            "payload": payload,
            "warnings": combined_warnings,
        }
    )

    db.commit()
    action_log_entry = _action_log_entry(action_log_row)
    if action_log_entry is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Submission action log entry could not be serialized",
        )

    return response_submission, action_log_entry

def submission_preview(
    db: Session,
    session_id: str | UUID,
    request: CurationSubmissionPreviewRequest,
) -> CurationSubmissionPreviewResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )

    validation_response = validate_session(
        db,
        normalized_session_id,
        CurationSessionValidationRequest(
            session_id=request.session_id,
            candidate_ids=request.candidate_ids,
            force=False,
        ),
    )

    session_row = _load_session_for_validation(db, session_id=normalized_session_id)
    submission_adapter = None
    if request.mode == SubmissionMode.EXPORT:
        _, target_key = _resolve_export_preview_target_key(
            adapter_key=session_row.adapter_key,
            requested_target_key=request.target_key,
        )
    else:
        submission_adapter, target_key = _resolve_submission_preview_target_key(
            adapter_key=session_row.adapter_key,
            requested_target_key=request.target_key,
        )
    candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
    target_candidate_ids = request.candidate_ids or list(candidate_map.keys())
    readiness = [
        _candidate_submission_readiness(
            candidate_map[candidate_id],
            next(
                (
                    candidate_validation
                    for candidate_validation in validation_response.candidate_validations
                    if candidate_validation.candidate_id == candidate_id
                ),
                None,
            ),
        )
        for candidate_id in target_candidate_ids
    ]
    ready_candidates = [
        candidate_map[readiness_item.candidate_id]
        for readiness_item in readiness
        if readiness_item.ready
    ]

    payload = (
        (
            _build_submission_execute_payload(
                db=db,
                session_row=session_row,
                mode=request.mode,
                target_key=target_key,
                ready_candidates=ready_candidates,
                session_validation=validation_response.session_validation,
            )
            if request.mode == SubmissionMode.EXPORT
            else _build_submission_preview_payload(
                db=db,
                session_row=session_row,
                submission_adapter=submission_adapter,
                mode=request.mode,
                target_key=target_key,
                ready_candidates=ready_candidates,
                session_validation=validation_response.session_validation,
            )
        )
        if request.include_payload
        else None
    )
    submission_warnings = list(payload.warnings) if payload is not None else []

    return CurationSubmissionPreviewResponse(
        submission=CurationSubmissionRecord(
            submission_id=str(uuid4()),
            session_id=str(session_row.id),
            adapter_key=session_row.adapter_key,
            mode=request.mode,
            target_key=target_key,
            status=(
                CurationSubmissionStatus.EXPORT_READY
                if request.mode == SubmissionMode.EXPORT
                else CurationSubmissionStatus.PREVIEW_READY
            ),
            readiness=readiness,
            payload=_submission_payload_model_input(payload),
            requested_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            validation_errors=[],
            warnings=submission_warnings,
        ),
        session_validation=validation_response.session_validation,
    )


def execute_submission(
    db: Session,
    session_id: str | UUID,
    request: CurationSubmissionExecuteRequest,
    actor_claims: dict[str, Any],
) -> CurationSubmissionExecuteResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )
    if request.mode != SubmissionMode.DIRECT_SUBMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submit endpoint only supports mode 'direct_submit'",
        )

    validation_response = validate_session(
        db,
        normalized_session_id,
        CurationSessionValidationRequest(
            session_id=request.session_id,
            candidate_ids=request.candidate_ids,
            force=False,
        ),
    )

    session_row = _load_session_for_validation(db, session_id=normalized_session_id)
    candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
    target_candidate_ids = request.candidate_ids or list(candidate_map.keys())
    readiness = [
        _candidate_submission_readiness(
            candidate_map[candidate_id],
            next(
                (
                    candidate_validation
                    for candidate_validation in validation_response.candidate_validations
                    if candidate_validation.candidate_id == candidate_id
                ),
                None,
            ),
        )
        for candidate_id in target_candidate_ids
    ]
    ready_candidates = [
        candidate_map[readiness_item.candidate_id]
        for readiness_item in readiness
        if readiness_item.ready
    ]
    if not ready_candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No eligible candidates are ready for direct submission",
        )

    payload = _build_submission_execute_payload(
        db=db,
        session_row=session_row,
        mode=request.mode,
        target_key=request.target_key,
        ready_candidates=ready_candidates,
        session_validation=validation_response.session_validation,
    )
    response_submission, action_log_entry = _execute_direct_submission_attempt(
        db=db,
        session_row=session_row,
        adapter_key=payload.adapter_key,
        mode=request.mode,
        target_key=request.target_key,
        payload=payload,
        readiness=readiness,
        actor_claims=actor_claims,
        action_type=CurationActionType.SUBMISSION_EXECUTED,
    )
    db.expire_all()

    response_session = get_session_detail(db, normalized_session_id)
    if (
        response_session.latest_submission is not None
        and response_session.latest_submission.submission_id == response_submission.submission_id
    ):
        response_session = response_session.model_copy(
            update={"latest_submission": response_submission}
        )

    return CurationSubmissionExecuteResponse(
        submission=response_submission,
        session=response_session,
        action_log_entry=action_log_entry,
    )


def retry_submission(
    db: Session,
    session_id: str | UUID,
    submission_id: str | UUID,
    request: CurationSubmissionRetryRequest,
    actor_claims: dict[str, Any],
) -> CurationSubmissionRetryResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_submission_id = _normalize_uuid(submission_id, field_name="submission_id")
    request_submission_id = _normalize_uuid(request.submission_id, field_name="submission_id")
    if normalized_submission_id != request_submission_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path submission_id does not match request body submission_id",
        )

    original_submission = _load_submission_record(
        db,
        session_id=normalized_session_id,
        submission_id=normalized_submission_id,
    )
    if original_submission.mode != SubmissionMode.DIRECT_SUBMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only direct-submit submissions may be retried",
        )
    if original_submission.status != CurationSubmissionStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only failed submissions may be retried",
        )

    target_candidate_ids = _submission_candidate_ids(original_submission)
    if not target_candidate_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Original submission does not include retriable candidate identifiers",
        )

    validation_response = validate_session(
        db,
        normalized_session_id,
        CurationSessionValidationRequest(
            session_id=str(normalized_session_id),
            candidate_ids=target_candidate_ids,
            force=False,
        ),
    )

    session_row = _load_session_for_validation(db, session_id=normalized_session_id)
    candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
    readiness = [
        _candidate_submission_readiness(
            candidate_map[candidate_id],
            next(
                (
                    candidate_validation
                    for candidate_validation in validation_response.candidate_validations
                    if candidate_validation.candidate_id == candidate_id
                ),
                None,
            ),
        )
        for candidate_id in target_candidate_ids
    ]
    ready_candidates = [
        candidate_map[readiness_item.candidate_id]
        for readiness_item in readiness
        if readiness_item.ready
    ]
    if not ready_candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No eligible candidates are ready for direct submission",
        )

    payload = _build_submission_execute_payload(
        db=db,
        session_row=session_row,
        adapter_key=original_submission.adapter_key,
        mode=original_submission.mode,
        target_key=original_submission.target_key,
        ready_candidates=ready_candidates,
        session_validation=validation_response.session_validation,
    )
    retry_reason = _normalized_optional_string(request.reason, field_name="reason")
    response_submission, action_log_entry = _execute_direct_submission_attempt(
        db=db,
        session_row=session_row,
        adapter_key=original_submission.adapter_key,
        mode=original_submission.mode,
        target_key=original_submission.target_key,
        payload=payload,
        readiness=readiness,
        actor_claims=actor_claims,
        action_type=CurationActionType.SUBMISSION_RETRIED,
        action_metadata={
            "original_submission_id": str(original_submission.id),
            "retry_reason": retry_reason,
        },
    )
    db.expire_all()

    return CurationSubmissionRetryResponse(
        submission=response_submission,
        action_log_entry=action_log_entry,
    )


def get_submission(
    db: Session,
    session_id: str | UUID,
    submission_id: str | UUID,
) -> CurationSubmissionHistoryResponse:
    submission_row = _load_submission_record(
        db,
        session_id=session_id,
        submission_id=submission_id,
    )
    return CurationSubmissionHistoryResponse(
        submission=_submission_record(submission_row),
    )

__all__ = [
    "execute_submission",
    "get_submission",
    "retry_submission",
    "submission_preview",
]
