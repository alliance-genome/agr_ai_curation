"""Which of a paper's files an agent run may read, as piece-number ranges.

A paper's pieces in the search index are numbered across all its files; each
file owns the range ``chunk_index_start..chunk_index_end``. A run reads only
the ranges of its readable files: the paper's ready files, or only its ready
main file when the run's reading scope says so (batches). Files being added,
failed files and removed files are never readable.

Every read function takes a ``ReadableFiles`` value and keeps to its ranges.
The value is resolved on the server, never from model input.
"""

from __future__ import annotations

import enum
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.document_files.service import require_chunk_range
from src.models.sql.pdf_document import FILE_ROLE_MAIN, FILE_STATUS_READY, PDFDocument, PDFDocumentFile
from src.models.sql.user import User


class ReadingScope(enum.Enum):
    """The run's rule for which ready files it reads."""

    ALL_READY_FILES = "all_ready_files"
    MAIN_FILE_ONLY = "main_file_only"


# Chat and single-paper flows read every ready file; batch runs set MAIN_FILE_ONLY.
_READING_SCOPE: ContextVar[ReadingScope] = ContextVar(
    "document_reading_scope", default=ReadingScope.ALL_READY_FILES,
)


def current_reading_scope() -> ReadingScope:
    return _READING_SCOPE.get()


@contextmanager
def reading_scope(scope: ReadingScope) -> Iterator[None]:
    """Run the enclosed agent work under ``scope``."""

    if not isinstance(scope, ReadingScope):
        raise TypeError("reading_scope needs a ReadingScope")
    token = _READING_SCOPE.set(scope)
    try:
        yield
    finally:
        _READING_SCOPE.reset(token)


class ReadableFilesError(RuntimeError):
    """The paper's readable files could not be determined."""


@dataclass(frozen=True, slots=True)
class ReadableFile:
    file_id: str
    display_name: str
    role: str
    chunk_index_start: int
    chunk_index_end: int

    def contains(self, chunk_index: int) -> bool:
        return self.chunk_index_start <= chunk_index <= self.chunk_index_end


@dataclass(frozen=True, slots=True)
class ReadableFiles:
    """The readable files of one paper, in tab order, with their piece ranges."""

    document_id: str
    files: tuple[ReadableFile, ...]

    def contains(self, chunk_index: int) -> bool:
        return self.file_for(chunk_index) is not None

    def file_for(self, chunk_index: int) -> ReadableFile | None:
        if type(chunk_index) is not int:
            return None
        for file in self.files:
            if file.contains(chunk_index):
                return file
        return None

    @property
    def fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        """Identity of the readable set, for caches built from it."""

        return tuple((file.file_id, file.chunk_index_start, file.chunk_index_end) for file in self.files)

    def ranges_text(self) -> str:
        """The readable ranges as the model is told them.

        One file: "0 to 41". Several: "0 to 41 (name), 42 to 60 (name)", in piece order.
        """

        ordered = sorted(self.files, key=lambda file: file.chunk_index_start)
        if len(ordered) == 1:
            only = ordered[0]
            return f"{only.chunk_index_start} to {only.chunk_index_end}"
        return ", ".join(
            f"{file.chunk_index_start} to {file.chunk_index_end} ({file.display_name})" for file in ordered
        )


def readable_files_in_session(
    session: Session,
    document_id: str,
    owner_auth_sub: str,
    *,
    scope: ReadingScope | None = None,
) -> ReadableFiles:
    """Resolve the owner's paper's readable files inside the caller's transaction."""

    scope = current_reading_scope() if scope is None else scope
    paper_id = UUID(str(document_id))
    owned = session.scalar(
        select(PDFDocument.id).join(User, User.id == PDFDocument.user_id).where(
            PDFDocument.id == paper_id, User.auth_sub == owner_auth_sub,
        )
    )
    if owned is None:
        raise ReadableFilesError(f"Document {document_id} is not a paper of this user")
    statement = select(PDFDocumentFile).where(
        PDFDocumentFile.document_id == paper_id,
        PDFDocumentFile.status == FILE_STATUS_READY,
    )
    if scope is ReadingScope.MAIN_FILE_ONLY:
        statement = statement.where(PDFDocumentFile.role == FILE_ROLE_MAIN)
    rows = session.execute(statement.order_by(PDFDocumentFile.tab_order)).scalars().all()
    files = []
    for row in rows:
        start, end = require_chunk_range(row)
        files.append(ReadableFile(str(row.id), row.display_name, row.role, start, end))
    # Keep the caller's spelling: read functions compare it with the documentId they filter on.
    return ReadableFiles(str(document_id), tuple(files))


def resolve_readable_files(document_id: str, owner_auth_sub: str) -> ReadableFiles:
    """Resolve readable files in a short read of their own (outside a tool call)."""

    from src.models.sql.database import SessionLocal

    with SessionLocal() as session:
        return readable_files_in_session(session, document_id, owner_auth_sub)


__all__ = [
    "ReadableFile",
    "ReadableFiles",
    "ReadableFilesError",
    "ReadingScope",
    "current_reading_scope",
    "readable_files_in_session",
    "reading_scope",
    "resolve_readable_files",
]
