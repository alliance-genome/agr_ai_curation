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

The file row goes first, in one transaction under the paper and file row locks.
Every reader takes readable piece ranges from ``ready`` rows, so the file's pieces
are unreadable from that commit. Its pieces, stored files and the paper's piece
counts follow. A failure there leaves only orphans nobody can read (piece numbers
are never reused); it is reported, and the removal stands.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, select, text

from src.models.sql.pdf_document import (
    FILE_ROLE_SUPPLEMENT,
    FILE_STATUS_FAILED,
    FILE_STATUS_READY,
    PDFDocument,
    PDFDocumentFile,
)
from src.models.sql.pdf_processing_job import PdfJobStatus, PdfProcessingJob

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


def _remove_row(document_id: UUID, owner_user_id: int, file_id: UUID) -> FileRemoved:
    """Every check and the row delete, in one transaction (worker thread)."""

    from src.lib.pdf_jobs import service as pdf_job_service
    from src.models.sql.database import SessionLocal
    from src.models.sql.user import User
    from src.services.document_access import require_owned_document

    # A stale job is reconciled first, as the paper delete does, so a dead add does
    # not block a removal for ever.
    pdf_job_service.get_latest_job_for_document(
        document_id=document_id, user_id=owner_user_id, reconcile_stale=True,
    )
    with SessionLocal() as session, session.begin():
        require_owned_document(session, document_id, owner_user_id)
        # NOWAIT: a removal never waits for a row lock. A preparation or an anchor write
        # holding the file row FOR SHARE, or anything holding the paper row, makes it
        # answer "another operation in progress" at once (``PaperRowsBusy``).
        paper: PDFDocument = session.execute(
            select(PDFDocument).where(PDFDocument.id == document_id)
            .with_for_update(nowait=True).execution_options(populate_existing=True)
        ).scalar_one()
        if session.scalar(select(exists().where(
            PdfProcessingJob.document_id == document_id,
            PdfProcessingJob.status.in_(_ACTIVE_JOB_STATUSES),
        ))):
            raise FileRemovalRefused(409, "import_in_progress")
        if paper.status != PAPER_STATUS_COMPLETED:
            raise FileRemovalRefused(409, "paper_not_completed")
        file_row = session.execute(
            select(PDFDocumentFile).where(PDFDocumentFile.id == file_id)
            .with_for_update(nowait=True).execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if file_row is None or file_row.document_id != document_id:
            raise FileRemovalRefused(404, "file_not_in_paper")
        if file_row.role != FILE_ROLE_SUPPLEMENT:
            raise FileRemovalRefused(409, "main_file_not_removable")
        if file_row.status not in (FILE_STATUS_READY, FILE_STATUS_FAILED):
            raise RuntimeError(f"File {file_id} is {file_row.status} with no active job on its paper")
        other_ready = [
            row for row in paper.files if row.id != file_row.id and row.status == FILE_STATUS_READY
        ]
        if not other_ready:
            raise FileRemovalRefused(409, "last_file_not_removable")
        sessions = quoting_session_ids(session, document_id=document_id, file_id=file_id)
        if sessions:
            raise FileRemovalRefused(409, "file_quoted_in_review", session_ids=sessions)

        subject = session.scalar(select(User.auth_sub).where(User.id == owner_user_id))
        if not subject:
            raise RuntimeError("The paper's owner has no subject")
        chunk_range = (
            (file_row.chunk_index_start, file_row.chunk_index_end)
            if file_row.chunk_index_start is not None and file_row.chunk_index_end is not None
            else None
        )
        stored_paths = tuple(dict.fromkeys(
            path for path in (file_row.source_markdown_path, file_row.processed_json_path, file_row.file_path) if path
        ))
        session.delete(file_row)
        return FileRemoved(
            document_id=document_id,
            file_id=file_id,
            owner_subject=subject,
            chunk_range=chunk_range,
            stored_paths=stored_paths,
            ready_file_count=len(other_ready),
            ready_piece_total=sum(row.chunk_count or 0 for row in other_ready),
        )


def _remove_row_or_refuse(document_id: UUID, owner_user_id: int, file_id: UUID) -> FileRemoved:
    """``_remove_row``; an anchor written at the same moment makes the commit fail on its foreign key."""

    from sqlalchemy.exc import IntegrityError, OperationalError

    from src.models.sql.database import SessionLocal

    try:
        return _remove_row(document_id, owner_user_id, file_id)
    except OperationalError as exc:
        if getattr(exc.orig, "pgcode", None) == "55P03":  # lock_not_available (NOWAIT)
            raise PaperRowsBusy(str(document_id)) from None
        raise
    except IntegrityError as exc:
        if "fk_evidence_anchors_source_file_id" not in str(exc.orig):
            raise
        with SessionLocal() as session:
            sessions = quoting_session_ids(session, document_id=document_id, file_id=file_id)
        raise FileRemovalRefused(409, "file_quoted_in_review", session_ids=sessions) from None


async def remove_paper_file(
    document_id: UUID, owner_user_id: int, file_id: UUID, *, weaviate_client: Any,
) -> FileRemoved:
    """Remove the file; the caller holds the paper's operation lock. Raises ``FileRemovalRefused``."""

    from src.lib.document_cache import invalidate_cache
    from src.lib.document_files.service import remove_stored_files
    from src.lib.pipeline.store import update_document_piece_count
    from src.lib.weaviate_client.chunks import delete_chunk_range

    removed = await asyncio.to_thread(_remove_row_or_refuse, document_id, owner_user_id, file_id)
    subject = removed.owner_subject
    if removed.chunk_range is not None:
        start, end = removed.chunk_range
        try:
            await asyncio.to_thread(delete_chunk_range, str(document_id), subject, start=start, end=end)
        except Exception as exc:
            removed.cleanup_failures.append("pieces")
            _report_cleanup_failure(exc, document_id, "pieces")
    try:
        await asyncio.to_thread(remove_stored_files, removed.stored_paths)
    except Exception as exc:
        removed.cleanup_failures.append("stored_files")
        _report_cleanup_failure(exc, document_id, "stored_files")
    try:
        await update_document_piece_count(str(document_id), removed.ready_piece_total, weaviate_client, subject)
    except Exception as exc:
        removed.cleanup_failures.append("piece_count")
        _report_cleanup_failure(exc, document_id, "piece_count")
    invalidate_cache(subject, str(document_id))
    return removed


def _report_cleanup_failure(exc: Exception, document_id: UUID, step: str) -> None:
    """The file is removed and unreadable; something it left behind could not be cleared."""

    from src.lib.observability.runtime import report_runtime_exception

    report_runtime_exception(
        FileRemovalCleanupFailed(f"Clearing a removed file's {step} failed ({type(exc).__name__})"),
        component="document_files",
        operation="file_removal_cleanup_failed",
        level="warning",
        context={"document_id": str(document_id), "step": step},
        fingerprint=["file_removal_cleanup_failed", step],
    )
