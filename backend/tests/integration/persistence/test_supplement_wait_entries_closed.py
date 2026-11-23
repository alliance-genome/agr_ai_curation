"""A supplement wait ended from outside its stage leaves no file open (KANBAN-1976 / ALL-1460).

When the dead-wait cancel or the stale sweep finalizes a ``supplement_wait`` job
(its task is gone: a backend restart), every entry of ``metadata.supplements``
that had not ended is closed as ``left_out`` with a named reason, in the same
row-locked transaction: ``import_cancelled`` for a cancel, ``supplement_stage_failed``
for a sweep. Ended entries are kept. All values are made up.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete

from src.lib.pdf_jobs import service as job_service
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from tests.pdf_document_test_support import ensure_test_pdf_owner
from src.models.sql.pdf_processing_job import PdfProcessingJob
from tests.integration.persistence.test_supplement_wait_persistence import (
    BACKEND_ROOT,
    PREFIX,
    _age,
    _ready_paper,
    _waiting_stage,
)


@pytest.fixture
def owner():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    with SessionLocal.begin() as db:
        owner_id = ensure_test_pdf_owner(db, auth_sub=f"p7b-closed-owner-{uuid4()}")
    yield owner_id, None
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


def _entry(provider_file_id, state, reason=None):
    return {
        "provider_file_id": provider_file_id, "display_name": f"Made_up_S{provider_file_id}",
        "state": state, "reason_code": reason, "observed_at": None, "file_id": None,
        "measured_value": None, "limit_value": None,
    }


OPEN_AND_ENDED = [
    _entry("20", "added"),
    _entry("30", "waiting"),
    _entry("40", "queued"),
    _entry("50", "left_out", "provider_not_converted"),
]


def _stage_with_entries(owner_id):
    paper = _ready_paper(owner_id)
    stage = _waiting_stage(paper, owner_id)
    job_service.update_progress(job_id=stage.job_id, metadata={"supplements": OPEN_AND_ENDED})
    return stage


def _entries(job_id):
    with SessionLocal() as db:
        job = db.get(PdfProcessingJob, job_id)
        return job.status, {
            entry["provider_file_id"]: (entry["state"], entry["reason_code"])
            for entry in job.metadata_json["supplements"]
        }, [entry["observed_at"] for entry in job.metadata_json["supplements"]]


def _sweep(monkeypatch):
    from src.lib.pdf_jobs.recovery import reconcile_stale_jobs_once

    monkeypatch.setenv("PDF_JOB_STALE_TIMEOUT_SECONDS", "7200")
    monkeypatch.setenv("PDF_JOB_RECOVERY_BATCH_SIZE", "200")
    cursor = reconcile_stale_jobs_once()
    while cursor is not None:
        cursor = reconcile_stale_jobs_once(after=cursor)


def test_a_dead_wait_cancel_closes_every_open_entry_as_cancelled(owner, monkeypatch):
    monkeypatch.delenv("DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS", raising=False)
    owner_id, _ = owner
    stage = _stage_with_entries(owner_id)
    _age(stage.job_id, 600)  # no read for 10 minutes: the task is gone

    job_service.request_cancel(job_id=stage.job_id, user_id=owner_id)

    status, entries, observed = _entries(stage.job_id)
    assert status == "cancelled"
    assert entries == {
        "20": ("added", None),
        "30": ("left_out", "import_cancelled"),
        "40": ("left_out", "import_cancelled"),
        "50": ("left_out", "provider_not_converted"),
    }
    assert observed[0] is None and observed[3] is None  # ended entries are kept as they were
    assert observed[1] and observed[2]


def test_the_sweep_after_a_restart_closes_every_open_entry_as_stage_failed(owner, monkeypatch):
    owner_id, _ = owner
    stage = _stage_with_entries(owner_id)
    _age(stage.job_id, 3 * 3600)

    _sweep(monkeypatch)

    status, entries, _ = _entries(stage.job_id)
    assert status == "failed"
    assert entries == {
        "20": ("added", None),
        "30": ("left_out", "supplement_stage_failed"),
        "40": ("left_out", "supplement_stage_failed"),
        "50": ("left_out", "provider_not_converted"),
    }


def test_the_sweep_of_a_wait_asked_to_stop_closes_every_open_entry_as_cancelled(owner, monkeypatch):
    owner_id, _ = owner
    stage = _stage_with_entries(owner_id)
    with SessionLocal.begin() as db:
        job = db.get(PdfProcessingJob, stage.job_id)
        job.status = "cancel_requested"
        job.cancel_requested = True
    _age(stage.job_id, 3 * 3600)

    _sweep(monkeypatch)

    status, entries, _ = _entries(stage.job_id)
    assert status == "cancelled"
    assert {key: value for key, value in entries.items() if key in {"30", "40"}} == {
        "30": ("left_out", "import_cancelled"),
        "40": ("left_out", "import_cancelled"),
    }

