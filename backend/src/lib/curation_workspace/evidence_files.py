"""The paper file an evidence anchor quotes (ALL-1460).

Every evidence anchor written since 0.12.0 names one of its paper's
``pdf_document_files`` rows. These checks are structural: the id must be a file
of the session's paper. They never look at the quoted text.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from src.models.sql.pdf_document import PDFDocumentFile


def require_paper_file(
    db: Session,
    *,
    document_id: UUID,
    file_id: str | None,
) -> PDFDocumentFile:
    """Return the session paper's file named by ``file_id``, or refuse with 400."""

    if file_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="file_id is required: evidence names the paper file it quotes",
        )
    not_in_paper = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="file_id is not a file of this paper",
    )
    try:
        parsed_file_id = UUID(str(file_id))
    except ValueError as exc:
        raise not_in_paper from exc
    file_row = db.get(PDFDocumentFile, parsed_file_id)
    if file_row is None or file_row.document_id != document_id:
        raise not_in_paper
    return file_row


def file_chunk_index_range(file_row: PDFDocumentFile) -> tuple[int, int]:
    """The file's piece numbers in the search index, or 409 when it has none yet."""

    if file_row.chunk_index_start is None or file_row.chunk_index_end is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This file has no text to search yet",
        )
    return file_row.chunk_index_start, file_row.chunk_index_end


__all__ = ["file_chunk_index_range", "require_paper_file"]
