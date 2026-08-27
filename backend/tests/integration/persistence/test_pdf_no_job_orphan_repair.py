"""PostgreSQL coverage for pending PDF documents with no processing job."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
from sqlalchemy import delete, select

from src.lib.pdf_jobs import service as pdf_job_service
from src.lib.pipeline.processing_receipt import PDF_PROCESSING_RECEIPT_KEY
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.pdf_processing_job import PdfJobStatus, PdfProcessingJob
from tests.pdf_document_test_support import ensure_test_pdf_owner


BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _document(*, document_id, owner_id: int, status: str, uploaded_at: datetime):
    return PDFDocument(
        id=document_id,
        filename=f"orphan-repair-{document_id}.pdf",
        file_path=f"orphan-repair/{document_id}.pdf",
        file_hash=document_id.hex * 2,
        file_size=1024,
        page_count=1,
        user_id=owner_id,
        status=status,
        upload_timestamp=uploaded_at,
    )


def test_repair_selects_only_aged_pending_no_job_documents_and_is_idempotent(
    monkeypatch,
):
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    old_orphan_id = uuid4()
    recent_intake_id = uuid4()
    stale_live_job_document_id = uuid4()
    terminal_document_id = uuid4()
    document_ids = (
        old_orphan_id,
        recent_intake_id,
        stale_live_job_document_id,
        terminal_document_id,
    )

    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_THRESHOLD_SECONDS", "86400")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_BATCH_SIZE", "10")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_REPAIR_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_REPAIR_RETRY_COUNT", "0")

    try:
        with SessionLocal() as setup_session:
            owner_id = ensure_test_pdf_owner(
                setup_session,
                auth_sub=f"pdf_no_job_orphan_repair_{uuid4()}",
            )
            setup_session.add_all(
                [
                    _document(
                        document_id=old_orphan_id,
                        owner_id=owner_id,
                        status="pending",
                        uploaded_at=now - timedelta(days=7),
                    ),
                    _document(
                        document_id=recent_intake_id,
                        owner_id=owner_id,
                        status="pending",
                        uploaded_at=now - timedelta(minutes=5),
                    ),
                    _document(
                        document_id=stale_live_job_document_id,
                        owner_id=owner_id,
                        status="pending",
                        uploaded_at=now - timedelta(days=7),
                    ),
                    _document(
                        document_id=terminal_document_id,
                        owner_id=owner_id,
                        status="completed",
                        uploaded_at=now - timedelta(days=7),
                    ),
                ]
            )
            setup_session.flush()
            setup_session.add(
                PdfProcessingJob(
                    document_id=stale_live_job_document_id,
                    user_id=owner_id,
                    filename="stale-live.pdf",
                    status=PdfJobStatus.RUNNING.value,
                    current_stage="parsing",
                    progress_percentage=20,
                    message="Still running",
                    created_at=now - timedelta(days=7),
                    started_at=now - timedelta(days=7),
                    updated_at=now - timedelta(days=6),
                )
            )
            setup_session.commit()

        dry_run = pdf_job_service.reconcile_pending_documents_without_jobs(
            apply=False,
            now=now,
        )
        assert dry_run.dry_run is True
        assert [record.document_id for record in dry_run.records] == [
            str(old_orphan_id)
        ]

        with SessionLocal() as inspection_session:
            assert inspection_session.get(PDFDocument, old_orphan_id).status == "pending"
            assert (
                inspection_session.scalar(
                    select(PdfProcessingJob).where(
                        PdfProcessingJob.document_id == old_orphan_id
                    )
                )
                is None
            )

        applied = pdf_job_service.reconcile_pending_documents_without_jobs(
            apply=True,
            now=now,
        )
        assert applied.dry_run is False
        assert applied.qualifying_count == 1
        assert applied.records[0].document_id == str(old_orphan_id)
        assert applied.records[0].status == "failed"

        with SessionLocal() as inspection_session:
            repaired_document = inspection_session.get(PDFDocument, old_orphan_id)
            repaired_job = inspection_session.scalar(
                select(PdfProcessingJob).where(
                    PdfProcessingJob.document_id == old_orphan_id
                )
            )
            assert repaired_document.status == "failed"
            assert repaired_document.error_message == (
                pdf_job_service.NO_JOB_ORPHAN_FAILURE_MESSAGE
            )
            assert repaired_job.status == PdfJobStatus.FAILED.value
            assert repaired_job.metadata_json[PDF_PROCESSING_RECEIPT_KEY][
                "outcome"
            ] == "failed"

            assert inspection_session.get(PDFDocument, recent_intake_id).status == (
                "pending"
            )
            live_job = inspection_session.scalar(
                select(PdfProcessingJob).where(
                    PdfProcessingJob.document_id == stale_live_job_document_id
                )
            )
            assert live_job.status == PdfJobStatus.RUNNING.value
            assert inspection_session.get(
                PDFDocument,
                stale_live_job_document_id,
            ).status == "pending"
            assert inspection_session.get(PDFDocument, terminal_document_id).status == (
                "completed"
            )

        repeated = pdf_job_service.reconcile_pending_documents_without_jobs(
            apply=True,
            now=now,
        )
        assert repeated.qualifying_count == 0
    finally:
        with SessionLocal() as cleanup_session:
            cleanup_session.execute(
                delete(PDFDocument).where(PDFDocument.id.in_(document_ids))
            )
            cleanup_session.commit()
