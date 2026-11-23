"""The run's outline says ABC has not finished converting supplements only while a living wait runs.

Real job rows and the real resolver (KANBAN-1976 / ALL-1460, P7b outline line).
The line keys on ``supplement_wait_active`` through ``ReadableFiles``, the same
way the approved "Files are still being added" line keys on pending file rows:
read for every run, never in a batch's main-file-only scope, and gone once the
wait ends by any route. All values are made up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, update

from src.lib.document_files.readable import ReadingScope, reading_scope, resolve_readable_files
from src.lib.pdf_jobs import service as job_service
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.pdf_processing_job import PdfProcessingJob
from tests.pdf_document_test_support import ensure_test_pdf_owner, new_paper

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREFIX = "abc_wait_outline_test_"


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.delenv("DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS", raising=False)
    monkeypatch.delenv("DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DOCUMENT_SOURCE_DEAD_WAIT_POLLS", raising=False)
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    auth_sub = f"abc-wait-owner-{uuid4()}"
    with SessionLocal.begin() as db:
        owner_id = ensure_test_pdf_owner(db, auth_sub=auth_sub)
    yield owner_id, auth_sub
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


def _waiting(owner_id: int):
    document_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(new_paper(
            id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf", status="completed",
            processing_completed_at=datetime.now(timezone.utc), file_path=f"owner/{document_id}/made-up.pdf",
            file_hash=document_id.hex * 2, file_size=100, page_count=2, next_chunk_index=3,
            chunk_index_start=0, chunk_index_end=2, chunk_count=3, text_bytes=300,
        ))
    job = job_service.create_job(document_id=document_id, user_id=owner_id, filename="made-up.pdf")
    job_service.update_progress(
        job_id=job.job_id, stage=job_service.WAITING_FOR_SUPPLEMENTS_STAGE, progress_percentage=60,
        message="Waiting for ABC", status="running",
    )
    return str(document_id), job


def _converting(document_id: str, auth_sub: str) -> bool:
    return resolve_readable_files(document_id, auth_sub).supplements_converting


def test_a_living_wait_is_told_to_the_run_but_never_to_a_batch(owner):
    owner_id, auth_sub = owner
    document_id, _job = _waiting(owner_id)

    assert _converting(document_id, auth_sub) is True
    with reading_scope(ReadingScope.MAIN_FILE_ONLY):
        assert _converting(document_id, auth_sub) is False


def test_the_line_goes_when_abc_has_converted_and_the_add_begins(owner):
    owner_id, auth_sub = owner
    document_id, job = _waiting(owner_id)
    job_service.update_progress(
        job_id=job.job_id, stage=job_service.ADDING_SUPPLEMENTS_STAGE, progress_percentage=80,
        message="Adding supplements", status="running",
    )
    assert _converting(document_id, auth_sub) is False
    job_service.mark_completed(job_id=job.job_id, message="Done")
    assert _converting(document_id, auth_sub) is False


def test_the_line_goes_when_the_wait_times_out(owner):
    owner_id, auth_sub = owner
    document_id, job = _waiting(owner_id)
    job_service.mark_failed(job_id=job.job_id, message="ABC did not convert the supplements in time",
                            stage=job_service.WAITING_FOR_SUPPLEMENTS_STAGE)
    assert _converting(document_id, auth_sub) is False


def test_the_line_goes_as_soon_as_a_cancel_is_requested(owner):
    owner_id, auth_sub = owner
    document_id, job = _waiting(owner_id)
    job_service.request_cancel(job_id=job.job_id, user_id=owner_id)
    assert _converting(document_id, auth_sub) is False


def test_the_line_goes_when_the_wait_died_and_when_the_sweep_ends_it(owner, monkeypatch):
    from src.lib.pdf_jobs.recovery import reconcile_stale_jobs_once

    owner_id, auth_sub = owner
    document_id, job = _waiting(owner_id)
    aged = datetime.now(timezone.utc) - timedelta(hours=3)
    with SessionLocal.begin() as db:
        db.execute(update(PdfProcessingJob).where(PdfProcessingJob.id == job.job_id).values(updated_at=aged))
    # A dead wait (no poll for longer than the dead-wait test allows) is not told, before any sweep.
    assert _converting(document_id, auth_sub) is False

    monkeypatch.setenv("PDF_JOB_STALE_TIMEOUT_SECONDS", "7200")
    cursor = reconcile_stale_jobs_once()
    while cursor is not None:
        cursor = reconcile_stale_jobs_once(after=cursor)
    with SessionLocal() as db:
        assert db.get(PdfProcessingJob, job.job_id).status == "failed"
    assert _converting(document_id, auth_sub) is False


def test_a_wait_queued_behind_the_cap_is_told_to_the_run_too(owner):
    """A stage job starts pending in the wait stage until its first heartbeat; while it is
    alive by the dead-wait test the run is told, and a batch still never is."""
    owner_id, auth_sub = owner
    document_id, job = _waiting(owner_id)
    with SessionLocal.begin() as db:
        db.execute(update(PdfProcessingJob).where(PdfProcessingJob.id == job.job_id).values(status="pending"))

    assert _converting(document_id, auth_sub) is True
    with reading_scope(ReadingScope.MAIN_FILE_ONLY):
        assert _converting(document_id, auth_sub) is False
