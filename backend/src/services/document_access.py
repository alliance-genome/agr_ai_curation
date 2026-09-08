"""Canonical ownership policy for tenant PDF documents."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from src.models.sql.pdf_document import PDFDocument


def owned_documents_select(owner_user_id: int) -> Select[tuple[PDFDocument]]:
    """Build the database-scoped query for documents visible to one owner.

    Documents without an owner and documents owned by another user are deliberately
    excluded. ``source_access_scope`` is provenance metadata, not an access grant.
    """
    return select(PDFDocument).where(
        PDFDocument.user_id == owner_user_id,
        PDFDocument.viewer_mode.is_distinct_from("benchmark_frozen"),
    )


def require_owned_document(
    db: Session,
    document_id: UUID,
    owner_user_id: int,
    *,
    for_update: bool = False,
) -> PDFDocument:
    """Return a document only when the authenticated database user owns it.

    Missing documents return 404. Existing documents owned by another user,
    including legacy rows with a null owner, return 403.
    """
    statement = select(PDFDocument).where(PDFDocument.id == document_id)
    if for_update:
        statement = statement.with_for_update()
    document = db.execute(statement).scalar_one_or_none()
    # Benchmark runtime copies belong to a curator for normal internal tool
    # scoping, but are not editable/reprocessable curator-library documents.
    # Internal ingestion and document tools have separate owned read paths.
    if document is None or document.viewer_mode == "benchmark_frozen":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID {document_id} not found",
        )
    if document.user_id != owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this document",
        )
    return document


def exclude_benchmark_document(db: Session, document_id: str | UUID | None) -> None:
    """Exclude frozen copies at curator execution boundaries, not internal tools.

    This is not a replacement for document ownership or request validation.
    Non-UUID inputs cannot identify a persisted frozen copy and retain their
    existing downstream validation behavior.
    """
    if not document_id:
        return
    try:
        parsed_id = document_id if isinstance(document_id, UUID) else UUID(document_id)
    except ValueError:
        return
    mode = db.scalar(select(PDFDocument.viewer_mode).where(PDFDocument.id == parsed_id))
    if mode == "benchmark_frozen":
        raise HTTPException(status_code=404, detail="Document not found")


def protected_pdf_url(document_id: UUID) -> str:
    """Return the stable authenticated API route for a document's PDF bytes."""
    return f"/api/pdf-viewer/documents/{document_id}/content"
