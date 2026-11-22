"""A paper's files: main-file lookup, piece-number ranges, text size and file status.

Every paper (``pdf_documents``) holds one or more files (``pdf_document_files``).
Each file's pieces in the search index are the paper's ``chunkIndex`` values
``chunk_index_start..chunk_index_end``. A range is reserved in one short
transaction that locks the paper row and advances ``next_chunk_index``, and
numbers are never reused.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.sql.pdf_document import (
    FILE_ROLE_MAIN,
    FILE_STATUS_FAILED,
    FILE_STATUS_PENDING,
    FILE_STATUS_READY,
    MissingMainFileError,
    PDFDocument,
    PDFDocumentFile,
)


class DocumentFileError(RuntimeError):
    """A file row is not in the state an operation requires."""


class ChunkRangeUnavailableError(DocumentFileError):
    """The paper's next piece number has not been measured, so no range can be reserved."""


class FileRangeMissingError(DocumentFileError):
    """A ready file has no recorded piece range; nothing guesses one."""


def element_text_bytes(elements: Iterable[Mapping[str, Any]]) -> int:
    """UTF-8 bytes of the text that is cut into pieces.

    This is the one definition of a file's text size, the same for text
    imports and for PDFs: the ``text`` of every parsed element that chunking
    keeps (``is_chunked_element``), after clean-up, as saved in the file's
    processed-elements JSON. It is never the size of that JSON file.
    """

    from src.lib.pipeline.chunk import is_chunked_element

    total = 0
    for element in elements:
        if not isinstance(element, Mapping):
            raise DocumentFileError("A processed element is not an object")
        if not is_chunked_element(dict(element)):
            continue
        text = element.get("text")
        if not isinstance(text, str):
            raise DocumentFileError("A processed element has no text string")
        total += len(text.encode("utf-8"))
    return total


def main_file_query(document_id: UUID):
    return select(PDFDocumentFile).where(
        PDFDocumentFile.document_id == document_id,
        PDFDocumentFile.role == FILE_ROLE_MAIN,
    )


def require_main_file(session: Session, document_id: UUID | str, *, for_update: bool = False) -> PDFDocumentFile:
    statement = main_file_query(UUID(str(document_id)))
    if for_update:
        statement = statement.with_for_update()
    main_file = session.execute(statement).scalar_one_or_none()
    if main_file is None:
        raise MissingMainFileError(document_id)
    return main_file


def main_file_id(document_id: UUID | str) -> UUID:
    from src.models.sql.database import SessionLocal

    with SessionLocal() as session:
        return require_main_file(session, document_id).id


def reserve_file_chunk_range(document_id: UUID | str, file_id: UUID, chunk_count: int) -> int:
    """Reserve ``chunk_count`` piece numbers for one file and return the first.

    Locks the paper row, takes ``next_chunk_index`` as the start, records the
    range on the file and advances the counter, then commits. A paper's first
    file therefore starts at 0.
    """

    from src.models.sql.database import SessionLocal

    if type(chunk_count) is not int or chunk_count < 1:
        raise DocumentFileError("A file's piece range needs at least one piece")
    with SessionLocal() as session, session.begin():
        paper = session.execute(
            select(PDFDocument).where(PDFDocument.id == UUID(str(document_id))).with_for_update()
        ).scalar_one_or_none()
        if paper is None:
            raise DocumentFileError(f"Document {document_id} does not exist")
        if paper.next_chunk_index is None:
            raise ChunkRangeUnavailableError(
                f"Document {document_id} has no measured next piece number; "
                "run the paper-files fill step first"
            )
        file_row = session.get(PDFDocumentFile, file_id, with_for_update=True)
        if file_row is None or file_row.document_id != paper.id:
            raise DocumentFileError(f"File {file_id} is not a file of document {document_id}")
        if file_row.chunk_index_start is not None or file_row.status != FILE_STATUS_PENDING:
            raise DocumentFileError(f"File {file_id} already has a piece range or is not pending")
        start = paper.next_chunk_index
        file_row.chunk_index_start = start
        file_row.chunk_index_end = start + chunk_count - 1
        file_row.chunk_count = chunk_count
        paper.next_chunk_index = start + chunk_count
        return start


