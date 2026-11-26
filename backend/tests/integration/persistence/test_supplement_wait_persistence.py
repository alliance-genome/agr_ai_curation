"""The supplement wait against the real database (KANBAN-1976 / ALL-1460, P7b).

The stage runs under a job of its own kind, handed off when the main paper's
import job completes, so the paper reads completed and stays usable during the
wait and after every ending (done, failed, cancelled, a restart swept by the
stale sweep). Only adding files is refused while it waits; a delete ends a
waiting stage. Statuses are read through the real status endpoint. All values
are made up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

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
def owner():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    subject = f"p7b-owner-{uuid4()}"
    with SessionLocal.begin() as db:
        owner_id = ensure_test_pdf_owner(db, auth_sub=subject)
    yield owner_id, subject
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


def _ready_paper(owner_id: int) -> PDFDocument:
    """A paper whose import job has just completed (the moment of the hand-off)."""

    document_id = uuid4()
    paper = new_paper(
        id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf", status="completed",
        file_path=f"owner/{document_id}/made-up.pdf", file_hash=document_id.hex * 2,
        file_size=100, page_count=2,
    )
    with SessionLocal.begin() as db:
        db.add(paper)
    import_job = job_service.create_job(document_id=paper.id, user_id=owner_id, filename=paper.filename)
    job_service.mark_completed(job_id=import_job.job_id, message="Processing completed")
    return paper


def _waiting_stage(paper: PDFDocument, owner_id: int):
    job = job_service.start_supplement_wait_job(document_id=paper.id, user_id=owner_id)
    assert job.filename == paper.filename
    job_service.update_progress(
        job_id=job.job_id, stage="waiting_for_supplements", progress_percentage=60,
        message="Waiting for ABC", status="running",
    )
    return job


def _age(job_id, seconds: int) -> None:
    aged = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    with SessionLocal.begin() as db:
        db.execute(update(PdfProcessingJob).where(PdfProcessingJob.id == job_id).values(updated_at=aged))


async def _status(paper: PDFDocument, owner, monkeypatch) -> str:
    """What the real status endpoint answers for the paper."""

    from src.api import documents

    owner_id, subject = owner
    monkeypatch.setattr(documents, "provision_user_async", AsyncMock(return_value=SimpleNamespace(id=owner_id)))
    monkeypatch.setattr(
        documents, "get_document",
        AsyncMock(return_value={"document": {"processing_status": "completed"}}),
    )
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", AsyncMock(return_value=None))
    answer = await documents.get_document_processing_status(document_id=str(paper.id), user={"sub": subject})
    return answer["processing_status"]


def _main_file_status(paper: PDFDocument) -> str:
    with SessionLocal() as db:
        return db.get(PDFDocument, paper.id).main_file.status


@pytest.mark.asyncio
async def test_the_paper_reads_completed_while_its_supplements_are_awaited(owner, monkeypatch):
    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    _waiting_stage(paper, owner_id)

    assert await _status(paper, owner, monkeypatch) == "completed"
    # Only adding files is refused during the wait (one active job per paper).
    with pytest.raises(job_service.ActiveJobExistsError):
        job_service.create_job(document_id=paper.id, user_id=owner_id, filename=paper.filename)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["completed", "failed", "cancelled"])
async def test_the_paper_reads_completed_after_every_ending_of_its_stage(owner, monkeypatch, ending):
    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    stage = _waiting_stage(paper, owner_id)

    if ending == "completed":
        job_service.mark_completed(job_id=stage.job_id, message="Supplements finished")
    elif ending == "failed":
        job_service.mark_failed(
            job_id=stage.job_id, message="The main paper is ready, but its supplements could not be added.",
            stage="failed",
        )
    else:
        job_service.mark_cancelled(job_id=stage.job_id, reason="Processing cancelled by user request")

    assert await _status(paper, owner, monkeypatch) == "completed"
    # A failed or cancelled stage never marks the paper's main file failed (mutation M6).
    assert _main_file_status(paper) == "ready"


@pytest.mark.asyncio
async def test_after_a_restart_the_sweep_ends_the_wait_and_the_paper_stays_usable(owner, monkeypatch):
    from src.lib.pdf_jobs.recovery import reconcile_stale_jobs_once

    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    stage = _waiting_stage(paper, owner_id)
    _age(stage.job_id, 3 * 3600)  # the process died with its stage three hours ago
    monkeypatch.setenv("PDF_JOB_STALE_TIMEOUT_SECONDS", "7200")
    monkeypatch.setenv("PDF_JOB_RECOVERY_BATCH_SIZE", "200")

    cursor = reconcile_stale_jobs_once()
    while cursor is not None:
        cursor = reconcile_stale_jobs_once(after=cursor)

    with SessionLocal() as db:
        assert db.get(PdfProcessingJob, stage.job_id).status == "failed"
    assert await _status(paper, owner, monkeypatch) == "completed"
    assert _main_file_status(paper) == "ready"
    # The one-active-job rule no longer holds the paper.
    job_service.create_job(document_id=paper.id, user_id=owner_id, filename=paper.filename)


def test_a_delete_ends_a_stage_that_only_waits(owner):
    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    stage = _waiting_stage(paper, owner_id)

    assert job_service.end_supplement_wait_for_delete(document_id=paper.id, user_id=owner_id) is True

    latest = job_service.get_latest_job_for_document(document_id=paper.id, user_id=owner_id)
    assert (latest.job_id, latest.status) == (stage.job_id, "cancelled")


def test_a_delete_keeps_refusing_while_the_stage_writes_files(owner):
    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    stage = _waiting_stage(paper, owner_id)
    job_service.update_progress(job_id=stage.job_id, stage="adding_supplements", status="running")

    assert job_service.end_supplement_wait_for_delete(document_id=paper.id, user_id=owner_id) is False
    latest = job_service.get_latest_job_for_document(document_id=paper.id, user_id=owner_id)
    assert latest.status == "running"


def test_the_outline_signal_reads_a_living_wait_and_drops_a_dead_one(owner, monkeypatch):
    monkeypatch.delenv("DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DOCUMENT_SOURCE_DEAD_WAIT_POLLS", raising=False)
    monkeypatch.delenv("DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS", raising=False)
    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    stage = _waiting_stage(paper, owner_id)

    with SessionLocal() as db:
        assert job_service.supplement_wait_active(db, paper.id) is True
    _age(stage.job_id, 220)  # no listing read for longer than 3 x (60 s + 10 s)
    with SessionLocal() as db:
        assert job_service.supplement_wait_active(db, paper.id) is False
    with SessionLocal() as db:
        assert job_service.supplement_wait_active(db, uuid4()) is False


def test_a_delete_ends_a_stage_from_its_first_moment(owner):
    """Born in the waiting stage: no poll or heartbeat is needed before a delete can end it."""

    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    stage = job_service.start_supplement_wait_job(document_id=paper.id, user_id=owner_id)

    assert job_service.end_supplement_wait_for_delete(document_id=paper.id, user_id=owner_id) is True
    latest = job_service.get_latest_job_for_document(document_id=paper.id, user_id=owner_id)
    assert (latest.job_id, latest.status) == (stage.job_id, "cancelled")


@pytest.mark.asyncio
async def test_a_failing_stage_ends_its_own_job_and_never_touches_the_paper(owner, monkeypatch):
    """Through the real hand-off and the real failure path (the stage task's guard and
    its outcomes): the stage job fails by name, the paper and its main file stay as they
    were (mutation M6)."""

    import asyncio

    from src.lib.document_sources import supplement_wait
    from src.lib.document_sources.access import DocumentSourceRequestContext
    from src.lib.document_sources.models import ProviderBearerKind
    from src.lib.document_sources.supplement_plan import ChosenSupplement, SupplementPlan
    from src.lib.pdf_jobs import upload_execution_service as execution
    from src.lib.pdf_jobs.upload_execution_service import (
        ProviderMarkdownExecutionRequest,
        UploadExecutionService,
    )

    owner_id, subject = owner
    document_id = uuid4()
    paper = new_paper(
        id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf", status="completed",
        file_path=f"owner/{document_id}/made-up.pdf", file_hash=document_id.hex * 2,
        file_size=100, page_count=2,
    )
    with SessionLocal.begin() as db:
        db.add(paper)
    plan = SupplementPlan(
        chosen=(ChosenSupplement(provider_file_id="20", md5="md5-20", converted_md5="md5-21"),),
        request_context=DocumentSourceRequestContext(
            provider_groups=("made-up-staff",), authorized_group_ids=("made-up-group",),
            bearer_kind=ProviderBearerKind.CURATOR, curator_token="made-up-token",
        ),
        login_expires_at=None, owner_user_id=owner_id,
    )
    import_job = job_service.create_job(
        document_id=paper.id,
        user_id=owner_id,
        filename=paper.filename,
        metadata={"supplements": supplement_wait.queued_entries(plan)},
    )
    reported = []

    async def _failing_stage(**_kwargs):
        raise KeyError("made-up bug")

    class _Provider:
        async def aclose(self):
            return None

    monkeypatch.setattr(supplement_wait, "run_supplement_stage", _failing_stage)
    monkeypatch.setattr(supplement_wait, "report_runtime_exception", lambda exc, **kwargs: reported.append(kwargs))
    monkeypatch.setattr(execution, "get_connection", lambda: None)
    service = UploadExecutionService(
        pipeline_tracker=AsyncMock(), document_source_provider_factory=lambda: _Provider(),
    )
    request = ProviderMarkdownExecutionRequest(
        document_id=str(paper.id), job_id=str(import_job.job_id), user_id=subject,
        owner_user_id=owner_id, filename=paper.filename, converted_artifact_id="made-up-text",
        curator_token="made-up-token", bearer_kind=ProviderBearerKind.CURATOR,
        source_provenance={}, file_path=Path("/tmp/made-up.pdf"),
        supplements=plan,
    )

    await service._complete_main_paper(request, message="Processing completed")
    await asyncio.wait_for(asyncio.gather(*list(supplement_wait._stage_tasks)), timeout=30)

    with SessionLocal() as db:
        jobs = {
            job.id: job for job in db.query(PdfProcessingJob).filter(PdfProcessingJob.document_id == paper.id)
        }
    assert jobs[UUID(str(import_job.job_id))].status == "completed"
    (stage_job,) = [job for job in jobs.values() if (job.metadata_json or {}).get("job_kind") == "supplement_wait"]
    assert stage_job.status == "failed"
    assert stage_job.error_message == supplement_wait.SUPPLEMENT_STAGE_FAILED_MESSAGE
    assert reported and reported[0]["operation"] == "supplement_stage"
    assert _main_file_status(paper) == "ready"
    with SessionLocal() as db:
        assert db.get(PDFDocument, paper.id).status == "completed"


def test_the_outline_signal_reads_a_queued_stage_until_it_ends(owner, monkeypatch):
    """A stage just created, or queued behind the limit on simultaneous stages, has
    supplements on their way just as a polling one has; it stops counting when it ends."""

    monkeypatch.delenv("DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DOCUMENT_SOURCE_DEAD_WAIT_POLLS", raising=False)
    monkeypatch.delenv("DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS", raising=False)
    owner_id, _ = owner
    queued = _ready_paper(owner_id)
    stage = job_service.start_supplement_wait_job(document_id=queued.id, user_id=owner_id)
    dead = _ready_paper(owner_id)
    dead_stage = job_service.start_supplement_wait_job(document_id=dead.id, user_id=owner_id)
    _age(dead_stage.job_id, 220)

    with SessionLocal() as db:
        assert db.get(PdfProcessingJob, UUID(str(stage.job_id))).status == "pending"
        assert job_service.supplement_wait_active(db, queued.id) is True
        assert job_service.supplement_wait_active(db, dead.id) is False
    assert job_service.end_supplement_wait_for_delete(document_id=queued.id, user_id=owner_id) is True
    with SessionLocal() as db:
        assert job_service.supplement_wait_active(db, queued.id) is False
