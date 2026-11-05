"""Find, link and read a document's reference in its literature source.

A document's reference comes from one of two places. An import records where the
file came from in the ``source_provider_*`` columns. A curator link says which
paper an uploaded file is, and is kept apart so the file still reads as an upload
everywhere that cares about its origin. Linking refuses imported documents, so a
document never has both.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Mapping
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationReviewSession as ReviewSessionModel,
)
from src.lib.curation_workspace.session_common import build_actor_claims_payload
from src.lib.document_sources.access import build_document_source_request_context
from src.lib.document_sources.identifier_import import normalize_source_identifier
from src.lib.document_sources.models import (
    DocumentSourceAccessDenied,
    DocumentSourceError,
    DocumentSourceReferenceNotFound,
)
from src.lib.document_sources.registry import get_configured_document_source_provider
from src.lib.observability.runtime import report_runtime_exception
from src.lib.packages.document_source_provider_models import DevelopmentReaderUnavailable
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationActionType,
    CurationActorType,
    CurationDocumentReferenceResult,
    CurationDocumentReferenceSource,
    CurationDocumentReferenceStatus,
)

logger = logging.getLogger(__name__)

_NOT_FOUND_MESSAGE = (
    "We could not find this paper in the literature source. "
    "Check the identifier. If it is right, the paper may need to be added there first."
)
_TOKEN_UNAVAILABLE_MESSAGE = (
    "Your sign-in could not be used to reach the literature source. "
    "Sign in again and retry."
)
_SOURCE_UNAVAILABLE_MESSAGE = "The literature source is unavailable. Try again later."
_ACCESS_DENIED_MESSAGE = "You do not have access to this paper in the literature source."
_IMPORTED_DETAIL = "This paper came from the literature source, so its reference cannot be changed."
_ALREADY_LINKED_DETAIL = "This paper is already linked. Unlink it before linking another paper."
_NOT_LINKED_DETAIL = "This paper has no link to remove."


@dataclass(frozen=True)
class EffectiveDocumentReference:
    curie: str
    source: CurationDocumentReferenceSource


def effective_document_reference(document: Any) -> EffectiveDocumentReference | None:
    """Return the document's reference and where it came from, if it has one."""

    if document.source_provider_reference_curie:
        return EffectiveDocumentReference(
            curie=document.source_provider_reference_curie,
            source=CurationDocumentReferenceSource.IMPORTED,
        )
    if document.linked_reference_curie:
        return EffectiveDocumentReference(
            curie=document.linked_reference_curie,
            source=CurationDocumentReferenceSource.CURATOR_LINK,
        )
    return None


def _has_imported_reference(document: PDFDocument) -> bool:
    return bool(document.source_provider_reference_id or document.source_provider_reference_curie)


def _session_or_404(db: Session, session_id: str | UUID) -> ReviewSessionModel:
    session = db.get(ReviewSessionModel, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curation review session {session_id} not found",
        )
    return session


def _document_or_404(
    db: Session,
    document_id: UUID,
    *,
    for_update: bool = False,
) -> PDFDocument:
    query = select(PDFDocument).where(PDFDocument.id == document_id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    document = db.scalars(query).one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session document was not found",
        )
    return document


def _refuse_link(document: PDFDocument) -> None:
    if _has_imported_reference(document):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_IMPORTED_DETAIL)
    if document.linked_reference_curie:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_ALREADY_LINKED_DETAIL)


def require_document_can_be_linked(db: Session, session_id: str | UUID) -> None:
    """Refuse before a lookup when the session's document cannot take a link."""

    session = _session_or_404(db, session_id)
    _refuse_link(_document_or_404(db, session.document_id))


