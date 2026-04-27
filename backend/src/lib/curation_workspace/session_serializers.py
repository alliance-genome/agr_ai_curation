"""Serialization helpers for curation workspace session payloads."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.curation_adapters.entity_tag_bridge import (
    ENTITY_FIELD_KEYS,
    ENTITY_TYPE_FIELD_KEYS,
    SPECIES_FIELD_KEYS,
    TOPIC_FIELD_KEYS,
)
from src.lib.curation_workspace.evidence_quality import summarize_evidence_records
from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate,
    CurationDraft as DraftModel,
    CurationEvidenceRecord as EvidenceRecordModel,
    CurationReviewSession as ReviewSessionModel,
    CurationSubmissionRecord as SubmissionModel,
    CurationValidationSnapshot as ValidationSnapshotModel,
)
from src.lib.curation_workspace.session_common import _latest_snapshot_record
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from src.schemas.curation_workspace import (
    CurationActionLogEntry,
    CurationActorRef,
    CurationAdapterRef,
    CurationCandidate as CurationCandidatePayload,
    CurationCandidateSource,
    CurationDraft as CurationDraftPayload,
    CurationDraftField as CurationDraftFieldSchema,
    CurationDocumentRef,
    CurationEntityTag as CurationEntityTagPayload,
    CurationEntityTagDbValidationStatus,
    CurationEntityTagEvidence as CurationEntityTagEvidencePayload,
    CurationEntityTagSource,
    CurationEntityTypeCode,
    CurationEvidenceRecord as CurationEvidenceRecordPayload,
    CurationEvidenceSummary,
    CurationExtractionResultRecord,
    CurationReviewSession,
    CurationSessionProgress,
    CurationSessionSummary,
    CurationSubmissionRecord,
    CurationValidationSnapshot as CurationValidationSnapshotSchema,
    CurationValidationSummary,
    EvidenceAnchor,
    FieldValidationResult,
    FieldValidationStatus,
    SubmissionPayloadContract,
)

ENTITY_TAG_FIELD_KEYS: tuple[str, ...] = (
    *ENTITY_FIELD_KEYS,
    *ENTITY_TYPE_FIELD_KEYS,
    *SPECIES_FIELD_KEYS,
    *TOPIC_FIELD_KEYS,
)

def _viewer_url(file_path: str | None) -> str | None:
    if not file_path:
        return None
    return f"/uploads/{file_path.lstrip('/')}"

def _load_documents(db: Session, document_ids: Iterable[UUID]) -> dict[UUID, PDFDocument]:
    ids = list({document_id for document_id in document_ids})
    if not ids:
        return {}
    documents = db.scalars(select(PDFDocument).where(PDFDocument.id.in_(ids))).all()
    return {document.id: document for document in documents}


def _load_users(db: Session, actor_ids: Iterable[str | None]) -> dict[str, User]:
    ids = sorted({actor_id for actor_id in actor_ids if actor_id})
    if not ids:
        return {}
    users = db.scalars(select(User).where(User.auth_sub.in_(ids))).all()
    return {user.auth_sub: user for user in users}


def _actor_ref(user_map: dict[str, User], actor_id: str | None) -> CurationActorRef | None:
    if not actor_id:
        return None
    user = user_map.get(actor_id)
    if user is None:
        return CurationActorRef(actor_id=actor_id)
    return CurationActorRef(
        actor_id=user.auth_sub,
        display_name=user.display_name or user.email or user.auth_sub,
        email=user.email,
    )


def _adapter_ref(
    session: ReviewSessionModel,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> CurationAdapterRef:
    display_label = session.adapter_key.replace("_", " ").title()
    return CurationAdapterRef(
        adapter_key=session.adapter_key,
        display_label=display_label,
        metadata=dict(metadata or {}),
    )


def _document_ref(document: PDFDocument | None) -> CurationDocumentRef:
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session document metadata is missing",
        )

    viewer_url = _viewer_url(document.file_path)
    return CurationDocumentRef(
        document_id=str(document.id),
        title=document.title or document.filename,
        pdf_url=viewer_url,
        viewer_url=viewer_url,
        page_count=document.page_count,
    )


def _session_progress(session: ReviewSessionModel) -> CurationSessionProgress:
    return CurationSessionProgress(
        total_candidates=session.total_candidates,
        reviewed_candidates=session.reviewed_candidates,
        pending_candidates=session.pending_candidates,
        accepted_candidates=session.accepted_candidates,
        rejected_candidates=session.rejected_candidates,
        manual_candidates=session.manual_candidates,
    )


def _validation_summary(
    snapshots: Sequence[ValidationSnapshotModel],
) -> CurationValidationSummary | None:
    snapshot = _latest_snapshot_record(snapshots)
    if snapshot is None:
        return None

    summary_payload = dict(snapshot.summary or {})
    summary_payload.setdefault("state", snapshot.state)
    summary_payload.setdefault("counts", {})
    summary_payload.setdefault("warnings", list(snapshot.warnings or []))
    summary_payload.setdefault("stale_field_keys", [])
    summary_payload.setdefault("last_validated_at", snapshot.completed_at)
    try:
        return CurationValidationSummary.model_validate(summary_payload)
    except Exception:
        return None


def _latest_validation_summary(session: ReviewSessionModel) -> CurationValidationSummary | None:
    session_level_snapshots = [
        snapshot
        for snapshot in session.validation_snapshots
        if snapshot.candidate_id is None
    ]
    return _validation_summary(session_level_snapshots or list(session.validation_snapshots))


def _evidence_summary_from_records(
    records: Sequence[EvidenceRecordModel],
) -> CurationEvidenceSummary | None:
    return summarize_evidence_records(records)
def _evidence_summary(session: ReviewSessionModel) -> CurationEvidenceSummary | None:
    return _evidence_summary_from_records(
        [
            evidence_anchor
            for candidate in session.candidates
            for evidence_anchor in candidate.evidence_anchors
        ]
    )


def _draft_detail(record: DraftModel | None) -> CurationDraftPayload | None:
    if record is None:
        return None

    return CurationDraftPayload(
        draft_id=str(record.id),
        candidate_id=str(record.candidate_id),
        adapter_key=record.adapter_key,
        version=record.version,
        title=record.title,
        summary=record.summary,
        fields=[
            CurationDraftFieldSchema.model_validate(field_payload)
            for field_payload in (record.fields or [])
        ],
        notes=record.notes,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_saved_at=record.last_saved_at,
        metadata=dict(record.draft_metadata or {}),
    )


def _evidence_record(record: EvidenceRecordModel) -> CurationEvidenceRecordPayload:
    return CurationEvidenceRecordPayload(
        anchor_id=str(record.id),
        candidate_id=str(record.candidate_id),
        source=record.source,
        field_keys=list(record.field_keys or []),
        field_group_keys=list(record.field_group_keys or []),
        is_primary=record.is_primary,
        anchor=EvidenceAnchor.model_validate(record.anchor or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
        warnings=list(record.warnings or []),
    )

def build_evidence_record(record: EvidenceRecordModel) -> CurationEvidenceRecordPayload:
    """Public evidence-record serializer shared across curation workspace services."""

    return _evidence_record(record)


def _validation_snapshot(record: ValidationSnapshotModel) -> CurationValidationSnapshotSchema:
    summary_payload = dict(record.summary or {})
    summary_payload.setdefault("state", record.state)
    summary_payload.setdefault("counts", {})
    summary_payload.setdefault("warnings", list(record.warnings or []))
    summary_payload.setdefault("stale_field_keys", [])
    summary_payload.setdefault("last_validated_at", record.completed_at)

    return CurationValidationSnapshotSchema(
        snapshot_id=str(record.id),
        scope=record.scope,
        session_id=str(record.session_id),
        candidate_id=str(record.candidate_id) if record.candidate_id else None,
        adapter_key=record.adapter_key,
        state=record.state,
        field_results={
            field_key: FieldValidationResult.model_validate(result_payload)
            for field_key, result_payload in (record.field_results or {}).items()
        },
        summary=CurationValidationSummary.model_validate(summary_payload),
        requested_at=record.requested_at,
        completed_at=record.completed_at,
        warnings=list(record.warnings or []),
    )


def _candidate_detail(candidate: CurationCandidate) -> CurationCandidatePayload:
    draft = _draft_detail(candidate.draft)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Candidate {candidate.id} draft is missing",
        )

    ordered_evidence = sorted(
        candidate.evidence_anchors,
        key=lambda evidence_record: (
            evidence_record.created_at,
            evidence_record.updated_at,
            evidence_record.id,
        ),
    )

    return CurationCandidatePayload(
        candidate_id=str(candidate.id),
        session_id=str(candidate.session_id),
        source=candidate.source,
        status=candidate.status,
        order=candidate.order,
        adapter_key=candidate.adapter_key,
        display_label=candidate.display_label,
        secondary_label=candidate.secondary_label,
        conversation_summary=candidate.conversation_summary,
        extraction_result_id=(
            str(candidate.extraction_result_id) if candidate.extraction_result_id else None
        ),
        normalized_payload=dict(candidate.normalized_payload or {}),
        draft=draft,
        evidence_anchors=[_evidence_record(record) for record in ordered_evidence],
        validation=_validation_summary(candidate.validation_snapshots),
        evidence_summary=_evidence_summary_from_records(candidate.evidence_anchors),
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
        last_reviewed_at=candidate.last_reviewed_at,
        metadata=dict(candidate.candidate_metadata or {}),
    )


def _submission_payload(record: SubmissionModel) -> SubmissionPayloadContract | None:
    if record.payload is None:
        return None

    if _stored_submission_payload_contract(record.payload):
        payload_json = record.payload.get("payload_json")
        payload_text = record.payload.get("payload_text")
        candidate_ids = [
            candidate_id
            for candidate_id in record.payload.get("candidate_ids", [])
            if isinstance(candidate_id, str) and candidate_id
        ]
        content_type = record.payload.get("content_type")
        filename = record.payload.get("filename")
        payload_warnings = [
            warning
            for warning in record.payload.get("warnings", [])
            if isinstance(warning, str) and warning
        ]
    else:
        payload_json = record.payload if not isinstance(record.payload, str) else None
        payload_text = record.payload if isinstance(record.payload, str) else None
        candidate_ids = [
            readiness.get("candidate_id")
            for readiness in record.readiness or []
            if isinstance(readiness, dict) and readiness.get("candidate_id")
        ]
        content_type = None
        filename = None
        payload_warnings = list(record.warnings or [])

    return SubmissionPayloadContract(
        mode=record.mode,
        target_key=record.target_key,
        adapter_key=record.adapter_key,
        candidate_ids=candidate_ids,
        payload_json=payload_json,
        payload_text=payload_text,
        content_type=content_type,
        filename=filename,
        warnings=payload_warnings,
    )


def _stored_submission_payload_contract(payload: object) -> bool:
    return isinstance(payload, dict) and payload.get("storage_kind") == "submission_payload_contract"


def _serialize_submission_payload_contract(payload: SubmissionPayloadContract) -> dict[str, Any]:
    return {
        "storage_kind": "submission_payload_contract",
        "candidate_ids": list(payload.candidate_ids),
        "payload_json": payload.payload_json,
        "payload_text": payload.payload_text,
        "content_type": payload.content_type,
        "filename": payload.filename,
        "warnings": list(payload.warnings),
    }


def _submission_payload_model_input(
    payload: SubmissionPayloadContract | None,
) -> dict[str, Any] | None:
    """Normalize nested payload models before embedding them in larger response schemas."""

    if payload is None:
        return None
    return payload.model_dump()


def _submission_record(record: SubmissionModel) -> CurationSubmissionRecord:
    return CurationSubmissionRecord(
        submission_id=str(record.id),
        session_id=str(record.session_id),
        adapter_key=record.adapter_key,
        mode=record.mode,
        target_key=record.target_key,
        status=record.status,
        readiness=list(record.readiness or []),
        payload=_submission_payload_model_input(_submission_payload(record)),
        requested_at=record.requested_at,
        completed_at=record.completed_at,
        external_reference=record.external_reference,
        response_message=record.response_message,
        validation_errors=list(record.validation_errors or []),
        warnings=list(record.warnings or []),
    )


def _extraction_records(session: ReviewSessionModel) -> list[CurationExtractionResultRecord]:
    extraction_results: list[CurationExtractionResultRecord] = []
    seen_ids: set[UUID] = set()

    for candidate in session.candidates:
        extraction_result = candidate.extraction_result
        if extraction_result is None or extraction_result.id in seen_ids:
            continue
        seen_ids.add(extraction_result.id)
        extraction_results.append(
            CurationExtractionResultRecord(
                extraction_result_id=str(extraction_result.id),
                document_id=str(extraction_result.document_id),
                adapter_key=extraction_result.adapter_key,
                agent_key=extraction_result.agent_key,
                source_kind=extraction_result.source_kind,
                origin_session_id=extraction_result.origin_session_id,
                trace_id=extraction_result.trace_id,
                flow_run_id=extraction_result.flow_run_id,
                user_id=extraction_result.user_id,
                candidate_count=extraction_result.candidate_count,
                conversation_summary=extraction_result.conversation_summary,
                payload_json=extraction_result.payload_json,
                created_at=extraction_result.created_at,
                metadata=dict(extraction_result.extraction_metadata or {}),
            )
        )

    return extraction_results


def _session_summary(
    session: ReviewSessionModel,
    document_map: dict[UUID, PDFDocument],
    user_map: dict[str, User],
) -> CurationSessionSummary:
    return CurationSessionSummary(
        session_id=str(session.id),
        status=session.status,
        adapter=_adapter_ref(session),
        document=_document_ref(document_map.get(session.document_id)),
        flow_run_id=session.flow_run_id,
        progress=_session_progress(session),
        validation=_latest_validation_summary(session),
        evidence=_evidence_summary(session),
        current_candidate_id=str(session.current_candidate_id) if session.current_candidate_id else None,
        assigned_curator=_actor_ref(user_map, session.assigned_curator_id),
        created_by=_actor_ref(user_map, session.created_by_id),
        prepared_at=session.prepared_at,
        last_worked_at=session.last_worked_at,
        notes=session.notes,
        warnings=list(session.warnings or []),
        tags=list(session.tags or []),
    )


def _session_detail(
    db: Session,
    session: ReviewSessionModel,
    document_map: dict[UUID, PDFDocument],
    user_map: dict[str, User],
) -> CurationReviewSession:
    summary = _session_summary(session, document_map, user_map)
    latest_submission = _submission_record(session.submissions[-1]) if session.submissions else None
    summary_payload = summary.model_dump()
    summary_payload["adapter"] = _adapter_ref(session)

    return CurationReviewSession(
        **summary_payload,
        session_version=session.session_version,
        extraction_results=_extraction_records(session),
        latest_submission=latest_submission,
        submitted_at=session.submitted_at,
        paused_at=session.paused_at,
        rejection_reason=session.rejection_reason,
    )


def _action_log_entry(record: SessionActionLogModel | None) -> CurationActionLogEntry | None:
    if record is None:
        return None

    actor = None
    if record.actor:
        try:
            actor = CurationActorRef.model_validate(record.actor)
        except Exception:
            actor = CurationActorRef(actor_id=record.actor.get("actor_id"))

    return CurationActionLogEntry(
        action_id=str(record.id),
        session_id=str(record.session_id),
        candidate_id=str(record.candidate_id) if record.candidate_id else None,
        draft_id=str(record.draft_id) if record.draft_id else None,
        action_type=record.action_type,
        actor_type=record.actor_type,
        actor=actor,
        occurred_at=record.occurred_at,
        previous_session_status=record.previous_session_status,
        new_session_status=record.new_session_status,
        previous_candidate_status=record.previous_candidate_status,
        new_candidate_status=record.new_candidate_status,
        changed_field_keys=list(record.changed_field_keys or []),
        evidence_anchor_ids=[str(anchor_id) for anchor_id in record.evidence_anchor_ids or []],
        reason=record.reason,
        message=record.message,
        metadata=dict(record.action_metadata or {}),
    )


def build_action_log_entry(
    record: SessionActionLogModel | None,
) -> CurationActionLogEntry | None:
    """Public action-log serializer shared across curation workspace services."""

    return _action_log_entry(record)

def _draft_payload(candidate: CurationCandidate) -> CurationDraftPayload:
    if candidate.draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curation candidate {candidate.id} is missing its draft payload",
        )

    return CurationDraftPayload(
        draft_id=str(candidate.draft.id),
        candidate_id=str(candidate.draft.candidate_id),
        adapter_key=candidate.draft.adapter_key,
        version=candidate.draft.version,
        title=candidate.draft.title,
        summary=candidate.draft.summary,
        fields=list(candidate.draft.fields or []),
        notes=candidate.draft.notes,
        created_at=candidate.draft.created_at,
        updated_at=candidate.draft.updated_at,
        last_saved_at=candidate.draft.last_saved_at,
        metadata=dict(candidate.draft.draft_metadata or {}),
    )


def _candidate_validation_summary(candidate: CurationCandidate) -> CurationValidationSummary | None:
    return _validation_summary(candidate.validation_snapshots)


def _candidate_evidence_record(record: EvidenceRecordModel) -> CurationEvidenceRecordPayload:
    return CurationEvidenceRecordPayload(
        anchor_id=str(record.id),
        candidate_id=str(record.candidate_id),
        source=record.source,
        field_keys=list(record.field_keys or []),
        field_group_keys=list(record.field_group_keys or []),
        is_primary=record.is_primary,
        anchor=dict(record.anchor or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
        warnings=list(record.warnings or []),
    )


def _normalize_entity_field_key(value: str) -> str:
    return value.strip().lower()


def _matches_entity_field(
    draft_field: CurationDraftFieldSchema,
    accepted_keys: Sequence[str],
) -> bool:
    field_key = _normalize_entity_field_key(draft_field.field_key)
    field_label = _normalize_entity_field_key(draft_field.label)
    return any(
        field_key == _normalize_entity_field_key(accepted_key)
        or field_label == _normalize_entity_field_key(accepted_key)
        for accepted_key in accepted_keys
    )


def _find_entity_field(
    fields: Sequence[CurationDraftFieldSchema],
    accepted_keys: Sequence[str],
) -> CurationDraftFieldSchema | None:
    for draft_field in fields:
        if _matches_entity_field(draft_field, accepted_keys):
            return draft_field
    return None


def _candidate_has_entity_tag_fields(candidate: CurationCandidatePayload) -> bool:
    return any(
        _matches_entity_field(draft_field, ENTITY_TAG_FIELD_KEYS)
        for draft_field in candidate.draft.fields
    )


def _read_required_entity_string(
    field: CurationDraftFieldSchema,
    candidate_id: str,
) -> str:
    if not isinstance(field.value, str) or not field.value.strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate_id} is missing a required string value "
                f"for {field.field_key} in the entity-tag payload"
            ),
        )

    return field.value.strip()


def _read_optional_entity_string(
    field: CurationDraftFieldSchema | None,
    candidate_id: str,
) -> str:
    if field is None or field.value is None:
        return ""

    if not isinstance(field.value, str):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate_id} has a non-string value "
                f"for {field.field_key} in the entity-tag payload"
            ),
        )

    return field.value.strip()


def _resolve_entity_name_field(candidate: CurationCandidatePayload) -> CurationDraftFieldSchema:
    entity_field = _find_entity_field(candidate.draft.fields, ENTITY_FIELD_KEYS)
    if entity_field is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate.candidate_id} is missing an entity-name field "
                "required for the entity-tag payload"
            ),
        )

    return entity_field


def _entity_type_code(
    candidate: CurationCandidatePayload,
) -> CurationEntityTypeCode:
    type_field = _find_entity_field(candidate.draft.fields, ENTITY_TYPE_FIELD_KEYS)
    if type_field is not None and type_field.value is not None:
        if not isinstance(type_field.value, str):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"Curation candidate {candidate.candidate_id} has a non-string entity type "
                    "value in the entity-tag payload"
                ),
            )

        normalized_type = type_field.value.strip()
        if not normalized_type:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"Curation candidate {candidate.candidate_id} has a blank entity type "
                    "identifier in the entity-tag payload"
                ),
            )

        return normalized_type

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            f"Curation candidate {candidate.candidate_id} is missing an entity type "
            "required for the entity-tag payload"
        ),
    )


def _entity_db_status(
    candidate: CurationCandidatePayload,
    entity_field: CurationDraftFieldSchema,
) -> CurationEntityTagDbValidationStatus:
    field_status = entity_field.validation_result.status if entity_field.validation_result else None
    if field_status is FieldValidationStatus.VALIDATED:
        return CurationEntityTagDbValidationStatus.VALIDATED
    if field_status is FieldValidationStatus.AMBIGUOUS:
        return CurationEntityTagDbValidationStatus.AMBIGUOUS
    if field_status is FieldValidationStatus.NOT_FOUND:
        return CurationEntityTagDbValidationStatus.NOT_FOUND
    if field_status is FieldValidationStatus.SKIPPED:
        return CurationEntityTagDbValidationStatus.NOT_FOUND
    if field_status is not None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate.candidate_id} has unsupported validation status "
                f"{field_status.value!r} for the entity-tag payload"
            ),
        )

    summary = candidate.validation
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate.candidate_id} is missing validation data "
                "required for the entity-tag payload"
            ),
        )

    if summary.counts.not_found > 0 or summary.counts.invalid_format > 0:
        return CurationEntityTagDbValidationStatus.NOT_FOUND
    if summary.counts.ambiguous > 0 or summary.counts.conflict > 0:
        return CurationEntityTagDbValidationStatus.AMBIGUOUS
    if summary.counts.validated > 0:
        return CurationEntityTagDbValidationStatus.VALIDATED
    if summary.counts.skipped > 0:
        return CurationEntityTagDbValidationStatus.NOT_FOUND

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            f"Curation candidate {candidate.candidate_id} does not have a usable validation "
            "status for the entity-tag payload"
        ),
    )


def _entity_db_identifier(entity_field: CurationDraftFieldSchema) -> str | None:
    validation_result = entity_field.validation_result
    if validation_result is None or not validation_result.candidate_matches:
        return None

    identifier = validation_result.candidate_matches[0].identifier
    if not isinstance(identifier, str) or not identifier.strip():
        return None

    return identifier.strip()


def _entity_evidence(
    candidate: CurationCandidatePayload,
) -> CurationEntityTagEvidencePayload | None:
    primary_evidence = next(
        (anchor for anchor in candidate.evidence_anchors if anchor.is_primary),
        None,
    )
    evidence_record = primary_evidence or (candidate.evidence_anchors[0] if candidate.evidence_anchors else None)
    if evidence_record is None:
        return None

    anchor = evidence_record.anchor
    sentence_text = None
    if isinstance(anchor.sentence_text, str) and anchor.sentence_text.strip():
        sentence_text = anchor.sentence_text.strip()
    elif isinstance(anchor.snippet_text, str) and anchor.snippet_text.strip():
        sentence_text = anchor.snippet_text.strip()

    if sentence_text is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Curation candidate {candidate.candidate_id} has evidence without sentence or "
                "snippet text required for the entity-tag payload"
            ),
        )

    return CurationEntityTagEvidencePayload(
        sentence_text=sentence_text,
        page_number=anchor.page_number,
        section_title=anchor.section_title,
        chunk_ids=list(anchor.chunk_ids or []),
    )


def _entity_tag_payload(candidate: CurationCandidatePayload) -> CurationEntityTagPayload:
    entity_field = _resolve_entity_name_field(candidate)

    return CurationEntityTagPayload(
        tag_id=candidate.candidate_id,
        entity_name=_read_required_entity_string(entity_field, candidate.candidate_id),
        entity_type=_entity_type_code(candidate),
        species=_read_optional_entity_string(
            _find_entity_field(candidate.draft.fields, SPECIES_FIELD_KEYS),
            candidate.candidate_id,
        ),
        topic=_read_optional_entity_string(
            _find_entity_field(candidate.draft.fields, TOPIC_FIELD_KEYS),
            candidate.candidate_id,
        ),
        db_status=_entity_db_status(candidate, entity_field),
        db_entity_id=_entity_db_identifier(entity_field),
        source=(
            CurationEntityTagSource.MANUAL
            if candidate.source is CurationCandidateSource.MANUAL
            else CurationEntityTagSource.AI
        ),
        decision=candidate.status,
        evidence=_entity_evidence(candidate),
        notes=candidate.draft.notes,
    )


def _candidate_payload(candidate: CurationCandidate) -> CurationCandidatePayload:
    evidence_records = [
        _candidate_evidence_record(record)
        for record in candidate.evidence_anchors
    ]
    return CurationCandidatePayload(
        candidate_id=str(candidate.id),
        session_id=str(candidate.session_id),
        source=candidate.source,
        status=candidate.status,
        order=candidate.order,
        adapter_key=candidate.adapter_key,
        display_label=candidate.display_label,
        secondary_label=candidate.secondary_label,
        conversation_summary=candidate.conversation_summary,
        extraction_result_id=(
            str(candidate.extraction_result_id)
            if candidate.extraction_result_id is not None
            else None
        ),
        draft=_draft_payload(candidate),
        evidence_anchors=evidence_records,
        validation=_candidate_validation_summary(candidate),
        evidence_summary=_evidence_summary_from_records(candidate.evidence_anchors),
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
        last_reviewed_at=candidate.last_reviewed_at,
        metadata=dict(candidate.candidate_metadata or {}),
    )

__all__ = [
    "build_action_log_entry",
    "build_evidence_record",
]
