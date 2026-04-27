"""Persistence primitives shared by curation workspace session flows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate,
    CurationDraft as DraftModel,
    CurationEvidenceRecord as EvidenceRecordModel,
    CurationReviewSession as ReviewSessionModel,
    CurationValidationSnapshot as ValidationSnapshotModel,
)
from src.lib.curation_workspace.session_common import _normalize_uuid
from src.lib.curation_workspace.session_types import (
    CandidateProgressCountsInput,
    PreparedCandidateInput,
    PreparedDraftFieldInput,
    PreparedEvidenceRecordInput,
    PreparedValidationSnapshotInput,
)
from src.schemas.curation_workspace import (
    CurationActionType,
    CurationActorType,
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationValidationScope,
    FieldValidationResult,
)

def _delete_session_validation_snapshots(
    db: Session,
    *,
    session_id: UUID,
) -> None:
    db.execute(
        delete(ValidationSnapshotModel).where(
            ValidationSnapshotModel.session_id == session_id
        )
    )


def _delete_candidate_children(
    db: Session,
    *,
    session_id: UUID,
    candidate_ids: Sequence[UUID],
) -> None:
    if not candidate_ids:
        return

    db.execute(
        delete(SessionActionLogModel).where(
            SessionActionLogModel.session_id == session_id,
            SessionActionLogModel.candidate_id.in_(candidate_ids),
        )
    )
    db.execute(
        delete(EvidenceRecordModel).where(
            EvidenceRecordModel.candidate_id.in_(candidate_ids)
        )
    )
    db.execute(
        delete(DraftModel).where(DraftModel.candidate_id.in_(candidate_ids))
    )
    db.execute(
        delete(CurationCandidate).where(CurationCandidate.id.in_(candidate_ids))
    )


def _persist_prepared_candidates(
    db: Session,
    session_row: ReviewSessionModel,
    candidates: Sequence[PreparedCandidateInput],
    *,
    prepared_at: datetime,
) -> list[str]:
    candidate_ids: list[str] = []

    for candidate_input in sorted(candidates, key=lambda item: item.order):
        candidate_row = CurationCandidate(
            session_id=session_row.id,
            source=candidate_input.source,
            status=candidate_input.status,
            order=candidate_input.order,
            adapter_key=candidate_input.adapter_key,
            profile_key=None,
            display_label=candidate_input.display_label,
            secondary_label=candidate_input.secondary_label,
            conversation_summary=candidate_input.conversation_summary,
            extraction_result_id=(
                _normalize_uuid(
                    candidate_input.extraction_result_id,
                    field_name="extraction_result_id",
                )
                if candidate_input.extraction_result_id is not None
                else None
            ),
            normalized_payload=dict(candidate_input.normalized_payload),
            candidate_metadata=dict(candidate_input.metadata),
            created_at=prepared_at,
            updated_at=prepared_at,
        )
        db.add(candidate_row)
        db.flush()

        evidence_anchor_ids_by_field = _persist_candidate_evidence_records(
            db,
            candidate_row,
            candidate_input.evidence_records,
            created_at=prepared_at,
        )

        draft_row = DraftModel(
            candidate_id=candidate_row.id,
            adapter_key=candidate_input.adapter_key,
            title=candidate_input.draft_title,
            summary=candidate_input.draft_summary,
            fields=[
                _draft_field_payload(
                    field_input,
                    evidence_anchor_ids_by_field=evidence_anchor_ids_by_field,
                    field_results=(
                        candidate_input.validation_snapshot.field_results
                        if candidate_input.validation_snapshot is not None
                        else {}
                    ),
                )
                for field_input in candidate_input.draft_fields
            ],
            notes=candidate_input.draft_notes,
            draft_metadata=dict(candidate_input.draft_metadata),
            created_at=prepared_at,
            updated_at=prepared_at,
        )
        db.add(draft_row)

        if candidate_input.validation_snapshot is not None:
            db.add(
                _validation_snapshot_row(
                    session_row=session_row,
                    candidate_id=candidate_row.id,
                    snapshot=candidate_input.validation_snapshot,
                )
            )

        candidate_ids.append(str(candidate_row.id))

    return candidate_ids


def _persist_candidate_evidence_records(
    db: Session,
    candidate_row: CurationCandidate,
    evidence_records: Sequence[PreparedEvidenceRecordInput],
    *,
    created_at: datetime,
) -> dict[str, list[str]]:
    evidence_anchor_ids_by_field: dict[str, list[str]] = {}

    for evidence_input in evidence_records:
        evidence_row = EvidenceRecordModel(
            candidate_id=candidate_row.id,
            source=evidence_input.source,
            field_keys=list(evidence_input.field_keys),
            field_group_keys=list(evidence_input.field_group_keys),
            is_primary=evidence_input.is_primary,
            anchor=dict(evidence_input.anchor),
            warnings=list(evidence_input.warnings),
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(evidence_row)
        db.flush()

        anchor_id = str(evidence_row.id)
        for field_key in evidence_input.field_keys:
            evidence_anchor_ids_by_field.setdefault(field_key, []).append(anchor_id)

    return evidence_anchor_ids_by_field


def _draft_field_payload(
    field_input: PreparedDraftFieldInput,
    *,
    evidence_anchor_ids_by_field: Mapping[str, Sequence[str]],
    field_results: Mapping[str, FieldValidationResult],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "field_key": field_input.field_key,
        "label": field_input.label,
        "value": field_input.value,
        "seed_value": field_input.seed_value,
        "field_type": field_input.field_type,
        "group_key": field_input.group_key,
        "group_label": field_input.group_label,
        "order": field_input.order,
        "required": field_input.required,
        "read_only": field_input.read_only,
        "dirty": field_input.dirty,
        "stale_validation": field_input.stale_validation,
        "evidence_anchor_ids": list(
            evidence_anchor_ids_by_field.get(field_input.field_key, [])
        ),
        "metadata": dict(field_input.metadata),
    }

    validation_result = field_results.get(field_input.field_key)
    if validation_result is not None:
        payload["validation_result"] = validation_result.model_dump(mode="json")

    return payload


def _validation_snapshot_row(
    *,
    session_row: ReviewSessionModel,
    candidate_id: UUID | None,
    snapshot: PreparedValidationSnapshotInput,
) -> ValidationSnapshotModel:
    return ValidationSnapshotModel(
        scope=snapshot.scope,
        session_id=session_row.id,
        candidate_id=candidate_id,
        adapter_key=snapshot.adapter_key or session_row.adapter_key,
        state=snapshot.state,
        field_results={
            field_key: result.model_dump(mode="json")
            for field_key, result in snapshot.field_results.items()
        },
        summary=snapshot.summary.model_dump(mode="json"),
        warnings=list(snapshot.warnings),
        requested_at=snapshot.requested_at,
        completed_at=snapshot.completed_at,
    )


def _persist_session_validation_snapshot(
    db: Session,
    session_row: ReviewSessionModel,
    snapshot: PreparedValidationSnapshotInput | None,
) -> SessionActionLogModel | None:
    if snapshot is None:
        return None

    db.add(
        _validation_snapshot_row(
            session_row=session_row,
            candidate_id=None,
            snapshot=snapshot,
        )
    )
    return SessionActionLogModel(
        session_id=session_row.id,
        action_type=CurationActionType.VALIDATION_COMPLETED,
        actor_type=CurationActorType.SYSTEM,
        occurred_at=snapshot.completed_at or snapshot.requested_at or session_row.prepared_at,
        message="Deterministic post-agent validation completed",
        action_metadata={
            "validation_state": snapshot.state.value,
            "validation_scope": snapshot.scope.value,
        },
    )


def _apply_progress_counts(
    session_row: ReviewSessionModel,
    candidates: Sequence[PreparedCandidateInput],
) -> None:
    _apply_candidate_progress_counts(session_row, candidates)
def _apply_candidate_progress_counts(
    session_row: ReviewSessionModel,
    candidates: Sequence[CandidateProgressCountsInput],
) -> None:
    total_candidates = len(candidates)
    pending_candidates = sum(
        1 for candidate in candidates if candidate.status == CurationCandidateStatus.PENDING
    )
    accepted_candidates = sum(
        1 for candidate in candidates if candidate.status == CurationCandidateStatus.ACCEPTED
    )
    rejected_candidates = sum(
        1 for candidate in candidates if candidate.status == CurationCandidateStatus.REJECTED
    )
    manual_candidates = sum(
        1 for candidate in candidates if candidate.source == CurationCandidateSource.MANUAL
    )

    session_row.total_candidates = total_candidates
    session_row.pending_candidates = pending_candidates
    session_row.accepted_candidates = accepted_candidates
    session_row.rejected_candidates = rejected_candidates
    session_row.manual_candidates = manual_candidates
    session_row.reviewed_candidates = total_candidates - pending_candidates

__all__ = []
