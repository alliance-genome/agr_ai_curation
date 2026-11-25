"""Remove one supplement from a paper (KANBAN-1976 / ALL-1460, P10b-api).

A curator may remove a supplementary file from one of their finished papers,
never the main file and never the paper's last ready file. The caller holds the
paper's operation lock (``paper_lock.py``), so no add, paper delete or other
removal runs at the same time.

Nothing that names the file elsewhere is cascaded away or rewritten
(forward-only):

- **Review sessions** that quote the file refuse the removal. A session quotes it
  when one of its evidence anchors names it (``evidence_anchors.source_file_id``,
  whose foreign key is ``RESTRICT``), or when an envelope attached to the session
  names its id in its stored evidence (an id equality inside the stored JSON;
  structural, never meaning).
- Chat evidence and files-read records keep the file's id and name. The viewer and
  the model's listings show such evidence as not openable.

Two phases (decided 23 Nov 2026, option A):

1. One transaction under the paper and file row locks makes every check and sets the
   file's status to ``removed``. Every reader keeps to ``ready`` rows, so the file is
   gone for them from that commit, and no evidence can name it any more.
2. Its piece range, its stored files and the paper's piece counts are cleared, then
   the row is deleted last. A failed step is reported and leaves the row ``removed``
   with its range and paths, so nothing is left that the system cannot find: removing
   the file again finishes the job, and so does deleting the paper (which clears every
   row's files and every piece of the paper). Piece numbers are never reused, so a
   leftover range never meets a later add's range.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, select, text

from src.models.sql.pdf_document import (
    FILE_ROLE_SUPPLEMENT,
    FILE_STATUS_FAILED,
    FILE_STATUS_READY,
    FILE_STATUS_REMOVED,
    PDFDocument,
    PDFDocumentFile,
)
from src.models.sql.pdf_processing_job import PdfJobStatus, PdfProcessingJob

logger = logging.getLogger(__name__)

PAPER_STATUS_COMPLETED = "completed"
_ACTIVE_JOB_STATUSES = (
    PdfJobStatus.PENDING.value,
    PdfJobStatus.RUNNING.value,
    PdfJobStatus.CANCEL_REQUESTED.value,
)


class PaperRowsBusy(RuntimeError):
    """The paper's or the file's row is locked by another transaction; the removal does not wait."""


class FileRemovalCleanupFailed(RuntimeError):
    """Something a removed file left behind could not be cleared; the file itself is removed."""


class FileRemovalRefused(Exception):
    """The file is not removed; ``code`` says why, ``status_code`` is the HTTP status, ``details`` extra codes."""

    def __init__(self, status_code: int, code: str, **details: Any):
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.details = details


@dataclass(frozen=True, slots=True)
class FileRemoved:
    document_id: UUID
    file_id: UUID
    owner_subject: str
    chunk_range: tuple[int, int] | None
    stored_paths: tuple[str, ...]
    ready_file_count: int
    ready_piece_total: int
    cleanup_failures: list[str] = field(default_factory=list)

    @property
    def cleanup_finished(self) -> bool:
        return not self.cleanup_failures


def quoting_session_ids(session: Any, *, document_id: UUID, file_id: UUID) -> list[str]:
    """The review sessions that quote the file, by an evidence anchor or by session envelope evidence."""

    from src.lib.curation_workspace.models import (
        CurationCandidate,
        CurationEvidenceRecord,
        DomainEnvelopeModel,
    )

    by_anchor = session.scalars(
        select(CurationCandidate.session_id)
        .join(CurationEvidenceRecord, CurationEvidenceRecord.candidate_id == CurationCandidate.id)
        .where(CurationEvidenceRecord.source_file_id == file_id)
        .distinct()
    ).all()
    by_envelope = session.scalars(
        select(DomainEnvelopeModel.session_id)
        .where(
            DomainEnvelopeModel.document_id == document_id,
            DomainEnvelopeModel.session_id.is_not(None),
            func.jsonb_path_exists(
                DomainEnvelopeModel.envelope_json,
                text("'$.** ? (@.file_id == $file_id)'::jsonpath"),
                func.jsonb_build_object("file_id", str(file_id)),
            ),
        )
        .distinct()
    ).all()
    return sorted({str(session_id) for session_id in (*by_anchor, *by_envelope)})


def _refuse_unless_removable(
    session: Any, *, document_id: UUID, paper: PDFDocument, file_row: PDFDocumentFile | None,
) -> list[PDFDocumentFile]:
    """Every removal check, shared by the removal and its read-only pre-check.

    Raises ``FileRemovalRefused``; returns the paper's other ready files. A file already
    ``removed`` passes (its clean-up is unfinished; it was checked when it was marked).
    """

    if session.scalar(select(exists().where(
        PdfProcessingJob.document_id == document_id,
        PdfProcessingJob.status.in_(_ACTIVE_JOB_STATUSES),
    ))):
        raise FileRemovalRefused(409, "import_in_progress")
    if paper.status != PAPER_STATUS_COMPLETED:
        raise FileRemovalRefused(409, "paper_not_completed")
    if file_row is None or file_row.document_id != document_id:
        raise FileRemovalRefused(404, "file_not_in_paper")
    if file_row.role != FILE_ROLE_SUPPLEMENT:
        raise FileRemovalRefused(409, "main_file_not_removable")
    if file_row.status not in (FILE_STATUS_READY, FILE_STATUS_FAILED, FILE_STATUS_REMOVED):
        raise RuntimeError(f"File {file_row.id} is {file_row.status} with no active job on its paper")
    other_ready = [row for row in paper.files if row.id != file_row.id and row.status == FILE_STATUS_READY]
    if file_row.status != FILE_STATUS_REMOVED:
        if not other_ready:
            raise FileRemovalRefused(409, "last_file_not_removable")
        sessions = quoting_session_ids(session, document_id=document_id, file_id=file_row.id)
        if sessions:
            raise FileRemovalRefused(409, "file_quoted_in_review", session_ids=sessions)
    return other_ready


def newest_result_quotes_file(session: Any, *, document_id: UUID, owner_subject: str, file_id: UUID) -> bool:
    """Whether the result the Library's Review & Curate would replay quotes the file.

    The bootstrap's own selection (the owner's newest prepared result for the paper, no
    adapter or flow filter, as the Library sends it), then a file id comparison over its
    envelopes' stored evidence. Structural only.
    """

    from fastapi import HTTPException

    from src.lib.curation_workspace.bootstrap_service import (
        _replayable_prep_output,
        _select_bootstrap_extraction_result,
    )
    from src.lib.curation_workspace.models import DomainEnvelopeModel
    from src.lib.curation_workspace.removed_file_gate import quoted_file_ids
    from src.schemas.curation_workspace import CurationDocumentBootstrapRequest

    try:
        newest = _select_bootstrap_extraction_result(
            session, document_id=str(document_id), request=CurationDocumentBootstrapRequest(),
            current_user_id=owner_subject,
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            return False  # no prepared result: Review & Curate has nothing to replay
        raise
    for envelope_ref in _replayable_prep_output(newest).envelope_refs:
        envelope = session.get(DomainEnvelopeModel, envelope_ref.envelope_id)
        if envelope is not None and str(file_id) in quoted_file_ids(envelope.envelope_json):
            return True
    return False


def has_review_session(session: Any, *, document_id: UUID, owner_subject: str) -> bool:
    """Whether the Library's Review & Curate would open an existing session for the paper.

    The Library first lists the curator's sessions of the paper (newest prepared first)
    and opens the newest; only with none does it replay the newest result. The same list
    query, so the answer is what that button does.
    """

    from src.lib.curation_workspace.session_queries import list_sessions
    from src.schemas.curation_workspace import CurationSessionListRequest

    listed = list_sessions(
        session,
        CurationSessionListRequest.model_validate({"filters": {"document_id": str(document_id)}, "page_size": 1}),
        current_user_id=owner_subject,
    )
    return bool(listed.sessions)


def removal_check(document_id: UUID, owner_user_id: int, file_id: UUID) -> dict[str, Any]:
    """Read-only: what removing the file would do now, for the screen's confirmation (codes only)."""

    from src.lib.pdf_jobs import service as pdf_job_service
    from src.models.sql.database import SessionLocal
    from src.models.sql.user import User
    from src.services.document_access import require_owned_document

    pdf_job_service.get_latest_job_for_document(
        document_id=document_id, user_id=owner_user_id, reconcile_stale=True,
    )
    with SessionLocal() as session:
        paper = require_owned_document(session, document_id, owner_user_id)
        file_row = session.get(PDFDocumentFile, file_id)
        refusal: FileRemovalRefused | None = None
        try:
            _refuse_unless_removable(session, document_id=document_id, paper=paper, file_row=file_row)
        except FileRemovalRefused as refused:
            refusal = refused
        subject = session.scalar(select(User.auth_sub).where(User.id == owner_user_id))
        quotes = newest_result_quotes_file(session, document_id=document_id, owner_subject=subject, file_id=file_id)
        opens_session = has_review_session(session, document_id=document_id, owner_subject=subject)
    answer: dict[str, Any] = {
        "document_id": str(document_id),
        "file_id": str(file_id),
        "removable": refusal is None,
        "error": refusal.code if refusal else None,
        "newest_result_quotes_file": quotes,
        "has_review_session": opens_session,
        # After the removal, Review & Curate would answer result_quotes_removed_file: it
        # replays the newest result only when the paper has no session to open.
        "review_and_curate_would_refuse": quotes and not opens_session,
    }
    if refusal is not None:
        answer.update(refusal.details)
    return answer


