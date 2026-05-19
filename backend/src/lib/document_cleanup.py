"""Helpers for removing document-owned curation artifacts safely."""

from __future__ import annotations

from typing import Any
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
    DomainEnvelopeHistory,
    DomainEnvelopeModel,
    DomainEnvelopeObject,
    DomainEnvelopeProjectionIndex,
    DomainValidationFinding,
)


def _scalar_list(result) -> list[Any]:
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
    candidate_ids = (
        _scalar_list(
            session.execute(
                select(CurationCandidate.id).where(
                    CurationCandidate.session_id.in_(session_ids)
                )
            )
        )
        if session_ids
        else []
    )
    extraction_result_ids = _scalar_list(
        session.execute(
            select(ExtractionResultModel.id).where(ExtractionResultModel.document_id == document_id)
        )
    )
    envelope_ids = [
        str(envelope_id)
        for envelope_id in _scalar_list(
            session.execute(
                select(DomainEnvelopeModel.envelope_id).where(
                    DomainEnvelopeModel.document_id == document_id
                )
            )
        )
    ]

    cleared_current_candidate_refs = 0
    deleted_action_logs = 0
    deleted_validation_snapshots = 0
    deleted_submissions = 0
    deleted_evidence_anchors = 0
    deleted_drafts = 0
    deleted_candidates = 0
    deleted_sessions = 0
    cleared_candidate_envelope_refs = 0
    deleted_domain_projection_index = 0
    deleted_domain_history = 0
    deleted_domain_validation_findings = 0
    deleted_domain_objects = 0
    deleted_domain_envelopes = 0

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
                delete(CurationActionLogEntry).where(
                    CurationActionLogEntry.session_id.in_(session_ids)
                )
            ).rowcount
            or 0
        )
        deleted_validation_snapshots = int(
            session.execute(
                delete(CurationValidationSnapshot).where(
                    CurationValidationSnapshot.session_id.in_(session_ids)
                )
            ).rowcount
            or 0
        )
        deleted_submissions = int(
            session.execute(
                delete(CurationSubmissionRecord).where(
                    CurationSubmissionRecord.session_id.in_(session_ids)
                )
            ).rowcount
            or 0
        )

    if candidate_ids:
        deleted_evidence_anchors = int(
            session.execute(
                delete(CurationEvidenceRecord).where(
                    CurationEvidenceRecord.candidate_id.in_(candidate_ids)
                )
            ).rowcount
            or 0
        )
        deleted_drafts = int(
            session.execute(
                delete(CurationDraft).where(CurationDraft.candidate_id.in_(candidate_ids))
            ).rowcount
            or 0
        )

    cleared_candidate_refs = (
        int(
            session.execute(
                update(CurationCandidate)
                .where(CurationCandidate.extraction_result_id.in_(extraction_result_ids))
                .values(extraction_result_id=None)
            ).rowcount
            or 0
        )
        if extraction_result_ids
        else 0
    )
    if session_ids:
        deleted_candidates = int(
            session.execute(
                delete(CurationCandidate).where(CurationCandidate.session_id.in_(session_ids))
            ).rowcount
            or 0
        )

    if envelope_ids:
        cleared_candidate_envelope_refs = int(
            session.execute(
                update(CurationCandidate)
                .where(CurationCandidate.envelope_id.in_(envelope_ids))
                .values(envelope_id=None, object_id=None, envelope_revision=None)
            ).rowcount
            or 0
        )
        deleted_domain_projection_index = int(
            session.execute(
                delete(DomainEnvelopeProjectionIndex).where(
                    DomainEnvelopeProjectionIndex.envelope_id.in_(envelope_ids)
                )
            ).rowcount
            or 0
        )
        deleted_domain_history = int(
            session.execute(
                delete(DomainEnvelopeHistory).where(
                    DomainEnvelopeHistory.envelope_id.in_(envelope_ids)
                )
            ).rowcount
            or 0
        )
        deleted_domain_validation_findings = int(
            session.execute(
                delete(DomainValidationFinding).where(
                    DomainValidationFinding.envelope_id.in_(envelope_ids)
                )
            ).rowcount
            or 0
        )
        deleted_domain_objects = int(
            session.execute(
                delete(DomainEnvelopeObject).where(
                    DomainEnvelopeObject.envelope_id.in_(envelope_ids)
                )
            ).rowcount
            or 0
        )
        deleted_domain_envelopes = int(
            session.execute(
                delete(DomainEnvelopeModel).where(
                    DomainEnvelopeModel.envelope_id.in_(envelope_ids)
                )
            ).rowcount
            or 0
        )

    if session_ids:
        deleted_sessions = int(
            session.execute(
                delete(CurationReviewSession).where(CurationReviewSession.id.in_(session_ids))
            ).rowcount
            or 0
        )
    deleted_extraction_results = (
        int(
            session.execute(
                delete(ExtractionResultModel).where(
                    ExtractionResultModel.id.in_(extraction_result_ids)
                )
            ).rowcount
            or 0
        )
        if extraction_result_ids
        else 0
    )
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
        "candidate_envelope_refs_cleared": cleared_candidate_envelope_refs,
        "domain_projection_index_deleted": deleted_domain_projection_index,
        "domain_history_deleted": deleted_domain_history,
        "domain_validation_findings_deleted": deleted_domain_validation_findings,
        "domain_objects_deleted": deleted_domain_objects,
        "domain_envelopes_deleted": deleted_domain_envelopes,
    }
