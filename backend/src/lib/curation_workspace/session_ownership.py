"""Who may reach a curation review session (ALL-1547).

A review session belongs to the curator who created it: ``created_by_id``
holds that curator's authentication subject. Nobody else can see or change
it, sessions are never shared or reassigned, and a session without a
recorded creator is visible to nobody.

Every caller-facing path loads sessions through the owner-scoped functions
below. A caller that is not the owner gets the same 404 as a caller asking
for something that does not exist, so the response never reveals that the
session exists or who owns it. Background work that runs without a curator
uses ``load_session_for_system`` and must not hand its result to a caller.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import String, and_, exists, false, func, not_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from src.lib.curation_workspace.envelope_visibility import linked_or_non_prepared_envelope
from src.lib.curation_workspace.models import (
    CurationBenchmarkSnapshot,
    CurationCandidate,
    CurationExtractionResultRecord,
    CurationReviewSession,
    CurationSubmissionRecord,
    DomainEnvelopeModel,
)
from src.lib.curation_workspace.session_common import _normalize_uuid
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from src.models.sql.validation_review import WorkspaceReviewOperation
from src.services.document_access import owned_documents_select


class OwnedResourceNotFound(HTTPException):
    """404 for a resource that is missing or belongs to another curator."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def owned_by(owner_sub: str | None) -> ColumnElement[bool]:
    """SQL form of the ownership rule."""

    if not owner_sub:
        return false()
    return CurationReviewSession.created_by_id == owner_sub


def is_owned_by(session_row: CurationReviewSession, owner_sub: str | None) -> bool:
    """Python form of the ownership rule, for a session that is already loaded."""

    return bool(owner_sub) and session_row.created_by_id == owner_sub


def owned_session_ids(owner_sub: str | None):
    """Select the identifiers of every session the caller owns."""

    return select(CurationReviewSession.id).where(owned_by(owner_sub))


def load_owned_session(
    db: Session,
    session_id: str | UUID,
    owner_sub: str | None,
) -> CurationReviewSession:
    """Return the caller's session, or 404 when it is missing or not theirs."""

    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    session_row = db.scalars(
        select(CurationReviewSession)
        .where(CurationReviewSession.id == normalized_session_id)
        .where(owned_by(owner_sub))
    ).first()
    if session_row is None:
        raise OwnedResourceNotFound(f"Curation review session {normalized_session_id} not found")
    return session_row


def session_is_owned(db: Session, session_id: str | UUID, owner_sub: str | None) -> bool:
    """Whether ``session_id`` names a session the caller owns."""

    try:
        normalized_session_id = UUID(str(session_id))
    except ValueError:
        return False
    return bool(
        db.scalar(
            select(
                exists().where(
                    CurationReviewSession.id == normalized_session_id,
                    owned_by(owner_sub),
                )
            )
        )
    )


def load_session_for_system(db: Session, session_id: str | UUID) -> CurationReviewSession | None:
    """Load a session for background work that runs without a curator.

    Only system callers (projection rebuilds, workers acting for a stored
    owner after their own access check) may use this. Never return its result
    to a request without an owner check.
    """

    return db.get(CurationReviewSession, _normalize_uuid(session_id, field_name="session_id"))


def load_owned_candidate_session(
    db: Session,
    candidate_id: str | UUID,
    owner_sub: str | None,
) -> CurationReviewSession:
    """Resolve a candidate to the caller's session that holds it."""

    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    session_row = db.scalars(
        select(CurationReviewSession)
        .join(CurationCandidate, CurationCandidate.session_id == CurationReviewSession.id)
        .where(CurationCandidate.id == normalized_candidate_id)
        .where(owned_by(owner_sub))
    ).first()
    if session_row is None:
        raise OwnedResourceNotFound(f"Curation candidate {normalized_candidate_id} not found")
    return session_row


def load_owned_submission_session(
    db: Session,
    session_id: str | UUID,
    submission_id: str | UUID,
    owner_sub: str | None,
) -> CurationReviewSession:
    """Resolve a submission to the caller's session, which must be ``session_id``."""

    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_submission_id = _normalize_uuid(submission_id, field_name="submission_id")
    session_row = db.scalars(
        select(CurationReviewSession)
        .join(CurationSubmissionRecord, CurationSubmissionRecord.session_id == CurationReviewSession.id)
        .where(CurationSubmissionRecord.id == normalized_submission_id)
        .where(CurationReviewSession.id == normalized_session_id)
        .where(owned_by(owner_sub))
    ).first()
    if session_row is None:
        raise OwnedResourceNotFound(
            f"Curation submission {normalized_submission_id} not found in session "
            f"{normalized_session_id}"
        )
    return session_row