def _mark_removed(
    document_id: UUID,
    owner_user_id: int,
    file_id: UUID,
    *,
    lock_connection: Any = None,
) -> FileRemoved:
    """Phase 1: every check, then status ``removed``, in one transaction (worker thread).

    A file already ``removed`` (an earlier removal whose clean-up failed) passes
    straight to the clean-up: it was checked when it was marked, and nothing can
    have come to quote it since (evidence names only ready files).
    """

    from src.lib.document_files.paper_lock import open_paper_lock_session
    from src.lib.pdf_jobs import service as pdf_job_service
    from src.models.sql.database import SessionLocal
    from src.models.sql.user import User
    from src.services.document_access import require_owned_document

    # A stale job is reconciled first, as the paper delete does, so a dead add does
    # not block a removal for ever.
    job_kwargs = {"connection": lock_connection} if lock_connection is not None else {}
    pdf_job_service.get_latest_job_for_document(
        document_id=document_id,
        user_id=owner_user_id,
        reconcile_stale=True,
        **job_kwargs,
    )
    bound_session = open_paper_lock_session(lock_connection)
    session = bound_session if bound_session is not None else SessionLocal()
    with session, session.begin():
        require_owned_document(session, document_id, owner_user_id)
        # NOWAIT: a removal never waits for a row lock. A preparation or an anchor write
        # holding the file row FOR SHARE, or anything holding the paper row, makes it
        # answer "another operation in progress" at once (``PaperRowsBusy``).
        paper: PDFDocument = session.execute(
            select(PDFDocument).where(PDFDocument.id == document_id)
            .with_for_update(nowait=True).execution_options(populate_existing=True)
        ).scalar_one()
        file_row = session.execute(
            select(PDFDocumentFile).where(PDFDocumentFile.id == file_id)
            .with_for_update(nowait=True).execution_options(populate_existing=True)
        ).scalar_one_or_none()
        other_ready = _refuse_unless_removable(session, document_id=document_id, paper=paper, file_row=file_row)
        if file_row.status != FILE_STATUS_REMOVED:
            file_row.status = FILE_STATUS_REMOVED

        subject = session.scalar(select(User.auth_sub).where(User.id == owner_user_id))
        if not subject:
            raise RuntimeError("The paper's owner has no subject")
        chunk_range = (
            (file_row.chunk_index_start, file_row.chunk_index_end)
            if file_row.chunk_index_start is not None and file_row.chunk_index_end is not None
            else None
        )
        stored_paths = tuple(dict.fromkeys(
            path for path in (
                file_row.source_markdown_path, file_row.processed_json_path, file_row.pdfx_json_path, file_row.file_path,
            ) if path
        ))
        return FileRemoved(
            document_id=document_id,
            file_id=file_id,
            owner_subject=subject,
            chunk_range=chunk_range,
            stored_paths=stored_paths,
            ready_file_count=len(other_ready),
            ready_piece_total=sum(row.chunk_count or 0 for row in other_ready),
        )