async def find_document_reference(
    identifier: str,
    *,
    request: Request | None,
    user_claims: Mapping[str, Any],
) -> CurationDocumentReferenceResult:
    """Look up one identifier in the configured source. Saves nothing."""

    try:
        context = await build_document_source_request_context(
            request=request,
            user_claims=user_claims,
        )
    except DevelopmentReaderUnavailable:
        logger.warning("Development document-source reader unavailable for reference lookup")
        return _failure(
            CurationDocumentReferenceStatus.TOKEN_UNAVAILABLE,
            _TOKEN_UNAVAILABLE_MESSAGE,
        )
    token = (context.curator_token or "").strip()
    provider = None
    try:
        provider = get_configured_document_source_provider()
        normalized = normalize_source_identifier(identifier, provider=provider)
        if not normalized.is_valid or normalized.normalized is None:
            return _failure(
                CurationDocumentReferenceStatus.INVALID_IDENTIFIER,
                normalized.error or "This does not look like a valid identifier.",
            )
        if not token:
            return _failure(
                CurationDocumentReferenceStatus.TOKEN_UNAVAILABLE,
                _TOKEN_UNAVAILABLE_MESSAGE,
            )
        reference = await provider.resolve_reference(
            normalized.normalized,
            request_bearer_token=token,
        )
    except DocumentSourceReferenceNotFound:
        return _failure(CurationDocumentReferenceStatus.NOT_FOUND, _NOT_FOUND_MESSAGE)
    except DocumentSourceAccessDenied:
        return _failure(CurationDocumentReferenceStatus.ACCESS_DENIED, _ACCESS_DENIED_MESSAGE)
    except DocumentSourceError as exc:
        logger.warning(
            "Document-source reference lookup failed: %s",
            type(exc).__name__,
            extra={"sentry_skip_event": True},
        )
        report_runtime_exception(
            exc,
            component="document_reference",
            operation="find_document_reference",
        )
        return _failure(
            CurationDocumentReferenceStatus.SOURCE_UNAVAILABLE,
            _SOURCE_UNAVAILABLE_MESSAGE,
        )
    finally:
        if provider is not None:
            try:
                await provider.aclose()
            except Exception as cleanup_err:
                logger.warning(
                    "Best-effort document-source provider cleanup failed: %s",
                    type(cleanup_err).__name__,
                )

    # A provider can know an identifier from elsewhere (such as PubMed) without
    # holding the paper. Only a reference id means the source has it.
    if not reference.reference_curie:
        return _failure(CurationDocumentReferenceStatus.NOT_FOUND, _NOT_FOUND_MESSAGE)
    return CurationDocumentReferenceResult(
        status=CurationDocumentReferenceStatus.FOUND,
        message="Check that this is the same paper before linking it.",
        reference_curie=reference.reference_curie,
        reference_title=reference.title,
        provider=reference.provider,
    )


def _failure(
    result_status: CurationDocumentReferenceStatus,
    message: str,
) -> CurationDocumentReferenceResult:
    return CurationDocumentReferenceResult(status=result_status, message=message)


def _log_change(
    db: Session,
    *,
    session: ReviewSessionModel,
    action_type: CurationActionType,
    occurred_at: datetime,
    actor_claims: dict[str, Any],
    message: str,
    metadata: dict[str, Any],
) -> None:
    session.session_version += 1
    session.updated_at = occurred_at
    session.last_worked_at = occurred_at
    db.add(session)
    db.add(
        SessionActionLogModel(
            session_id=session.id,
            action_type=action_type,
            actor_type=CurationActorType.USER,
            actor=build_actor_claims_payload(actor_claims),
            occurred_at=occurred_at,
            message=message,
            action_metadata=metadata,
        )
    )
    db.flush()


def link_document_reference(
    db: Session,
    session_id: str | UUID,
    found: CurationDocumentReferenceResult,
    *,
    actor_claims: dict[str, Any],
) -> CurationDocumentReferenceResult:
    """Save a reference the server just resolved, without committing ``db``."""

    session = _session_or_404(db, session_id)
    document = _document_or_404(db, session.document_id, for_update=True)
    _refuse_link(document)
    now = datetime.now(timezone.utc)
    document.linked_reference_provider = found.provider
    document.linked_reference_curie = found.reference_curie
    document.linked_reference_at = now
    db.add(document)
    _log_change(
        db,
        session=session,
        action_type=CurationActionType.DOCUMENT_REFERENCE_LINKED,
        occurred_at=now,
        actor_claims=actor_claims,
        message=f"Linked this paper to {found.reference_curie}",
        metadata={
            "reference_curie": found.reference_curie,
            "reference_title": found.reference_title,
            "provider": found.provider,
        },
    )
    return found.model_copy(
        update={
            "status": CurationDocumentReferenceStatus.LINKED,
            "message": f"Linked this paper to {found.reference_curie}.",
        }
    )


def unlink_document_reference(
    db: Session,
    session_id: str | UUID,
    *,
    actor_claims: dict[str, Any],
) -> CurationDocumentReferenceResult:
    """Remove a curator link, without committing ``db``."""

    session = _session_or_404(db, session_id)
    document = _document_or_404(db, session.document_id, for_update=True)
    if _has_imported_reference(document):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_IMPORTED_DETAIL)
    if not document.linked_reference_curie:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NOT_LINKED_DETAIL)
    removed_curie = document.linked_reference_curie
    removed_provider = document.linked_reference_provider
    now = datetime.now(timezone.utc)
    document.linked_reference_provider = None
    document.linked_reference_curie = None
    document.linked_reference_at = None
    db.add(document)
    _log_change(
        db,
        session=session,
        action_type=CurationActionType.DOCUMENT_REFERENCE_UNLINKED,
        occurred_at=now,
        actor_claims=actor_claims,
        message=f"Removed this paper's link to {removed_curie}",
        metadata={"reference_curie": removed_curie, "provider": removed_provider},
    )
    return CurationDocumentReferenceResult(
        status=CurationDocumentReferenceStatus.UNLINKED,
        message=f"Removed this paper's link to {removed_curie}.",
    )


__all__ = [
    "EffectiveDocumentReference",
    "effective_document_reference",
    "find_document_reference",
    "link_document_reference",
    "require_document_can_be_linked",
    "unlink_document_reference",
]
