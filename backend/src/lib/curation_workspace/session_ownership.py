"""Who may reach a curation review session (ALL-1547).

A review session belongs to the curator who created it: ``created_by_id``
holds that curator's authentication subject. Assignment grants nothing,
sessions cannot be reassigned, and a session without a recorded creator is
visible to nobody.

Enforcement happens at two layers:

* Route guards (``src.api.curation_workspace_access``) call the
  identifier-only ``require_owned_*`` checks here before a handler runs.
  A non-owner gets the same 404 as for an identifier that does not exist.
* The service entry points those routes reach take the caller (an owner
  subject or actor claims) as a required argument and load sessions,
  candidates and submissions only through queries filtered by ``owned_by``.
  Inventory queries filter the same way.

Envelope visibility has one definition, ``envelope_visible_clause``, used by
routes, record history and the Agent Studio tools. Internal work with no
curator request behind it uses the ``*_for_system`` functions, which do not
filter by owner; their callers are background jobs or code that already
checked the owner. Bulk statements that act on all sessions of a document
(document deletion cleanup) are outside this module.
"""

from __future__ import annotations

from typing import Sequence
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


def require_owned_session(db: Session, session_id: str | UUID, owner_sub: str | None) -> UUID:
    """Return the session id when the caller owns it, else 404.

    Reads only the identifier, so no session row is loaded or cached.
    """

    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    found = db.scalar(
        select(CurationReviewSession.id)
        .where(CurationReviewSession.id == normalized_session_id)
        .where(owned_by(owner_sub))
    )
    if found is None:
        raise OwnedResourceNotFound(f"Curation review session {normalized_session_id} not found")
    return found


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
    """Load a session for internal work that has no curator request behind it.

    Callers either run in the background (projection rebuilds) or act after
    their own owner check for a stored owner. Never return the result to a
    request without an owner check.
    """

    return db.get(CurationReviewSession, _normalize_uuid(session_id, field_name="session_id"))


def load_session_adapter_keys_for_system(
    db: Session,
    session_ids: Sequence[UUID],
) -> dict[UUID, str]:
    """Adapter key per session for internal metadata assembly (batch results)."""

    return {
        row.id: row.adapter_key
        for row in db.execute(
            select(CurationReviewSession.id, CurationReviewSession.adapter_key).where(
                CurationReviewSession.id.in_(session_ids)
            )
        ).all()
    }


def require_owned_candidate(db: Session, candidate_id: str | UUID, owner_sub: str | None) -> UUID:
    """Resolve a candidate to the id of the caller's session that holds it, else 404."""

    normalized_candidate_id = _normalize_uuid(candidate_id, field_name="candidate_id")
    found = db.scalar(
        select(CurationReviewSession.id)
        .join(CurationCandidate, CurationCandidate.session_id == CurationReviewSession.id)
        .where(CurationCandidate.id == normalized_candidate_id)
        .where(owned_by(owner_sub))
    )
    if found is None:
        raise OwnedResourceNotFound(f"Curation candidate {normalized_candidate_id} not found")
    return found


def submission_not_found(submission_id: UUID, session_id: UUID) -> OwnedResourceNotFound:
    return OwnedResourceNotFound(
        f"Curation submission {submission_id} not found in session {session_id}"
    )


def require_owned_submission(
    db: Session,
    session_id: str | UUID,
    submission_id: str | UUID,
    owner_sub: str | None,
) -> UUID:
    """404 unless the submission belongs to ``session_id`` and the caller owns it."""

    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_submission_id = _normalize_uuid(submission_id, field_name="submission_id")
    found = db.scalar(
        select(CurationReviewSession.id)
        .join(CurationSubmissionRecord, CurationSubmissionRecord.session_id == CurationReviewSession.id)
        .where(CurationSubmissionRecord.id == normalized_submission_id)
        .where(CurationReviewSession.id == normalized_session_id)
        .where(owned_by(owner_sub))
    )
    if found is None:
        raise submission_not_found(normalized_submission_id, normalized_session_id)
    return found


def require_owned_operation(db: Session, operation_id: str | UUID, owner_sub: str | None) -> UUID:
    """Resolve a workspace review operation to the caller's session id, else 404."""

    normalized_operation_id = str(_normalize_uuid(operation_id, field_name="operation_id"))
    operation_session_id = db.scalar(
        select(WorkspaceReviewOperation.session_id).where(
            WorkspaceReviewOperation.id == normalized_operation_id
        )
    )
    if operation_session_id is None or not session_is_owned(db, operation_session_id, owner_sub):
        raise OwnedResourceNotFound(f"Workspace review operation {normalized_operation_id} not found")
    return UUID(operation_session_id)


def require_owned_snapshot(db: Session, snapshot_id: str | UUID, owner_sub: str | None) -> UUID:
    """Resolve a benchmark snapshot to the caller's session id, else 404."""

    normalized_snapshot_id = _normalize_uuid(snapshot_id, field_name="snapshot_id")
    found = db.scalar(
        select(CurationReviewSession.id)
        .join(CurationBenchmarkSnapshot, CurationBenchmarkSnapshot.session_id == CurationReviewSession.id)
        .where(CurationBenchmarkSnapshot.id == normalized_snapshot_id)
        .where(owned_by(owner_sub))
    )
    if found is None:
        raise OwnedResourceNotFound(f"Benchmark snapshot {normalized_snapshot_id} not found")
    return found


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
                        # The document match lets Postgres use the indexed
                        # document_id; the source id is stored as text.
                        CurationExtractionResultRecord.document_id == DomainEnvelopeModel.document_id,
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
) -> None:
    """404 unless the document is a curator-library document the caller owns.

    Uses the canonical owned-document query, so another user's document and
    benchmark frozen copies both read as missing. Internal flow handoffs on
    frozen copies do not come through here.
    """

    normalized_document_id = _normalize_uuid(document_id, field_name="document_id")
    owner_user_id = (
        db.scalar(select(User.id).where(User.auth_sub == owner_sub)) if owner_sub else None
    )
    owned = owner_user_id is not None and db.scalar(
        select(
            exists(
                owned_documents_select(owner_user_id)
                .where(PDFDocument.id == normalized_document_id)
                .with_only_columns(PDFDocument.id)
            )
        )
    )
    if not owned:
        raise OwnedResourceNotFound(f"Document {normalized_document_id} not found")


__all__ = [
    "OwnedResourceNotFound",
    "envelope_visible_clause",
    "envelope_visible_to",
    "is_owned_by",
    "load_session_adapter_keys_for_system",
    "load_session_for_system",
    "owned_by",
    "owned_session_ids",
    "require_owned_candidate",
    "require_owned_document",
    "require_owned_operation",
    "require_owned_session",
    "require_owned_snapshot",
    "require_owned_submission",
    "require_visible_envelope",
    "session_is_owned",
    "submission_not_found",
]
