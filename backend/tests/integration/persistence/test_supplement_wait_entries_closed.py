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


def test_cancelling_an_accepted_import_while_queued_closes_every_queued_supplement(owner):
    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    queued = [_entry("30", "queued"), _entry("40", "queued")]
    import_job = job_service.create_job(
        document_id=paper.id,
        user_id=owner_id,
        filename=paper.filename,
        metadata={"supplements": queued},
    )

    job_service.mark_cancelled(job_id=import_job.job_id, reason="Cancelled before processing started")

    status, entries, observed = _entries(import_job.job_id)
    assert status == "cancelled"
    assert entries == {
        "30": ("left_out", "import_cancelled"),
        "40": ("left_out", "import_cancelled"),
    }
    assert all(observed)
    with SessionLocal() as db:
        saved = db.get(PdfProcessingJob, import_job.job_id).metadata_json["supplements"]
    assert [entry["display_name"] for entry in saved] == ["Made_up_S30", "Made_up_S40"]


def test_failing_an_accepted_import_before_supplement_stage_closes_queued_supplements(owner):
    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    import_job = job_service.create_job(
        document_id=paper.id,
        user_id=owner_id,
        filename=paper.filename,
        metadata={"supplements": [_entry("30", "queued")]},
    )

    job_service.mark_failed(job_id=import_job.job_id, message="Main import failed", stage="failed")

    status, entries, observed = _entries(import_job.job_id)
    assert status == "failed"
    assert entries == {"30": ("left_out", "supplement_stage_failed")}
    assert all(observed)


def test_handoff_transfers_queued_entries_atomically_and_cancel_while_queued_closes_them(owner):
    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    queued = [_entry("30", "queued"), _entry("40", "queued")]
    main_job = job_service.create_job(
        document_id=paper.id,
        user_id=owner_id,
        filename=paper.filename,
        metadata={"supplements": queued},
    )
    job_service.mark_completed(job_id=main_job.job_id, message="Processing completed")

    stage_job = job_service.start_supplement_wait_job(
        document_id=paper.id,
        user_id=owner_id,
        supplements=queued,
        source_job_id=main_job.job_id,
    )
    assert stage_job.filename == paper.filename

    with SessionLocal() as db:
        main_meta = db.get(PdfProcessingJob, main_job.job_id).metadata_json or {}
        stage_meta = db.get(PdfProcessingJob, stage_job.job_id).metadata_json or {}
    # Handed-off entries are removed from the completed main job (no duplicate display).
    assert "supplements" not in main_meta
    assert [(e["provider_file_id"], e["display_name"], e["state"]) for e in stage_meta["supplements"]] == [
        ("30", "Made_up_S30", "queued"),
        ("40", "Made_up_S40", "queued"),
    ]

    # Cancelling the queued stage before run_supplement_stage starts closes every queued entry.
    assert job_service.end_supplement_wait_for_delete(document_id=paper.id, user_id=owner_id) is True
    status, entries, observed = _entries(stage_job.job_id)
    assert status == "cancelled"
    assert entries == {
        "30": ("left_out", "import_cancelled"),
        "40": ("left_out", "import_cancelled"),
    }
    assert all(observed)


def test_atomic_complete_and_handoff_transfers_persisted_queued_entries(owner):
    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    queued = [_entry("30", "queued"), _entry("40", "queued")]
    main_job = job_service.create_job(
        document_id=paper.id,
        user_id=owner_id,
        filename=paper.filename,
        metadata={"supplements": queued},
    )

    completed_main, stage_job = job_service.complete_import_and_start_supplement_wait(
        job_id=main_job.job_id,
        document_id=paper.id,
        user_id=owner_id,
        message="Processing completed",
    )

    assert completed_main is not None and completed_main.status == "completed"
    assert stage_job is not None and stage_job.status == "pending"
    assert stage_job.filename == paper.filename
    with SessionLocal() as db:
        main_meta = db.get(PdfProcessingJob, main_job.job_id).metadata_json or {}
        stage_meta = db.get(PdfProcessingJob, stage_job.job_id).metadata_json or {}
    assert "supplements" not in main_meta
    assert [(e["provider_file_id"], e["display_name"], e["state"]) for e in stage_meta["supplements"]] == [
        ("30", "Made_up_S30", "queued"),
        ("40", "Made_up_S40", "queued"),
    ]


def test_atomic_complete_when_paper_busy_closes_entries_on_main_job_atomically(owner):
    from src.lib.document_files.paper_lock import try_lock_paper_for_add

    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    queued = [_entry("30", "queued"), _entry("40", "queued")]
    main_job = job_service.create_job(
        document_id=paper.id,
        user_id=owner_id,
        filename=paper.filename,
        metadata={"supplements": queued},
    )

    # Hold the paper's operation advisory lock from a separate connection to simulate a concurrent delete/add.
    with SessionLocal() as holder:
        assert try_lock_paper_for_add(holder, paper.id) is True
        completed_main, stage_job = job_service.complete_import_and_start_supplement_wait(
            job_id=main_job.job_id,
            document_id=paper.id,
            user_id=owner_id,
            message="Processing completed",
        )
        holder.rollback()

    assert completed_main is not None and completed_main.status == "completed"
    assert stage_job is None
    status, entries, observed = _entries(main_job.job_id)
    assert status == "completed"
    assert entries == {
        "30": ("left_out", "paper_busy_when_ready"),
        "40": ("left_out", "paper_busy_when_ready"),
    }
    assert all(observed)
    with SessionLocal() as db:
        saved = db.get(PdfProcessingJob, main_job.job_id).metadata_json["supplements"]
        all_jobs = db.query(PdfProcessingJob).filter(PdfProcessingJob.document_id == paper.id).all()
    assert [entry["display_name"] for entry in saved] == ["Made_up_S30", "Made_up_S40"]
    assert [j for j in all_jobs if (j.metadata_json or {}).get("job_kind") == "supplement_wait"] == []


