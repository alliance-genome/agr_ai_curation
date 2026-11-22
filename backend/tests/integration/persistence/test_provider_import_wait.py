"""A long provider wait stays alive, and a dead one can be cancelled at once.

KANBAN-1976 / ALL-1460, P7w. Against the real database, because whether an
unchanged poll records activity depends on the UPDATE the ORM emits. All values
are made up.
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
PREFIX = "p7w_provider_wait_test_"


@pytest.fixture
def owner_id():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    with SessionLocal.begin() as db:
        owner = ensure_test_pdf_owner(db, auth_sub=f"p7w-owner-{uuid4()}")
    yield owner
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


def _importing_paper(owner: int) -> PDFDocument:
    document_id = uuid4()
    paper = new_paper(
        id=document_id, user_id=owner, filename=f"{PREFIX}{document_id}.pdf", status="pending",
        file_path=f"owner/{document_id}/made-up.pdf", file_hash=document_id.hex * 2,
        file_size=100, page_count=1,
    )
    with SessionLocal.begin() as db:
        db.add(paper)
    return paper


def _waiting_job(paper: PDFDocument, owner: int, stage: str):
    job = job_service.create_job(document_id=paper.id, user_id=owner, filename=paper.filename)
    job_service.update_progress(
        job_id=job.job_id, stage=stage, progress_percentage=20,
        message="ABC Literature conversion running", status="running",
    )
    return job


def _age(job_id, seconds: int) -> datetime:
    aged = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    with SessionLocal.begin() as db:
        db.execute(update(PdfProcessingJob).where(PdfProcessingJob.id == job_id).values(updated_at=aged))
    return aged


def test_a_poll_with_an_unchanged_answer_still_records_activity(owner_id):
    paper = _importing_paper(owner_id)
    job = _waiting_job(paper, owner_id, "provider_conversion")
    aged = _age(job.job_id, 3600)

    # The same answer again: no column but the activity time changes.
    job_service.update_progress(
        job_id=job.job_id, stage="provider_conversion", progress_percentage=20,
        message="ABC Literature conversion running", status="running",
    )

    with SessionLocal() as db:
        stored = db.get(PdfProcessingJob, job.job_id)
        assert stored.updated_at > aged + timedelta(seconds=3000)
        assert stored.status == "running"


def test_cancel_of_a_main_import_whose_wait_died_frees_the_paper_at_once(owner_id):
    paper = _importing_paper(owner_id)
    job = _waiting_job(paper, owner_id, "provider_conversion")
    _age(job.job_id, 600)  # a backend restart ten minutes ago

    response = job_service.request_cancel(job_id=job.job_id, user_id=owner_id)

    assert response.status == "cancelled"
    with SessionLocal() as db:
        stored_paper = db.get(PDFDocument, paper.id)
        assert stored_paper.status == "failed"
        assert stored_paper.main_file.status == "failed"
    # The one-active-job rule no longer holds the paper: it can be imported
    # again (or deleted).
    job_service.create_job(document_id=paper.id, user_id=owner_id, filename=paper.filename)


def test_cancel_of_a_living_wait_only_asks_the_task_to_stop(owner_id):
    paper = _importing_paper(owner_id)
    job = _waiting_job(paper, owner_id, "provider_conversion")

    response = job_service.request_cancel(job_id=job.job_id, user_id=owner_id)

    assert response.status == "cancel_requested"
    with pytest.raises(job_service.ActiveJobExistsError):
        job_service.create_job(document_id=paper.id, user_id=owner_id, filename=paper.filename)


def test_touching_queued_jobs_refreshes_only_those_still_pending(owner_id):
    queued_paper = _importing_paper(owner_id)
    queued = job_service.create_job(
        document_id=queued_paper.id, user_id=owner_id, filename=queued_paper.filename
    )
    started_paper = _importing_paper(owner_id)
    started = _waiting_job(started_paper, owner_id, "provider_conversion")
    queued_aged = _age(queued.job_id, 5000)
    started_aged = _age(started.job_id, 5000)

    touched = job_service.touch_pending_jobs(job_ids=[queued.job_id, started.job_id])

    assert touched == 1
    with SessionLocal() as db:
        assert db.get(PdfProcessingJob, queued.job_id).updated_at > queued_aged + timedelta(seconds=4000)
        # A started job records its own activity; it is never touched for it.
        assert db.get(PdfProcessingJob, started.job_id).updated_at == started_aged