def require_chunk_range(file_row: PDFDocumentFile) -> tuple[int, int]:
    start, end = file_row.chunk_index_start, file_row.chunk_index_end
    if start is None or end is None or file_row.chunk_count is None:
        raise FileRangeMissingError(f"File {file_row.id} has no recorded piece range")
    return start, end


def mark_file_pending(file_id: UUID) -> None:
    """Record that a file's provider import is running; the file is not readable yet."""

    _update_file(file_id, status=FILE_STATUS_PENDING, import_status="processing", error_message=None)


def mark_file_ready(file_id: UUID, *, text_bytes: int) -> None:
    """Record a stored file as readable, with its measured text size."""

    from src.models.sql.database import SessionLocal

    if type(text_bytes) is not int or text_bytes < 0:
        raise DocumentFileError("A ready file needs a measured text size")
    with SessionLocal() as session, session.begin():
        file_row = session.get(PDFDocumentFile, file_id, with_for_update=True)
        if file_row is None:
            raise DocumentFileError(f"File {file_id} does not exist")
        require_chunk_range(file_row)
        file_row.status = FILE_STATUS_READY
        if file_row.source_import_status is not None:
            file_row.source_import_status = "completed"
        file_row.error_message = None
        file_row.text_bytes = text_bytes


def mark_files_ready(text_bytes_by_file: Mapping[UUID, int]) -> None:
    """Record several stored files as readable together, in one transaction (an add's files)."""

    from src.models.sql.database import SessionLocal

    if not text_bytes_by_file:
        raise DocumentFileError("No files to mark ready")
    with SessionLocal() as session, session.begin():
        for file_id in sorted(text_bytes_by_file, key=str):
            text_bytes = text_bytes_by_file[file_id]
            if type(text_bytes) is not int or text_bytes < 0:
                raise DocumentFileError("A ready file needs a measured text size")
            file_row = session.get(PDFDocumentFile, file_id, with_for_update=True)
            if file_row is None:
                raise DocumentFileError(f"File {file_id} does not exist")
            if file_row.status != FILE_STATUS_PENDING:
                raise DocumentFileError(f"File {file_id} is not pending")
            require_chunk_range(file_row)
            file_row.status = FILE_STATUS_READY
            if file_row.source_import_status is not None:
                file_row.source_import_status = "completed"
            file_row.error_message = None
            file_row.text_bytes = text_bytes


def mark_file_failed(file_id: UUID, *, error_message: str) -> None:
    _update_file(file_id, status=FILE_STATUS_FAILED, import_status="failed", error_message=error_message)


def _update_file(file_id: UUID, *, status: str, import_status: str, error_message: str | None) -> None:
    from src.lib.openai_agents.config import get_pdf_document_error_message_max_chars
    from src.models.sql.database import SessionLocal

    with SessionLocal() as session, session.begin():
        file_row = session.get(PDFDocumentFile, file_id, with_for_update=True)
        if file_row is None:
            raise DocumentFileError(f"File {file_id} does not exist")
        file_row.status = status
        # Only files with a provider import carry an import status (it starts
        # "pending" when the import is planned); plain uploads keep NULL.
        if file_row.source_import_status is not None:
            file_row.source_import_status = import_status
        file_row.error_message = (
            None if error_message is None else error_message[:get_pdf_document_error_message_max_chars()]
        )


def ready_piece_total(session: Session, document_id: UUID | str) -> int:
    """Sum of the piece counts of the paper's ready files."""

    total = session.scalar(
        select(func.coalesce(func.sum(PDFDocumentFile.chunk_count), 0)).where(
            PDFDocumentFile.document_id == UUID(str(document_id)),
            PDFDocumentFile.status == FILE_STATUS_READY,
        )
    )
    return int(total)


def owned_paper_by_main_file_hash(owner_user_id: int, hashes: Iterable[str]):
    """Query for the owner's paper whose main file has one of ``hashes`` (duplicate uploads)."""

    return (
        select(PDFDocument)
        .join(
            PDFDocumentFile,
            (PDFDocumentFile.document_id == PDFDocument.id)
            & (PDFDocumentFile.role == FILE_ROLE_MAIN),
        )
        .where(
            PDFDocument.user_id == owner_user_id,
            PDFDocumentFile.file_hash.in_(list(hashes)),
        )
    )