@pytest.mark.parametrize("terminal_kind", ["cancelled", "failed"])
def test_late_worker_after_cancel_or_fail_never_reopens_or_erases_supplement_outcomes(owner, terminal_kind):
    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    queued = [_entry("30", "queued"), _entry("40", "queued")]
    main_job = job_service.create_job(
        document_id=paper.id,
        user_id=owner_id,
        filename=paper.filename,
        metadata={"supplements": queued},
    )
    if terminal_kind == "cancelled":
        job_service.mark_cancelled(job_id=main_job.job_id, reason="Cancelled by user")
        expected_reason = "import_cancelled"
    else:
        job_service.mark_failed(job_id=main_job.job_id, message="Main import failed", stage="failed")
        expected_reason = "supplement_stage_failed"

    # Late worker attempts atomic completion+handoff: terminal winner must be preserved untouched.
    returned_main, stage_job = job_service.complete_import_and_start_supplement_wait(
        job_id=main_job.job_id,
        document_id=paper.id,
        user_id=owner_id,
        message="Processing completed",
    )
    assert returned_main is not None and returned_main.status == terminal_kind
    assert stage_job is None

    # Direct start_supplement_wait_job with source_job_id must also refuse handoff without mutating outcomes.
    with pytest.raises(job_service.SupplementHandoffRefusedError):
        job_service.start_supplement_wait_job(
            document_id=paper.id,
            user_id=owner_id,
            supplements=queued,
            source_job_id=main_job.job_id,
        )

    status, entries, observed = _entries(main_job.job_id)
    assert status == terminal_kind
    assert entries == {
        "30": ("left_out", expected_reason),
        "40": ("left_out", expected_reason),
    }
    assert all(observed)
    with SessionLocal() as db:
        all_jobs = db.query(PdfProcessingJob).filter(PdfProcessingJob.document_id == paper.id).all()
    assert [j for j in all_jobs if (j.metadata_json or {}).get("job_kind") == "supplement_wait"] == []


def test_main_import_login_expired_closes_queued_supplements_with_login_expired(owner):
    from src.lib.document_sources.file_reasons import FileReason

    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    import_job = job_service.create_job(
        document_id=paper.id,
        user_id=owner_id,
        filename=paper.filename,
        metadata={"supplements": [_entry("30", "queued"), _entry("40", "queued")]},
    )

    job_service.mark_failed(
        job_id=import_job.job_id,
        message="Your login expired.",
        stage="failed",
        supplement_failure_reason=FileReason.LOGIN_EXPIRED.value,
    )

    status, entries, observed = _entries(import_job.job_id)
    assert status == "failed"
    assert entries == {
        "30": ("left_out", "login_expired"),
        "40": ("left_out", "login_expired"),
    }
    assert all(observed)


def test_atomic_complete_honors_cancel_requested_under_row_lock_without_starting_stage(owner):
    owner_id, _ = owner
    paper = _ready_paper(owner_id)
    queued = [_entry("30", "queued"), _entry("40", "queued")]
    main_job = job_service.create_job(
        document_id=paper.id,
        user_id=owner_id,
        filename=paper.filename,
        metadata={"supplements": queued},
    )
    # Simulate a live cancel arriving after the worker's last is_cancel_requested check
    # and before handoff acquires the row lock.
    with SessionLocal.begin() as db:
        row = db.get(PdfProcessingJob, main_job.job_id)
        row.status = "cancel_requested"
        row.cancel_requested = True

    completed_main, stage_job = job_service.complete_import_and_start_supplement_wait(
        job_id=main_job.job_id,
        document_id=paper.id,
        user_id=owner_id,
        message="Processing completed",
    )

    assert completed_main is not None
    assert completed_main.status == "completed"
    assert completed_main.cancel_requested is True
    assert stage_job is None
    status, entries, observed = _entries(main_job.job_id)
    assert status == "completed"
    assert entries == {
        "30": ("left_out", "import_cancelled"),
        "40": ("left_out", "import_cancelled"),
    }
    assert all(observed)
    with SessionLocal() as db:
        saved = db.get(PdfProcessingJob, main_job.job_id).metadata_json["supplements"]
        all_jobs = db.query(PdfProcessingJob).filter(PdfProcessingJob.document_id == paper.id).all()
    assert [entry["display_name"] for entry in saved] == ["Made_up_S30", "Made_up_S40"]
    assert [j for j in all_jobs if (j.metadata_json or {}).get("job_kind") == "supplement_wait"] == []

    # Direct start_supplement_wait_job must also refuse a completed job with cancel_requested=True.
    queued_completed = job_service.create_job(
        document_id=paper.id, user_id=owner_id, filename=paper.filename,
        metadata={"supplements": queued},
    )
    job_service.mark_completed(job_id=queued_completed.job_id, message="Processing completed")
    with SessionLocal.begin() as db:
        db.get(PdfProcessingJob, queued_completed.job_id).cancel_requested = True
    with pytest.raises(job_service.SupplementHandoffRefusedError):
        job_service.start_supplement_wait_job(
            document_id=paper.id, user_id=owner_id,
            source_job_id=queued_completed.job_id,
        )

