"""A paper's files: main-file lookup, piece-number ranges, text size and file status.

Every paper (``pdf_documents``) holds one or more files (``pdf_document_files``).
Each file's pieces in the search index are the paper's ``chunkIndex`` values
``chunk_index_start..chunk_index_end``. A range is reserved in one short
transaction that locks the paper row and advances ``next_chunk_index``, and
numbers are never reused.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.sql.pdf_document import (
    DISPLAY_MODE_TEXT,
    FILE_ROLE_MAIN,
    FILE_ROLE_SUPPLEMENT,
    FILE_STATUS_FAILED,
    FILE_STATUS_PENDING,
    FILE_STATUS_READY,
    MissingMainFileError,
    PDFDocument,
    PDFDocumentFile,
)

logger = logging.getLogger(__name__)


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


def mark_files_ready(
    text_bytes_by_file: Mapping[UUID, int],
    *,
    on_ready_in_session: Callable[[Session], None] | None = None,
) -> None:
    """Record several stored files as readable together, in one transaction (an add's files).

    When ``on_ready_in_session`` is given, it runs inside the same transaction before
    locking the file rows (matching ``pdf_jobs.service``'s job-before-files lock order)
    so dependent rows such as the owning job commit or roll back atomically with the files.
    """

    from src.models.sql.database import SessionLocal

    if not text_bytes_by_file:
        raise DocumentFileError("No files to mark ready")
    ordered_ids = sorted(text_bytes_by_file, key=str)
    for file_id in ordered_ids:
        text_bytes = text_bytes_by_file[file_id]
        if type(text_bytes) is not int or text_bytes < 0:
            raise DocumentFileError("A ready file needs a measured text size")
    with SessionLocal() as session, session.begin():
        if on_ready_in_session is not None:
            on_ready_in_session(session)
        for file_id in ordered_ids:
            text_bytes = text_bytes_by_file[file_id]
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


class StoredFilesNotRemoved(RuntimeError):
    """Some stored files could not be deleted; every other path was still tried."""


def remove_stored_files(
    paths: Iterable[str],
    *,
    owner_subject: str,
    storage_root: Path | None = None,
) -> None:
    """Delete stored files, one file at a time; the one helper for removals, adds and paper deletes.

    Every path (relative to the storage root) must have its folder inside the owner's
    storage folder (``storage_paths.owner_storage_entry``): if any does not, nothing is deleted
    and ``StoredPathOutsideOwnerStorage`` is raised. Each path is then unlinked on its
    own: a missing one is fine, a directory is never removed (it fails like any other
    path), and one failing path does not stop the others. ``StoredFilesNotRemoved``
    reports the failures after every path was tried, so a retry has the same work left.
    """

    from src.config import get_pdf_storage_path
    from src.lib.document_files.storage_paths import owner_storage_entry

    root = Path(get_pdf_storage_path()) if storage_root is None else storage_root
    # The folder is resolved and checked; the name is not followed, so a link is deleted
    # as a link and its target is never touched.
    targets = [owner_storage_entry(root / relative, root, owner_subject) for relative in dict.fromkeys(paths)]
    failures: list[str] = []
    for target in targets:
        try:
            if target.is_dir() and not target.is_symlink():
                raise IsADirectoryError("a stored path names a directory")
            target.unlink(missing_ok=True)
        except OSError as exc:
            failures.append(type(exc).__name__)
    if failures:
        raise StoredFilesNotRemoved(f"{len(failures)} of {len(targets)} stored files not removed ({', '.join(failures)})")


def paper_cleanup_snapshot(document: PDFDocument) -> dict[str, Any]:
    """Snapshot every stored artifact path of a paper before deleting its rows."""
    return {
        "id": document.id,
        "files": [
            {
                "role": file_row.role,
                "display_mode": file_row.display_mode,
                "file_path": file_row.file_path,
                "pdfx_json_path": file_row.pdfx_json_path,
                "processed_json_path": file_row.processed_json_path,
                "source_markdown_path": file_row.source_markdown_path,
            }
            for file_row in document.files
        ],
    }


def removed_stored_paths(file_values: Mapping[str, Any]) -> tuple[str, ...]:
    """The stored paths a paper delete removes for one file row."""
    keys = ("pdfx_json_path", "processed_json_path", "source_markdown_path") + (
        () if file_values["display_mode"] == DISPLAY_MODE_TEXT else ("file_path",)
    )
    return tuple(file_values[key] for key in keys if file_values[key])


def require_removable_paper_paths(
    cleanup_values: Mapping[str, Any],
    base_storage: Path,
    user_sub: str,
) -> None:
    """Raise ``StoredPathOutsideOwnerStorage`` if owner storage or any stored path escapes storage or names a directory."""
    from src.lib.document_files.storage_paths import (
        StoredPathOutsideOwnerStorage,
        owner_storage_entry,
        owner_storage_path,
    )

    owner_storage_path(base_storage / user_sub, base_storage, user_sub)
    for file_values in cleanup_values["files"]:
        for relative in removed_stored_paths(file_values):
            path = owner_storage_entry(base_storage / relative, base_storage, user_sub)
            if path.is_dir() and not path.is_symlink():
                raise StoredPathOutsideOwnerStorage("A stored path names a folder, not a file")


def remove_paper_stored_files(
    cleanup_values: Mapping[str, Any],
    *,
    base_storage: Path,
    user_sub: str,
    document_id: str,
) -> None:
    """Validate and delete all stored files belonging to one paper without ignoring unlink errors."""
    from src.lib.document_files.storage_paths import owner_storage_entry, owner_storage_path

    require_removable_paper_paths(cleanup_values, base_storage, user_sub)
    user_storage_root = owner_storage_path(base_storage / user_sub, base_storage, user_sub)
    paper_dir = user_storage_root / document_id
    single_files: list[str] = []

    for file_values in cleanup_values["files"]:
        if (
            file_values["role"] != FILE_ROLE_SUPPLEMENT
            and file_values["display_mode"] != DISPLAY_MODE_TEXT
        ):
            relative_path = file_values["file_path"]
            if relative_path:
                file_path = owner_storage_entry(base_storage / relative_path, base_storage, user_sub)
                owns_folder = (
                    file_path.relative_to(user_storage_root).parts[:1] == (document_id,)
                    and len(file_path.relative_to(user_storage_root).parts) >= 2
                    and not paper_dir.is_symlink()
                    and paper_dir.resolve() == paper_dir
                )
                if owns_folder and paper_dir.is_dir():
                    shutil.rmtree(paper_dir)
                elif file_path.is_symlink() or file_path.exists():
                    file_path.unlink()
            single_files.extend(
                path for path in removed_stored_paths(file_values)
                if path != file_values["file_path"]
            )
        else:
            single_files.extend(removed_stored_paths(file_values))

    remove_stored_files(single_files, owner_subject=user_sub, storage_root=base_storage)
    if not paper_dir.is_symlink() and paper_dir.resolve() == paper_dir:
        for folder in (paper_dir / "supplement_pdf", paper_dir):
            if folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
                folder.rmdir()


def remove_committed_paper_stored_files(
    session: Session,
    *,
    document_id: str,
    user_sub: str,
    base_storage: Path,
    fallback_cleanup: Callable[[], bool],
) -> bool:
    """Lock the committed paper row, snapshot its live PDFDocumentFile rows, and remove them.

    Falls back to ``fallback_cleanup`` for any remaining staged artifact (or when a unit test
    stub has no ``files`` attribute). Returns False without raising on path/unlink failures.
    """
    from src.lib.document_files.storage_paths import StoredPathOutsideOwnerStorage

    persisted = (
        session.execute(
            select(PDFDocument)
            .where(PDFDocument.id == UUID(document_id))
            .with_for_update()
        )
        .scalars()
        .first()
    )
    files = getattr(persisted, "files", None) if persisted is not None else None
    if persisted is not None and files:
        try:
            cleanup_values = paper_cleanup_snapshot(persisted)
            remove_paper_stored_files(
                cleanup_values,
                base_storage=base_storage,
                user_sub=user_sub,
                document_id=document_id,
            )
        except (StoredPathOutsideOwnerStorage, StoredFilesNotRemoved, OSError):
            return False
    return fallback_cleanup()


def remove_staged_paper_artifacts(
    saved_path: Path,
    *,
    base_storage: Path,
    user_sub: str,
    document_id: str,
) -> bool:
    """Remove staged PDF artifacts strictly inside ``<base_storage>/<user_sub>/<document_id>``.

    Derives the only removable directory from trusted ``(base_storage, user_sub, document_id)``
    so a filename containing path separators or traversal segments can never cause
    ``shutil.rmtree`` to touch an unrelated directory. Wraps all stat/exists/rmtree checks
    in ``OSError`` handling so permission errors return False cleanly.
    """
    from src.lib.document_files.storage_paths import (
        StoredPathOutsideOwnerStorage,
        owner_storage_entry,
        owner_storage_path,
    )

    try:
        if (
            not document_id
            or "/" in document_id
            or "\\" in document_id
            or "\x00" in document_id
            or document_id in {".", ".."}
            or Path(document_id).name != document_id
        ):
            return False
        resolved_base = base_storage.resolve()
        owner_entry = resolved_base / user_sub
        if owner_entry.is_symlink():
            logger.warning(
                "Refusing to remove staged PDF artifacts through symlinked owner folder",
                extra={"sentry_skip_event": True},
            )
            return False
        owner_root = owner_storage_path(owner_entry, resolved_base, user_sub)
        owner_root.relative_to(resolved_base)
        if owner_root != owner_entry:
            return False
        expected_dir = owner_root / document_id
        if expected_dir.is_symlink():
            return False
        resolved_expected = expected_dir.resolve()
        resolved_expected.relative_to(owner_root)
        if resolved_expected != expected_dir:
            return False

        entry = owner_storage_entry(saved_path, base_storage, user_sub)
        if entry.parent.resolve() != expected_dir:
            logger.warning(
                "Refusing to remove staged PDF artifacts outside expected paper folder",
                extra={"sentry_skip_event": True},
            )
            return False

        if not entry.exists() and not entry.is_symlink() and not expected_dir.exists():
            return True
        try:
            shutil.rmtree(expected_dir)
        except FileNotFoundError:
            pass
        return not entry.exists() and not entry.is_symlink() and not expected_dir.exists()
    except (StoredPathOutsideOwnerStorage, ValueError, OSError) as exc:
        logger.warning(
            "Failed to remove staged PDF artifacts (%s)",
            type(exc).__name__,
            extra={"sentry_skip_event": True},
        )
        return False


def ready_file_count(paper: PDFDocument) -> int:
    """How many of the paper's files are ready: the files a curator can open and the AI reads.

    Main file included. An add's files count once they become ready together; failed
    leftovers and removed files do not count. Reads the paper's already loaded files
    (``PDFDocument.files`` is loaded with the paper), so a list adds no query per row.
    """

    return sum(1 for file_row in paper.files if file_row.status == FILE_STATUS_READY)
