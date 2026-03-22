"""Evidence write-path services for the curation workspace API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.lib.curation_workspace.evidence_resolver import (
    DeterministicEvidenceAnchorResolver,
)
from src.lib.curation_workspace.pipeline import (
    EvidenceResolutionContext,
    NormalizedCandidate,
)
from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate as CandidateModel,
    CurationDraft as DraftModel,
    CurationEvidenceRecord as EvidenceRecordModel,
    CurationExtractionResultRecord as ExtractionResultModel,
    CurationReviewSession as ReviewSessionModel,
)
from src.lib.curation_workspace.session_service import (
    _action_log_entry,
    _actor_claims_payload,
    _evidence_record,
    _normalize_uuid,
    get_candidate_detail,
    get_session_detail,
)
from src.schemas.curation_prep import CurationPrepCandidate
from src.schemas.curation_workspace import (
    CurationActionType,
    CurationActorType,
    CurationEvidenceRecomputeRequest,
    CurationEvidenceRecomputeResponse,
    CurationEvidenceResolveRequest,
    CurationEvidenceResolveResponse,
    CurationEvidenceSource,
    CurationManualEvidenceCreateRequest,
    CurationManualEvidenceCreateResponse,
    EvidenceAnchor,
)


SESSION_EVIDENCE_LOAD_OPTIONS = (
    selectinload(ReviewSessionModel.candidates).selectinload(CandidateModel.draft),
    selectinload(ReviewSessionModel.candidates).selectinload(CandidateModel.evidence_anchors),
    selectinload(ReviewSessionModel.candidates).selectinload(
        CandidateModel.validation_snapshots
    ),
)

MANUAL_RESOLUTION_SENTINEL = "__curation_workspace_manual__"


def recompute_evidence(
    request: CurationEvidenceRecomputeRequest,
    *,
    current_user_id: str,
    actor_claims: dict[str, Any],
    db: Session,
) -> CurationEvidenceRecomputeResponse:
    session = _load_session_for_mutation(db, request.session_id)
    target_candidates = _selected_candidates(session, request.candidate_ids)
    now = datetime.now(timezone.utc)

    updated_rows: list[EvidenceRecordModel] = []
    changed_field_keys: list[str] = []

    for candidate in target_candidates:
        candidate_updated = False

        for evidence_row in list(candidate.evidence_anchors):
            resolved_anchor, warnings = _resolve_anchor_against_document(
                db,
                document_id=str(session.document_id),
                anchor=EvidenceAnchor.model_validate(evidence_row.anchor or {}),
                adapter_key=candidate.adapter_key,
                profile_key=candidate.profile_key,
                field_path=(evidence_row.field_keys or [None])[0],
                current_user_id=current_user_id,
                prep_extraction_result_id=_prep_extraction_result_id(candidate),
            )
            evidence_row.source = CurationEvidenceSource.RECOMPUTED
            evidence_row.anchor = resolved_anchor.model_dump(mode="json")
            evidence_row.warnings = list(warnings)
            evidence_row.updated_at = now
            db.add(evidence_row)
            updated_rows.append(evidence_row)
            changed_field_keys.extend(evidence_row.field_keys or [])
            candidate_updated = True

        if candidate_updated:
            _touch_candidate(candidate, occurred_at=now)
            db.add(candidate)

    _touch_session(session, occurred_at=now)
    db.add(session)

    action_log_row = SessionActionLogModel(
        session_id=session.id,
        candidate_id=target_candidates[0].id if len(target_candidates) == 1 else None,
        draft_id=(
            target_candidates[0].draft.id
            if len(target_candidates) == 1 and target_candidates[0].draft is not None
            else None
        ),
        action_type=CurationActionType.EVIDENCE_RECOMPUTED,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=now,
        changed_field_keys=_dedupe_strings(changed_field_keys),
        evidence_anchor_ids=[str(row.id) for row in updated_rows],
        message=(
            f"Recomputed {len(updated_rows)} evidence anchor(s) across "
            f"{len(target_candidates)} candidate(s)"
        ),
        action_metadata={
            "candidate_ids": [str(candidate.id) for candidate in target_candidates],
            "updated_count": len(updated_rows),
            "force": request.force,
        },
    )
    db.add(action_log_row)
    db.flush()

    response = CurationEvidenceRecomputeResponse(
        session=get_session_detail(db, session.id),
        updated_evidence_records=[_evidence_record(row) for row in updated_rows],
        action_log_entry=_action_log_entry(action_log_row),
    )
    db.commit()
    return response


def create_manual_evidence(
    request: CurationManualEvidenceCreateRequest,
    *,
    actor_claims: dict[str, Any],
    db: Session,
) -> CurationManualEvidenceCreateResponse:
    session = _load_session_for_mutation(db, request.session_id)
    candidate = _candidate_in_session(session, request.candidate_id)
    field_keys = _normalized_string_list(request.field_keys, field_name="field_keys")
    field_group_keys = _normalized_string_list(
        request.field_group_keys,
        field_name="field_group_keys",
    )

    if not field_keys and not field_group_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manual evidence must include at least one field_key or field_group_key",
        )

    _ensure_field_keys_exist(candidate, field_keys)

    now = datetime.now(timezone.utc)
    if request.is_primary and field_keys:
        _demote_primary_evidence(candidate, field_keys, occurred_at=now)

    evidence_row = EvidenceRecordModel(
        candidate_id=candidate.id,
        source=CurationEvidenceSource.MANUAL,
        field_keys=field_keys,
        field_group_keys=field_group_keys,
        is_primary=request.is_primary,
        anchor=request.anchor.model_dump(mode="json"),
        warnings=[],
        created_at=now,
        updated_at=now,
    )
    db.add(evidence_row)
    db.flush()
    candidate.evidence_anchors.append(evidence_row)

    if candidate.draft is not None and field_keys:
        _update_draft_field_anchor_ids(
            candidate.draft,
            field_keys=field_keys,
            add_anchor_id=str(evidence_row.id),
            prepend=request.is_primary,
            occurred_at=now,
        )
        db.add(candidate.draft)

    _touch_candidate(candidate, occurred_at=now)
    _touch_session(session, occurred_at=now)
    db.add(candidate)
    db.add(session)

    action_log_row = SessionActionLogModel(
        session_id=session.id,
        candidate_id=candidate.id,
        draft_id=candidate.draft.id if candidate.draft is not None else None,
        action_type=CurationActionType.EVIDENCE_MANUAL_ADDED,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=now,
        changed_field_keys=field_keys,
        evidence_anchor_ids=[str(evidence_row.id)],
        message="Manual evidence added to candidate draft",
        action_metadata={
            "field_group_keys": field_group_keys,
            "is_primary": request.is_primary,
            "source": CurationEvidenceSource.MANUAL.value,
        },
    )
    db.add(action_log_row)
    db.flush()

    response = CurationManualEvidenceCreateResponse(
        evidence_record=_evidence_record(evidence_row),
        candidate=get_candidate_detail(db, candidate.id, session_id=session.id),
        action_log_entry=_action_log_entry(action_log_row),
    )
    db.commit()
    return response


def resolve_evidence(
    request: CurationEvidenceResolveRequest,
    *,
    current_user_id: str,
    db: Session,
) -> CurationEvidenceResolveResponse:
    session = _load_session_for_mutation(db, request.session_id)
    candidate = _candidate_in_session(session, request.candidate_id)
    field_key = _normalized_optional_string(request.field_key, field_name="field_key")

    if request.replace_existing and field_key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="field_key is required when replace_existing is true",
        )

    if field_key is not None:
        _ensure_field_keys_exist(candidate, [field_key])

    now = datetime.now(timezone.utc)
    resolved_anchor, warnings = _resolve_anchor_against_document(
        db,
        document_id=str(session.document_id),
        anchor=request.anchor,
        adapter_key=candidate.adapter_key,
        profile_key=candidate.profile_key,
        field_path=field_key,
        current_user_id=current_user_id,
        prep_extraction_result_id=_prep_extraction_result_id(candidate),
    )

    evidence_row = (
        _matching_evidence_record(candidate, field_key)
        if request.replace_existing
        else None
    )
    if evidence_row is None:
        is_primary = (
            field_key is not None
            and not any(
                record.is_primary and field_key in (record.field_keys or [])
                for record in candidate.evidence_anchors
            )
        )
        evidence_row = EvidenceRecordModel(
            candidate_id=candidate.id,
            source=CurationEvidenceSource.RECOMPUTED,
            field_keys=[field_key] if field_key is not None else [],
            field_group_keys=[],
            is_primary=is_primary,
            anchor=resolved_anchor.model_dump(mode="json"),
            warnings=list(warnings),
            created_at=now,
            updated_at=now,
        )
        db.add(evidence_row)
        db.flush()
        candidate.evidence_anchors.append(evidence_row)

        if candidate.draft is not None and field_key is not None:
            _update_draft_field_anchor_ids(
                candidate.draft,
                field_keys=[field_key],
                add_anchor_id=str(evidence_row.id),
                prepend=is_primary,
                occurred_at=now,
            )
            db.add(candidate.draft)
    else:
        evidence_row.source = CurationEvidenceSource.RECOMPUTED
        evidence_row.anchor = resolved_anchor.model_dump(mode="json")
        evidence_row.warnings = list(warnings)
        evidence_row.updated_at = now
        db.add(evidence_row)

    _touch_candidate(candidate, occurred_at=now)
    _touch_session(session, occurred_at=now)
    db.add(candidate)
    db.add(session)
    db.flush()

    response = CurationEvidenceResolveResponse(
        evidence_record=_evidence_record(evidence_row),
        candidate=get_candidate_detail(db, candidate.id, session_id=session.id),
    )
    db.commit()
    return response


def _load_session_for_mutation(db: Session, session_id: str | UUID) -> ReviewSessionModel:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    session = db.scalars(
        select(ReviewSessionModel)
        .where(ReviewSessionModel.id == normalized_session_id)
        .options(*SESSION_EVIDENCE_LOAD_OPTIONS)
    ).first()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {normalized_session_id} not found",
        )
    return session


def _candidate_in_session(
    session: ReviewSessionModel,
    candidate_id: str | UUID,
) -> CandidateModel:
    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    for candidate in session.candidates:
        if candidate.id == normalized_candidate_id:
            return candidate

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Candidate {normalized_candidate_id} does not belong to session {session.id}",
    )


def _selected_candidates(
    session: ReviewSessionModel,
    candidate_ids: Sequence[str],
) -> list[CandidateModel]:
    if not candidate_ids:
        return sorted(session.candidates, key=lambda candidate: (candidate.order, candidate.id))

    selected: list[CandidateModel] = []
    seen_ids: set[UUID] = set()
    for candidate_id in candidate_ids:
        candidate = _candidate_in_session(session, candidate_id)
        if candidate.id in seen_ids:
            continue
        selected.append(candidate)
        seen_ids.add(candidate.id)
    return selected


def _normalized_string_list(values: Sequence[str], *, field_name: str) -> list[str]:
    normalized_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalized_optional_string(value, field_name=field_name)
        if normalized is None or normalized in seen:
            continue
        normalized_values.append(normalized)
        seen.add(normalized)
    return normalized_values


def _normalized_optional_string(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must not contain empty values",
        )
    return normalized


def _ensure_field_keys_exist(candidate: CandidateModel, field_keys: Sequence[str]) -> None:
    if not field_keys:
        return

    draft = candidate.draft
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Candidate {candidate.id} draft is missing",
        )

    available_field_keys = {
        str(field_payload.get("field_key") or "").strip()
        for field_payload in (draft.fields or [])
    }
    missing_field_keys = [field_key for field_key in field_keys if field_key not in available_field_keys]
    if missing_field_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Candidate {candidate.id} draft does not contain field(s): "
                f"{', '.join(missing_field_keys)}"
            ),
        )


def _demote_primary_evidence(
    candidate: CandidateModel,
    field_keys: Sequence[str],
    *,
    occurred_at: datetime,
) -> None:
    target_field_keys = set(field_keys)
    for evidence_row in candidate.evidence_anchors:
        if evidence_row.is_primary and target_field_keys.intersection(evidence_row.field_keys or []):
            evidence_row.is_primary = False
            evidence_row.updated_at = occurred_at


def _update_draft_field_anchor_ids(
    draft: DraftModel,
    *,
    field_keys: Sequence[str],
    add_anchor_id: str,
    prepend: bool,
    occurred_at: datetime,
) -> None:
    if not field_keys:
        return

    target_field_keys = set(field_keys)
    updated_fields: list[dict[str, Any]] = []
    updated = False

    for field_payload in draft.fields or []:
        payload = dict(field_payload)
        current_ids = [str(anchor_id) for anchor_id in payload.get("evidence_anchor_ids") or []]
        next_ids = list(current_ids)

        if payload.get("field_key") in target_field_keys:
            if add_anchor_id in next_ids:
                if prepend and next_ids and next_ids[0] != add_anchor_id:
                    next_ids = [add_anchor_id] + [
                        anchor_id for anchor_id in next_ids if anchor_id != add_anchor_id
                    ]
            else:
                next_ids = [add_anchor_id, *next_ids] if prepend else [*next_ids, add_anchor_id]

        if next_ids != current_ids:
            payload["evidence_anchor_ids"] = next_ids
            updated = True

        updated_fields.append(payload)

    if updated:
        draft.fields = updated_fields
        draft.updated_at = occurred_at


def _touch_candidate(candidate: CandidateModel, *, occurred_at: datetime) -> None:
    candidate.updated_at = occurred_at


def _touch_session(session: ReviewSessionModel, *, occurred_at: datetime) -> None:
    session.session_version += 1
    session.updated_at = occurred_at
    session.last_worked_at = occurred_at


def _prep_extraction_result_id(candidate: CandidateModel) -> str:
    return (
        str(candidate.extraction_result_id)
        if candidate.extraction_result_id is not None
        else MANUAL_RESOLUTION_SENTINEL
    )


def _matching_evidence_record(
    candidate: CandidateModel,
    field_key: str | None,
) -> EvidenceRecordModel | None:
    if field_key is None:
        return None

    matches = [
        evidence_row
        for evidence_row in candidate.evidence_anchors
        if field_key in (evidence_row.field_keys or [])
    ]
    if not matches:
        return None

    return sorted(
        matches,
        key=lambda evidence_row: (
            not evidence_row.is_primary,
            evidence_row.created_at,
            evidence_row.id,
        ),
    )[0]


def _resolve_anchor_against_document(
    db: Session,
    *,
    document_id: str,
    anchor: EvidenceAnchor,
    adapter_key: str,
    profile_key: str | None,
    field_path: str | None,
    current_user_id: str,
    prep_extraction_result_id: str,
) -> tuple[EvidenceAnchor, list[str]]:
    resolver = DeterministicEvidenceAnchorResolver(
        user_id_resolver=_build_user_id_resolver(
            db,
            current_user_id=current_user_id,
        )
    )
    resolved_field_path = field_path or "workspace.evidence"
    prep_candidate = CurationPrepCandidate.model_validate(
        {
            "adapter_key": adapter_key,
            "profile_key": profile_key,
            "extracted_fields": [
                {
                    "field_path": resolved_field_path,
                    "value_type": "string",
                    "string_value": _anchor_seed_text(anchor),
                    "number_value": None,
                    "boolean_value": None,
                    "json_value": None,
                }
            ],
            "evidence_references": [
                {
                    "field_path": resolved_field_path,
                    "evidence_record_id": "workspace-evidence",
                    "extraction_result_id": (
                        None
                        if prep_extraction_result_id == MANUAL_RESOLUTION_SENTINEL
                        else prep_extraction_result_id
                    ),
                    "anchor": anchor.model_dump(mode="json"),
                    "rationale": "Workspace evidence resolution request.",
                }
            ],
            "conversation_context_summary": "Workspace evidence resolution.",
            "confidence": 1.0,
            "unresolved_ambiguities": [],
        }
    )
    normalized_candidate = NormalizedCandidate(
        prep_candidate=prep_candidate,
        normalized_payload=prep_candidate.to_extracted_fields_dict(),
        draft_fields=[],
    )
    resolved_records = resolver.resolve(
        prep_candidate,
        normalized_candidate=normalized_candidate,
        context=EvidenceResolutionContext(
            document_id=document_id,
            adapter_key=adapter_key,
            profile_key=profile_key,
            prep_extraction_result_id=prep_extraction_result_id,
            candidate_index=0,
        ),
    )
    if not resolved_records:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Evidence resolver did not return a resolved record",
        )

    resolved_record = resolved_records[0]
    return (
        EvidenceAnchor.model_validate(resolved_record.anchor),
        list(resolved_record.warnings),
    )


def _anchor_seed_text(anchor: EvidenceAnchor) -> str:
    if anchor.snippet_text:
        return anchor.snippet_text
    if anchor.sentence_text:
        return anchor.sentence_text
    if anchor.viewer_search_text:
        return anchor.viewer_search_text
    if anchor.section_title:
        return anchor.section_title
    if anchor.subsection_title:
        return anchor.subsection_title
    if anchor.figure_reference:
        return anchor.figure_reference
    if anchor.table_reference:
        return anchor.table_reference
    if anchor.page_label:
        return anchor.page_label
    if anchor.page_number is not None:
        return f"page {anchor.page_number}"
    return "workspace evidence"


def _build_user_id_resolver(
    db: Session,
    *,
    current_user_id: str,
):
    def _resolve_user_id(prep_extraction_result_id: str) -> str | None:
        normalized_id = str(prep_extraction_result_id or "").strip()
        if normalized_id and normalized_id != MANUAL_RESOLUTION_SENTINEL:
            try:
                extraction_result_id = UUID(normalized_id)
            except (TypeError, ValueError):
                extraction_result_id = None

            if extraction_result_id is not None:
                stored_user_id = db.scalar(
                    select(ExtractionResultModel.user_id).where(
                        ExtractionResultModel.id == extraction_result_id
                    )
                )
                normalized_user_id = str(stored_user_id or "").strip()
                if normalized_user_id:
                    return normalized_user_id

        return current_user_id

    return _resolve_user_id


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


__all__ = [
    "create_manual_evidence",
    "recompute_evidence",
    "resolve_evidence",
]