def _mark_removed_or_busy(
    document_id: UUID,
    owner_user_id: int,
    file_id: UUID,
    *,
    lock_connection: Any = None,
) -> FileRemoved:
    """``_mark_removed``; a row lock held elsewhere is ``PaperRowsBusy`` (NOWAIT)."""

    from sqlalchemy.exc import OperationalError

    try:
        return _mark_removed(
            document_id,
            owner_user_id,
            file_id,
            lock_connection=lock_connection,
        )
    except OperationalError as exc:
        if getattr(exc.orig, "pgcode", None) == "55P03":  # lock_not_available (NOWAIT)
            raise PaperRowsBusy(str(document_id)) from None
        raise


_REAL_MARK_REMOVED_OR_BUSY = _mark_removed_or_busy


def _delete_removed_row(
    document_id: UUID,
    file_id: UUID,
    *,
    lock_connection: Any = None,
) -> None:
    """Phase 2's last step: the ``removed`` row goes once everything it named is cleared."""

    from src.lib.document_files.paper_lock import open_paper_lock_session
    from src.models.sql.database import SessionLocal

    bound_session = open_paper_lock_session(lock_connection)
    session = bound_session if bound_session is not None else SessionLocal()
    with session, session.begin():
        file_row = session.execute(
            select(PDFDocumentFile).where(PDFDocumentFile.id == file_id)
            .with_for_update(nowait=True).execution_options(populate_existing=True)
        ).scalar_one()
        if file_row.document_id != document_id or file_row.status != FILE_STATUS_REMOVED:
            raise RuntimeError(f"File {file_id} is not a removed file of paper {document_id}")
        session.delete(file_row)


