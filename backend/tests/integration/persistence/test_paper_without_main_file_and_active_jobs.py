"""Groundwork for importing supplements (KANBAN-1976 / ALL-1460, P7a).

A paper may hold supplements only, with no main file; every reader that shows
any paper handles it. A paper holds at most one active processing job. All
values are made up.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select, text

from src.lib.curation_workspace.execution_provenance import capture_source_document
from src.lib.pdf_jobs.service import ActiveJobExistsError, create_job
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.pdf_processing_job import PdfProcessingJob
from src.services.document_access import owned_documents_select
from tests.pdf_document_test_support import ensure_test_pdf_owner, new_paper, new_supplements_only_paper


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREFIX = "p7a_groundwork_test_"
PREVIOUS_HEAD = "c6d7e8f9a0b1"
REVISION = "d7e8f9a0b1c2"


def _config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


@pytest.fixture
def owner():
    command.upgrade(_config(), "head")
    subject = f"p7a-owner-{uuid4()}"
    with SessionLocal.begin() as db:
        owner_id = ensure_test_pdf_owner(db, auth_sub=subject)
    yield owner_id, subject
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


def _store(paper: PDFDocument) -> PDFDocument:
    with SessionLocal.begin() as db:
        db.add(paper)
    return paper


def _supplements_only(owner_id: int, **columns) -> PDFDocument:
    return _store(new_supplements_only_paper(
        id=uuid4(), user_id=owner_id, filename=f"{PREFIX}{uuid4()}", status="completed",
        upload_timestamp=datetime(2026, 11, 21, tzinfo=timezone.utc), **columns,
    ))


def _with_main(owner_id: int) -> PDFDocument:
    document_id = uuid4()
    return _store(new_paper(
        id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf", status="completed",
        file_path=f"owner/{document_id}/made-up.pdf", file_hash=document_id.hex * 2, file_size=100,
        page_count=2,
    ))


def test_a_stored_paper_without_a_main_file_loads_with_its_supplement(owner):
    owner_id, _ = owner
    stored = _supplements_only(owner_id)
    _with_main(owner_id)

    with SessionLocal() as db:
        papers = db.execute(owned_documents_select(owner_id)).scalars().all()
        by_id = {paper.id: paper for paper in papers}
        supplements_only = by_id[stored.id]

        assert len(papers) == 2
        assert supplements_only.main_file_or_none is None
        (supplement,) = supplements_only.files
        assert (supplement.role, supplement.display_mode, supplement.page_count) == (
            "supplement", "text", None,
        )
        assert all(paper.main_file_or_none is not None for paper in papers if paper.id != stored.id)


def test_execution_provenance_of_a_paper_without_a_main_file_names_no_converted_text(owner, monkeypatch):
    owner_id, subject = owner
    paper = _supplements_only(
        owner_id, source_provider="made_up_provider", source_provider_reference_curie="MADEUP:1",
    )

    with SessionLocal() as db:
        monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: nullcontext(db))
        source = capture_source_document(str(paper.id), subject)

    assert source is not None
    assert (source.provider, source.reference_curie) == ("made_up_provider", "MADEUP:1")
    assert (source.converted_artifact_id, source.converted_artifact_sha256) == (None, None)


def test_a_paper_holds_at_most_one_active_job(owner):
    owner_id, _ = owner
    paper = _with_main(owner_id)

    first = create_job(document_id=paper.id, user_id=owner_id, filename=paper.filename)
    with pytest.raises(ActiveJobExistsError):
        create_job(document_id=paper.id, user_id=owner_id, filename=paper.filename)

    with SessionLocal.begin() as db:
        job = db.get(PdfProcessingJob, first.job_id)
        job.status = "completed"
    # A finished job does not block the next one.
    create_job(document_id=paper.id, user_id=owner_id, filename=paper.filename)
    with SessionLocal() as db:
        statuses = db.scalars(
            select(PdfProcessingJob.status).where(PdfProcessingJob.document_id == paper.id)
        ).all()
    assert sorted(statuses) == ["completed", "pending"]


@pytest.mark.parametrize("second_status", ["pending", "running", "cancel_requested"])
def test_two_active_jobs_on_one_paper_stop_the_migration(historical_migration_database, second_status):
    command.upgrade(_config(), PREVIOUS_HEAD)
    with SessionLocal.begin() as db:
        owner_id = ensure_test_pdf_owner(db, auth_sub=f"p7a-migration-{uuid4()}")
        crowded = new_paper(
            id=uuid4(), user_id=owner_id, filename="crowded-paper.pdf", file_path="o/c.pdf",
            file_hash="c" * 64, file_size=10, page_count=1,
        )
        calm = new_paper(
            id=uuid4(), user_id=owner_id, filename="calm-paper.pdf", file_path="o/a.pdf",
            file_hash="a" * 64, file_size=10, page_count=1,
        )
        db.add_all([crowded, calm])
        db.flush()
        for document_id, status in (
            (crowded.id, "running"), (crowded.id, second_status), (calm.id, "pending"), (calm.id, "failed"),
        ):
            db.add(PdfProcessingJob(document_id=document_id, user_id=owner_id, status=status))
        crowded_id, calm_id = crowded.id, calm.id

    with pytest.raises(Exception) as raised:
        command.upgrade(_config(), REVISION)

    assert type(raised.value).__name__ == "ActivePdfJobsMigrationError"
    assert str(crowded_id) in str(raised.value)
    assert str(calm_id) not in str(raised.value)
    with SessionLocal() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_HEAD


def test_the_active_job_index_applies_and_its_downgrade_is_refused(historical_migration_database):
    command.upgrade(_config(), REVISION)
    with SessionLocal() as db:
        definition = db.scalar(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_pdf_processing_jobs_one_active_per_document'"
        ))
    assert "UNIQUE" in definition
    assert "pending" in definition and "running" in definition and "cancel_requested" in definition

    with pytest.raises(RuntimeError, match="forward-only"):
        command.downgrade(_config(), PREVIOUS_HEAD)
