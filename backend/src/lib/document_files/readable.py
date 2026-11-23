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
from src.models.sql.pdf_document import (
    DISPLAY_MODE_TEXT,
    FILE_ROLE_MAIN,
    FILE_STATUS_PENDING,
    FILE_STATUS_READY,
    PDFDocument,
    PDFDocumentFile,
)
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


# How a file's kind is shown to the model, from its stored display mode and the
# original's file extension (structural metadata, never the file's text).
FILE_KIND_PDF = "PDF"
FILE_KIND_TEXT = "text"
_TEXT_FILE_KINDS = {"docx": "Word", "doc": "Word", "xlsx": "Excel", "xls": "Excel", "csv": "CSV"}


def file_kind(display_mode: str, source_file_extension: str | None) -> str:
    if display_mode != DISPLAY_MODE_TEXT:
        return FILE_KIND_PDF
    extension = (source_file_extension or "").lower().lstrip(".")
    return _TEXT_FILE_KINDS.get(extension, FILE_KIND_TEXT)


def file_pages_trusted(display_mode: str, source_markdown_path: str | None) -> bool:
    """Whether a file's pieces carry real page numbers (ALL-1460).

    Only a file whose text the extraction service produced from its own PDF has
    real page data. A file whose pieces were built from provider-converted text
    (it has stored provider Markdown, ``source_markdown_path``: every provider
    Markdown import since v0.9.7 records it) or that is shown as text never
    yields a page, whatever page its stored pieces carry. Structural only.
    """

    return display_mode != DISPLAY_MODE_TEXT and source_markdown_path is None


def file_labels(display_names: list[str]) -> list[str]:
    """The name the model sees for each of a paper's files, given in tab order.

    Numbered over ALL of the paper's files (any status), so a file's label does
    not change when another file becomes ready during a run. A name shared by
    several files gets " (2)", " (3)" ... on its later files, skipping any suffix
    that is itself the name of another file, so every label names one file.
    """

    taken = set(display_names)
    used: set[str] = set()
    labels = []
    for name in display_names:
        label = name
        if label in used:
            number = 2
            while f"{name} ({number})" in used | taken:
                number += 1
            label = f"{name} ({number})"
        used.add(label)
        labels.append(label)
    return labels


@dataclass(frozen=True, slots=True)
class ReadableFile:
    file_id: str
    display_name: str
    role: str
    chunk_index_start: int
    chunk_index_end: int
    kind: str
    # The name the model sees and passes back as ``file`` (see ``file_labels``).
    label: str
    # Whether its pieces' pages are real (``file_pages_trusted``); otherwise no page is shown or recorded.
    pages_trusted: bool

    def page_of(self, page: int | None) -> int | None:
        """A piece's page as it may be shown or recorded: ``None`` for a file without real pages."""

        return page if self.pages_trusted else None

    def contains(self, chunk_index: int) -> bool:
        return self.chunk_index_start <= chunk_index <= self.chunk_index_end

    @property
    def chunk_count(self) -> int:
        return self.chunk_index_end - self.chunk_index_start + 1


@dataclass(frozen=True, slots=True)
class ReadableFiles:
    """The readable files of one paper, in tab order, with their piece ranges."""

    document_id: str
    files: tuple[ReadableFile, ...]
    # Files of the paper being added right now (pending rows): never readable,
    # only counted so the model can be told. A dead add's rows stop counting
    # when the reconciler marks them failed. Not part of the fingerprint.
    files_being_added: int = 0
    # A living import of the paper is waiting for ABC to convert its supplements
    # (``supplement_wait_active``). Read per run like files_being_added; never in
    # main-file-only scope; not part of the fingerprint.
    supplements_converting: bool = False

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

    def labelled(self) -> tuple[tuple[str, ReadableFile], ...]:
        """Each readable file with the name the model sees and passes back as ``file``, in tab order."""

        return tuple((file.label, file) for file in self.files)

    def label_of(self, file: ReadableFile) -> str:
        return next(label for label, each in self.labelled() if each == file)

    def by_label(self, label: str) -> ReadableFile | None:
        return next((file for each, file in self.labelled() if each == label), None)

    def only(self, file: ReadableFile) -> "ReadableFiles":
        """The same paper with ``file`` as its only readable file."""

        if file not in self.files:
            raise ReadableFilesError("The file is not one of this paper's readable files")
        return ReadableFiles(self.document_id, (file,), self.files_being_added, self.supplements_converting)

    def ranges_text(self) -> str:
        """The readable ranges as the model is told them.

        One file: "0 to 41". Several: "0 to 41 (name), 42 to 60 (name)", in piece order.
        """

        ordered = sorted(self.files, key=lambda file: file.chunk_index_start)
        if len(ordered) == 1:
            only = ordered[0]
            return f"{only.chunk_index_start} to {only.chunk_index_end}"
        return ", ".join(
            f"{file.chunk_index_start} to {file.chunk_index_end} ({self.label_of(file)})" for file in ordered
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
    # Every file of the paper, so labels are numbered the same way whatever is ready.
    rows = session.execute(
        select(PDFDocumentFile).where(PDFDocumentFile.document_id == paper_id)
        .order_by(PDFDocumentFile.tab_order)
    ).scalars().all()
    labels = file_labels([row.display_name for row in rows])
    in_scope = [
        (row, label) for row, label in zip(rows, labels)
        if scope is not ReadingScope.MAIN_FILE_ONLY or row.role == FILE_ROLE_MAIN
    ]
    files = []
    for row, label in in_scope:
        if row.status != FILE_STATUS_READY:
            continue
        start, end = require_chunk_range(row)
        files.append(ReadableFile(
            str(row.id), row.display_name, row.role, start, end,
            file_kind(row.display_mode, row.source_file_extension), label,
            file_pages_trusted(row.display_mode, row.source_markdown_path),
        ))
    being_added = sum(1 for row, _label in in_scope if row.status == FILE_STATUS_PENDING)
    converting = False
    if scope is not ReadingScope.MAIN_FILE_ONLY:
        from src.lib.pdf_jobs.service import supplement_wait_active

        converting = supplement_wait_active(session, paper_id)
    # Keep the caller's spelling: read functions compare it with the documentId they filter on.
    return ReadableFiles(str(document_id), tuple(files), being_added, converting)


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
    "file_pages_trusted",
    "readable_files_in_session",
    "reading_scope",
    "resolve_readable_files",
]
