"""A paper's files as the viewer shows them, and the per-file viewer routes' URLs."""

from __future__ import annotations

from uuid import UUID

from src.models.sql.pdf_document import (
    DISPLAY_MODE_LOCAL_PDF,
    DISPLAY_MODE_PROVIDER_PDF_PROXY,
    DISPLAY_MODE_TEXT,
    FILE_STATUS_READY,
    PDFDocument,
    PDFDocumentFile,
)
from src.schemas.viewer_files import ViewerFileRef

PDF_DISPLAY_MODES = frozenset({DISPLAY_MODE_LOCAL_PDF, DISPLAY_MODE_PROVIDER_PDF_PROXY})


def file_content_url(document_id: UUID, file_id: UUID) -> str:
    """The authenticated route for one file's stored PDF bytes."""

    return f"/api/pdf-viewer/documents/{document_id}/files/{file_id}/content"


def file_text_url(document_id: UUID, file_id: UUID) -> str:
    """The authenticated route for one text file's display blocks."""

    return f"/api/pdf-viewer/documents/{document_id}/files/{file_id}/text"


def serves_pdf(file_row: PDFDocumentFile) -> bool:
    """Whether the viewer may serve this file's stored PDF now."""

    return file_row.status == FILE_STATUS_READY and file_row.display_mode in PDF_DISPLAY_MODES


def serves_text(file_row: PDFDocumentFile) -> bool:
    """Whether the viewer may serve this file's display blocks now."""

    return file_row.status == FILE_STATUS_READY and file_row.display_mode == DISPLAY_MODE_TEXT


def find_paper_file(document: PDFDocument, file_id: UUID) -> PDFDocumentFile | None:
    """The file with this id among this paper's own files; a file of another paper is never found."""

    return next((file_row for file_row in document.files if file_row.id == file_id), None)


def viewer_file_refs(document: PDFDocument) -> list[ViewerFileRef]:
    """Every file of the paper in tab order, with URLs only for files the viewer can serve."""

    return [
        ViewerFileRef(
            file_id=file_row.id,
            role=file_row.role,
            display_name=file_row.display_name,
            display_mode=file_row.display_mode,
            status=file_row.status,
            tab_order=file_row.tab_order,
            # A file that is not ready may hold a placeholder page count.
            page_count=file_row.page_count if serves_pdf(file_row) else None,
            content_url=file_content_url(document.id, file_row.id) if serves_pdf(file_row) else None,
            text_url=file_text_url(document.id, file_row.id) if serves_text(file_row) else None,
        )
        for file_row in sorted(document.files, key=lambda file_row: file_row.tab_order)
    ]
