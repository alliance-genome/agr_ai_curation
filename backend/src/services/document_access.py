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
    return select(PDFDocument).where(PDFDocument.user_id == owner_user_id)


def require_owned_document(
    db: Session,
    document_id: UUID,
    owner_user_id: int,
) -> PDFDocument:
    """Return a document only when the authenticated database user owns it.

    Missing documents return 404. Existing documents owned by another user,
    including legacy rows with a null owner, return 403.
    """
    document = db.execute(
        select(PDFDocument).where(PDFDocument.id == document_id)
    ).scalar_one_or_none()
    if document is None:
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


def protected_pdf_url(document_id: UUID) -> str:
    """Return the stable authenticated API route for a document's PDF bytes."""
    return f"/api/pdf-viewer/documents/{document_id}/content"
