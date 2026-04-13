"""Helpers for removing document-owned curation artifacts safely."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    CurationActionLogEntry,
    CurationCandidate,
    CurationDraft,
    CurationEvidenceRecord,
    CurationExtractionResultRecord as ExtractionResultModel,
    CurationReviewSession,
    CurationSubmissionRecord,
    CurationValidationSnapshot,
)


def _scalar_list(result) -> list[UUID]:
    scalars = result.scalars()
    if hasattr(scalars, "all"):
        return list(scalars.all())
    return list(scalars)


def cleanup_document_curation_dependencies(session: Session, document_id: UUID) -> dict[str, int]:
    """Detach and remove curation records that block pdf_documents deletion."""
    session_ids = _scalar_list(
        session.execute(
            select(CurationReviewSession.id).where(CurationReviewSession.document_id == document_id)
        )
    )
    candidate_ids = _scalar_list(
        session.execute(
            select(CurationCandidate.id).where(CurationCandidate.session_id.in_(session_ids))
        )
    ) if session_ids else []
    extraction_result_ids = _scalar_list(
        session.execute(
            select(ExtractionResultModel.id).where(ExtractionResultModel.document_id == document_id)
        )
    )

    cleared_current_candidate_refs = 0
    deleted_action_logs = 0
    deleted_validation_snapshots = 0
    deleted_submissions = 0
    deleted_evidence_anchors = 0
    deleted_drafts = 0
    deleted_candidates = 0
    deleted_sessions = 0

    if session_ids:
        cleared_current_candidate_refs = int(
            session.execute(
                update(CurationReviewSession)
                .where(CurationReviewSession.id.in_(session_ids))
                .values(current_candidate_id=None)
            ).rowcount
            or 0
        )
        deleted_action_logs = int(
            session.execute(
                delete(CurationActionLogEntry).where(CurationActionLogEntry.session_id.in_(session_ids))
            ).rowcount
            or 0
        )
        deleted_validation_snapshots = int(
            session.execute(
                delete(CurationValidationSnapshot).where(CurationValidationSnapshot.session_id.in_(session_ids))
            ).rowcount
            or 0
        )
        deleted_submissions = int(
            session.execute(
                delete(CurationSubmissionRecord).where(CurationSubmissionRecord.session_id.in_(session_ids))
            ).rowcount
            or 0
        )

    if candidate_ids:
        deleted_evidence_anchors = int(
            session.execute(
                delete(CurationEvidenceRecord).where(CurationEvidenceRecord.candidate_id.in_(candidate_ids))
            ).rowcount
            or 0
        )
        deleted_drafts = int(
            session.execute(
                delete(CurationDraft).where(CurationDraft.candidate_id.in_(candidate_ids))
            ).rowcount
            or 0
        )

    cleared_candidate_refs = int(
        session.execute(
            update(CurationCandidate)
            .where(CurationCandidate.extraction_result_id.in_(extraction_result_ids))
            .values(extraction_result_id=None)
        ).rowcount
        or 0
    ) if extraction_result_ids else 0
    if session_ids:
        deleted_candidates = int(
            session.execute(
                delete(CurationCandidate).where(CurationCandidate.session_id.in_(session_ids))
            ).rowcount
            or 0
        )
        deleted_sessions = int(
            session.execute(
                delete(CurationReviewSession).where(CurationReviewSession.id.in_(session_ids))
            ).rowcount
            or 0
        )
    deleted_extraction_results = int(
        session.execute(
            delete(ExtractionResultModel).where(ExtractionResultModel.id.in_(extraction_result_ids))
        ).rowcount
        or 0
    ) if extraction_result_ids else 0
    return {
        "current_candidate_refs_cleared": cleared_current_candidate_refs,
        "candidate_refs_cleared": cleared_candidate_refs,
        "action_logs_deleted": deleted_action_logs,
        "validation_snapshots_deleted": deleted_validation_snapshots,
        "submissions_deleted": deleted_submissions,
        "evidence_anchors_deleted": deleted_evidence_anchors,
        "drafts_deleted": deleted_drafts,
        "candidates_deleted": deleted_candidates,
        "sessions_deleted": deleted_sessions,
        "extraction_results_deleted": deleted_extraction_results,
    }