def load_owned_operation_session(
    db: Session,
    operation_id: str | UUID,
    owner_sub: str | None,
) -> CurationReviewSession:
    """Resolve a workspace review operation to the caller's session."""

    normalized_operation_id = str(_normalize_uuid(operation_id, field_name="operation_id"))
    operation_session_id = db.scalar(
        select(WorkspaceReviewOperation.session_id).where(
            WorkspaceReviewOperation.id == normalized_operation_id
        )
    )
    session_row = (
        db.scalars(
            select(CurationReviewSession)
            .where(CurationReviewSession.id == _normalize_uuid(operation_session_id, field_name="session_id"))
            .where(owned_by(owner_sub))
        ).first()
        if operation_session_id is not None
        else None
    )
    if session_row is None:
        raise OwnedResourceNotFound(f"Workspace review operation {normalized_operation_id} not found")
    return session_row


def load_owned_snapshot_session(
    db: Session,
    snapshot_id: str | UUID,
    owner_sub: str | None,
) -> CurationReviewSession:
    """Resolve a benchmark snapshot to the caller's session."""

    normalized_snapshot_id = _normalize_uuid(snapshot_id, field_name="snapshot_id")
    session_row = db.scalars(
        select(CurationReviewSession)
        .join(CurationBenchmarkSnapshot, CurationBenchmarkSnapshot.session_id == CurationReviewSession.id)
        .where(CurationBenchmarkSnapshot.id == normalized_snapshot_id)
        .where(owned_by(owner_sub))
    ).first()
    if session_row is None:
        raise OwnedResourceNotFound(f"Benchmark snapshot {normalized_snapshot_id} not found")
    return session_row


def envelope_visible_clause(owner_sub: str | None) -> ColumnElement[bool]:
    """SQL form of envelope visibility, shared by routes and Agent Studio tools.

    An envelope is visible when any session it is linked to (its own
    ``session_id`` or a session whose candidate projects it) belongs to the
    caller. An envelope linked to no session is visible to the user its
    source extraction result was run for. Unlinked manual preparations are
    never workspace records.
    """

    if not owner_sub:
        return false()
    caller_sessions = owned_session_ids(owner_sub)
    linked_candidates = select(CurationCandidate.id).where(
        CurationCandidate.envelope_id == DomainEnvelopeModel.envelope_id
    )
    return and_(
        linked_or_non_prepared_envelope(),
        or_(
            DomainEnvelopeModel.session_id.in_(caller_sessions),
            exists(linked_candidates.where(CurationCandidate.session_id.in_(caller_sessions))),
            and_(
                DomainEnvelopeModel.session_id.is_(None),
                not_(exists(linked_candidates)),
                exists(
                    select(CurationExtractionResultRecord.id).where(
                        func.cast(CurationExtractionResultRecord.id, String)
                        == DomainEnvelopeModel.source_extraction_result_id,
                        CurationExtractionResultRecord.user_id == owner_sub,
                    )
                ),
            ),
        ),
    )


def envelope_visible_to(db: Session, envelope_id: str, owner_sub: str | None) -> bool:
    """Whether the caller may see the persisted envelope ``envelope_id``."""

    return bool(
        db.scalar(
            select(
                exists().where(
                    DomainEnvelopeModel.envelope_id == envelope_id,
                    envelope_visible_clause(owner_sub),
                )
            )
        )
    )


def require_visible_envelope(db: Session, envelope_id: str, owner_sub: str | None) -> None:
    """404 unless the caller may see the envelope."""

    if not envelope_visible_to(db, envelope_id, owner_sub):
        raise OwnedResourceNotFound(f"Domain envelope {envelope_id} not found")


def require_owned_document(
    db: Session,
    document_id: str | UUID,
    owner_sub: str | None,
) -> PDFDocument:
    """Return a curator-library document the caller owns, or 404.

    Uses the canonical owned-document query, so another user's document and
    benchmark frozen copies both read as missing. Internal flow handoffs on
    frozen copies do not come through here.
    """

    normalized_document_id = _normalize_uuid(document_id, field_name="document_id")
    owner_user_id = (
        db.scalar(select(User.id).where(User.auth_sub == owner_sub)) if owner_sub else None
    )
    document = (
        db.scalars(
            owned_documents_select(owner_user_id).where(PDFDocument.id == normalized_document_id)
        ).first()
        if owner_user_id is not None
        else None
    )
    if document is None:
        raise OwnedResourceNotFound(f"Document {normalized_document_id} not found")
    return document


__all__ = [
    "OwnedResourceNotFound",
    "envelope_visible_clause",
    "envelope_visible_to",
    "is_owned_by",
    "load_owned_candidate_session",
    "load_owned_operation_session",
    "load_owned_session",
    "load_owned_snapshot_session",
    "load_owned_submission_session",
    "load_session_for_system",
    "owned_by",
    "owned_session_ids",
    "require_owned_document",
    "require_visible_envelope",
    "session_is_owned",
]