_REAL_DELETE_REMOVED_ROW = _delete_removed_row


async def remove_paper_file(
    document_id: UUID,
    owner_user_id: int,
    file_id: UUID,
    *,
    weaviate_client: Any,
    lock_connection: Any = None,
) -> FileRemoved:
    """Remove the file; the caller holds the paper's operation lock. Raises ``FileRemovalRefused``."""

    from src.lib.document_cache import invalidate_cache
    from src.lib.document_cleanup import run_thread_drained
    from src.lib.document_files.service import remove_stored_files
    from src.lib.pipeline.store import update_document_piece_count
    from src.lib.weaviate_client.chunks import delete_chunk_range

    if lock_connection is not None and _mark_removed_or_busy is _REAL_MARK_REMOVED_OR_BUSY:
        removed = await run_thread_drained(
            _mark_removed_or_busy,
            document_id,
            owner_user_id,
            file_id,
            lock_connection=lock_connection,
            cancellable_before_start=True,
        )
    else:
        removed = await run_thread_drained(
            _mark_removed_or_busy,
            document_id,
            owner_user_id,
            file_id,
            cancellable_before_start=True,
        )
    # From here a cancelled request stops the clean-up part way between phases (the row stays
    # ``removed`` with its range and paths, and a retry or the paper delete finishes it), while
    # any already-submitted mutation drains before ``paper_operation_lock`` releases ``lock_connection``.
    subject = removed.owner_subject
    if removed.chunk_range is not None:
        start, end = removed.chunk_range
        try:
            await run_thread_drained(
                delete_chunk_range,
                str(document_id),
                subject,
                start=start,
                end=end,
                caller_reports_failure=True,
                cancellable_before_start=True,
                on_cancelled_error=lambda exc: _report_cleanup_failure(exc, document_id, "pieces"),
            )
        except Exception as exc:
            removed.cleanup_failures.append("pieces")
            _report_cleanup_failure(exc, document_id, "pieces")
    try:
        await run_thread_drained(
            remove_stored_files,
            removed.stored_paths,
            owner_subject=subject,
            cancellable_before_start=True,
            on_cancelled_error=lambda exc: _report_cleanup_failure(exc, document_id, "stored_files"),
        )
    except Exception as exc:
        removed.cleanup_failures.append("stored_files")
        _report_cleanup_failure(exc, document_id, "stored_files")
    # Drain any in-flight Weaviate piece-count write before unwinding out of
    # ``paper_operation_lock`` so a cancelled removal cannot finish writing an
    # older count after a subsequent add publishes a newer one.
    count_task = asyncio.create_task(
        update_document_piece_count(
            str(document_id),
            removed.ready_piece_total,
            weaviate_client,
            subject,
            caller_reports_failure=True,
            on_cancelled_error=lambda exc: _report_cleanup_failure(exc, document_id, "piece_count"),
        )
    )
    try:
        await asyncio.shield(count_task)
    except asyncio.CancelledError:
        while not count_task.done():
            try:
                await asyncio.shield(count_task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not count_task.cancelled():
            count_exc = count_task.exception()
            if isinstance(count_exc, Exception):
                _report_cleanup_failure(count_exc, document_id, "piece_count")
        raise
    except Exception as exc:
        removed.cleanup_failures.append("piece_count")
        _report_cleanup_failure(exc, document_id, "piece_count")
    if removed.cleanup_finished:
        # Only now: until here the row is how a retry or the paper delete finds the leftovers.
        try:
            if lock_connection is not None and _delete_removed_row is _REAL_DELETE_REMOVED_ROW:
                await run_thread_drained(
                    _delete_removed_row,
                    document_id,
                    file_id,
                    lock_connection=lock_connection,
                    cancellable_before_start=True,
                    on_cancelled_error=lambda exc: _report_cleanup_failure(exc, document_id, "file_row"),
                )
            else:
                await run_thread_drained(
                    _delete_removed_row,
                    document_id,
                    file_id,
                    cancellable_before_start=True,
                    on_cancelled_error=lambda exc: _report_cleanup_failure(exc, document_id, "file_row"),
                )
        except Exception as exc:
            removed.cleanup_failures.append("file_row")
            _report_cleanup_failure(exc, document_id, "file_row")
    try:
        invalidate_cache(subject, str(document_id))
    except Exception as exc:
        # The outline cache is keyed to the readable files, so a stale entry never matches.
        _report_cleanup_failure(exc, document_id, "cache", unfinished=False)
    return removed


def _sanitized_cleanup_error(step: str, exc_type_name: str) -> FileRemovalCleanupFailed:
    """Build a traceback-bearing, chain-free cleanup error safe for logs and Sentry."""
    try:
        raise FileRemovalCleanupFailed(
            f"Clearing a removed file's {step} failed ({exc_type_name})"
        ) from None
    except FileRemovalCleanupFailed as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized


def _report_cleanup_failure(
    exc: Exception,
    document_id: UUID,
    step: str,
    *,
    unfinished: bool = True,
) -> None:
    """The file is removed and unreadable; something it left behind could not be cleared yet.

    The row stays ``removed`` with its range and paths. Operators find such rows with
    ``SELECT id, document_id FROM pdf_document_files WHERE status = 'removed'``.
    Unfinished cleanup steps (``cleanup_finished=False``) report at ``error`` level;
    outline-cache invalidation reports at ``warning`` level.
    """

    from src.lib.observability.runtime import report_runtime_exception

    exc_type_name = type(exc).__name__
    sanitized = _sanitized_cleanup_error(step, exc_type_name)
    log_fn = logger.error if unfinished else logger.warning
    log_fn(
        "Clearing a removed file's %s failed (%s)",
        step,
        exc_type_name,
        extra={"sentry_skip_event": True},
    )
    try:
        report_runtime_exception(
            sanitized,
            component="document_files",
            operation="file_removal_cleanup_failed",
            level="error" if unfinished else "warning",
            context={"document_id": str(document_id), "step": step},
            fingerprint=["file_removal_cleanup_failed", step],
            include_request=False,
        )
    except Exception:
        logger.warning(
            "Failed to report file removal cleanup failure (%s)",
            step,
            extra={"sentry_skip_event": True},
        )
