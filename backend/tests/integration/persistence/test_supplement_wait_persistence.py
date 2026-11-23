"""The supplement wait against the real database (KANBAN-1976 / ALL-1460, P7b).

A restart loses the in-process stage; the stale sweep must then end its job
cleanly, leaving the paper (completed before the wait) usable. And the outline
signal reads the real job row. All values are made up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, update

from src.lib.pdf_jobs import service as job_service
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.pdf_processing_job import PdfProcessingJob
from tests.pdf_document_test_support import ensure_test_pdf_owner, new_paper

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREFIX = "p7b_supplement_wait_test_"


@pytest.fixture
def owner_id():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    with SessionLocal.begin() as db:
        owner = ensure_test_pdf_owner(db, auth_sub=f"p7b-owner-{uuid4()}")
    yield owner
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


def _ready_paper(owner: int) -> PDFDocument:
    document_id = uuid4()
    paper = new_paper(
        id=document_id, user_id=owner, filename=f"{PREFIX}{document_id}.pdf", status="completed",
        file_path=f"owner/{document_id}/made-up.pdf", file_hash=document_id.hex * 2,
        file_size=100, page_count=2,
    )
    with SessionLocal.begin() as db:
        db.add(paper)
    return paper


def _waiting_job(paper: PDFDocument, owner: int):
    job = job_service.create_job(document_id=paper.id, user_id=owner, filename=paper.filename)
    job_service.update_progress(
        job_id=job.job_id, stage="waiting_for_supplements", progress_percentage=60,
        message="Waiting for ABC", status="running",
    )
    return job


def _age(job_id, seconds: int) -> None:
    aged = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    with SessionLocal.begin() as db:
        db.execute(update(PdfProcessingJob).where(PdfProcessingJob.id == job_id).values(updated_at=aged))


def test_after_a_restart_the_sweep_ends_the_wait_and_the_paper_stays_usable(owner_id, monkeypatch):
    from src.lib.pdf_jobs.recovery import reconcile_stale_jobs_once

    paper = _ready_paper(owner_id)
    job = _waiting_job(paper, owner_id)
    _age(job.job_id, 3 * 3600)  # the process died with its stage three hours ago
    monkeypatch.setenv("PDF_JOB_STALE_TIMEOUT_SECONDS", "7200")
    monkeypatch.setenv("PDF_JOB_RECOVERY_BATCH_SIZE", "200")

    cursor = reconcile_stale_jobs_once()
    while cursor is not None:
        cursor = reconcile_stale_jobs_once(after=cursor)

    with SessionLocal() as db:
        assert db.get(PdfProcessingJob, job.job_id).status == "failed"
        stored = db.get(PDFDocument, paper.id)
        assert stored.status == "completed"
        assert stored.main_file.status == "ready"
    # The one-active-job rule no longer holds the paper: files can be added by hand.
    job_service.create_job(document_id=paper.id, user_id=owner_id, filename=paper.filename)


def test_the_outline_signal_reads_a_living_wait_and_drops_a_dead_one(owner_id, monkeypatch):
    monkeypatch.delenv("DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DOCUMENT_SOURCE_DEAD_WAIT_POLLS", raising=False)
    paper = _ready_paper(owner_id)
    job = _waiting_job(paper, owner_id)

    with SessionLocal() as db:
        assert job_service.supplement_wait_active(db, paper.id) is True
    _age(job.job_id, 130)  # no poll for longer than 3 x (30 s + 10 s)
    with SessionLocal() as db:
        assert job_service.supplement_wait_active(db, paper.id) is False
    with SessionLocal() as db:
        assert job_service.supplement_wait_active(db, uuid4()) is False
