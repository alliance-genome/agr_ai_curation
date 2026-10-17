"""Real PostgreSQL selection, query counts, savepoints, and writer exclusion."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from uuid import UUID, uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import pytest
from sqlalchemy import delete, event, select

from src.lib.pdf_jobs import recovery, service
from src.lib.pipeline.processing_receipt import PDF_PROCESSING_RECEIPT_KEY
from src.models.sql.database import SessionLocal, engine
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.pdf_processing_job import PdfProcessingJob
from tests.pdf_document_test_support import ensure_test_pdf_owner

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


@pytest.fixture
def dataset(monkeypatch):
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")
    monkeypatch.setenv("PDF_JOB_STALE_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("PDF_JOB_RECOVERY_BATCH_SIZE", "100")
    ids = []

    def add(status="running", *, recent=False, terminal_document=False, owner=None,
            created=None, job_id=None, document_id=None):
        with SessionLocal() as db:
            if owner is None:
                owner = ensure_test_pdf_owner(db, auth_sub=f"recovery-{uuid4()}")
            if document_id is None:
                document_id = uuid4()
                ids.append(document_id)
                db.add(PDFDocument(
                    id=document_id, user_id=owner, filename="synthetic.pdf",
                    file_path=f"synthetic/{document_id}", file_hash=document_id.hex * 2,
                    file_size=100, page_count=1,
                    status="completed" if terminal_document else "processing",
                ))
                db.flush()
            job = PdfProcessingJob(
                id=job_id or uuid4(), document_id=document_id, user_id=owner,
                status=status, filename="synthetic.pdf",
                created_at=created or NOW - timedelta(days=30),
                updated_at=NOW if recent else NOW - timedelta(hours=3),
                cancel_requested=status == "cancel_requested", progress_percentage=20,
            )
            db.add(job)
            db.commit()
            return job.id, document_id, owner

    yield add
    with SessionLocal() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.id.in_(ids)))
        db.commit()


def states(job_id, document_id):
    with SessionLocal() as db:
        job = db.get(PdfProcessingJob, job_id)
        doc = db.get(PDFDocument, document_id)
        return job.status, doc.status, job.metadata_json


def test_recovery_without_viewers_preserves_latest_terminal_and_recent(dataset):
    cases = [(dataset(status), "cancelled" if status == "cancel_requested" else "failed")
             for status in ("pending", "running", "cancel_requested")]
    terminal = dataset(terminal_document=True)
    recent = dataset(recent=True)
    old = dataset(created=NOW - timedelta(days=40))
    new = dataset(recent=True, owner=old[2], document_id=old[1], created=NOW)
    assert recovery.reconcile_stale_jobs_once(now=NOW) is None
    for (job_id, doc_id, _), expected in cases:
        status, document_status, metadata = states(job_id, doc_id)
        assert (status, document_status) == (expected, "failed")
        assert metadata[PDF_PROCESSING_RECEIPT_KEY]["outcome"] == expected
    assert states(*terminal[:2])[:2] == ("failed", "completed")
    assert states(*recent[:2])[:2] == ("running", "processing")
    assert states(*old[:2])[:2] == ("failed", "processing")
    assert states(*new[:2])[:2] == ("running", "processing")
    assert recovery.reconcile_stale_jobs_once(now=NOW) is None


def test_bounded_cursor_and_failed_row_rollback_do_not_starve_other_users(dataset, monkeypatch):
    first = dataset(job_id=UUID(int=1))
    second = dataset(job_id=UUID(int=2))
    monkeypatch.setenv("PDF_JOB_RECOVERY_BATCH_SIZE", "1")
    original = service._reconcile_stale_job
    reports = []
    def fail_one(session, job, **kwargs):
        result = original(session, job, **kwargs)
        if job.id == first[0]:
            raise RuntimeError("synthetic failure after document mutation")
        return result
    monkeypatch.setattr(service, "_reconcile_stale_job", fail_one)
    monkeypatch.setattr(recovery, "_report_failure", lambda: reports.append(True))
    cursor = recovery.reconcile_stale_jobs_once(now=NOW)
    assert cursor == first[0]
    assert states(*first[:2])[:2] == ("running", "processing")
    cursor = recovery.reconcile_stale_jobs_once(after=cursor, now=NOW)
    assert cursor == second[0]
    assert states(*second[:2])[:2] == ("failed", "failed")
    assert recovery.reconcile_stale_jobs_once(after=cursor, now=NOW) is None
    monkeypatch.setattr(service, "_reconcile_stale_job", original)
    recovery.reconcile_stale_jobs_once(now=NOW)
    assert states(*first[:2])[:2] == ("failed", "failed")
    assert len(reports) == 1


@pytest.mark.parametrize("winner", ["progress", "completed", "cancel"])
def test_locked_active_writer_is_not_overwritten_by_recovery(dataset, winner):
    job_id, doc_id, _ = dataset()
    with SessionLocal() as writer:
        job = writer.execute(select(PdfProcessingJob).where(
            PdfProcessingJob.id == job_id).with_for_update()).scalar_one()
        if winner == "progress":
            job.updated_at = NOW
        elif winner == "completed":
            job.status = "completed"
        else:
            job.status = "cancel_requested"
            job.cancel_requested = True
            job.updated_at = NOW
        # Another connection must skip the locked row, including uncommitted updates.
        with ThreadPoolExecutor() as pool:
            pool.submit(recovery.reconcile_stale_jobs_once, now=NOW).result(timeout=5)
        writer.commit()
    recovery.reconcile_stale_jobs_once(now=NOW)
    expected = {"progress": "running", "completed": "completed", "cancel": "cancel_requested"}[winner]
    assert states(job_id, doc_id)[:2] == (expected, "processing")


def test_sweep_owner_exclusion_and_recovery_winner_survives_late_progress(dataset, monkeypatch):
    job_id, doc_id, _ = dataset()
    entered, release = threading.Event(), threading.Event()
    original = service._reconcile_stale_job
    def pause(session, job, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(session, job, **kwargs)
    monkeypatch.setattr(service, "_reconcile_stale_job", pause)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(recovery.reconcile_stale_jobs_once, now=NOW)
        try:
            assert entered.wait(5)
            # Competing sweep cannot enter the reconciliation callback.
            assert pool.submit(recovery.reconcile_stale_jobs_once, now=NOW).result(timeout=5) is None
            progress = pool.submit(service.update_progress, job_id=job_id, message="late progress")
        finally:
            release.set()
        first.result(timeout=5)
        response = progress.result(timeout=5)
        assert response is not None and response.status == "failed"
    assert states(job_id, doc_id)[:2] == ("failed", "failed")


@pytest.mark.parametrize("viewers", [1, 3])
def test_snapshot_boundaries_rest_totals_and_synthetic_query_counts(dataset, monkeypatch, viewers):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(service, "datetime", Clock)
    tie = NOW - timedelta(days=1)
    a = dataset(recent=True, created=tie, job_id=UUID(int=10))
    b = dataset(recent=True, owner=a[2], created=tie, job_id=UUID(int=11))
    dataset(recent=True, owner=a[2], created=NOW - timedelta(days=8))
    dataset(recent=True, created=NOW)  # Other user's newer row must not leak.
    statements = []
    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for _ in range(viewers * 3):
            page = service.list_jobs(user_id=a[2], limit=1)
            assert page.total == 2
            assert [job.job_id for job in page.jobs] == [str(b[0])]
        before = len(statements)
        assert before == viewers * 3 * 3
        assert all(stmt.lstrip().upper().startswith("SELECT") for stmt in statements)
        statements.clear()
        for _ in range(viewers * 3):
            page = service.snapshot_jobs(user_id=a[2], limit=1)
            assert [job.job_id for job in page] == [str(b[0])]
        assert len(statements) == viewers * 3
        assert all(stmt.lstrip().upper().startswith("SELECT") for stmt in statements)
        assert all("count(" not in stmt.lower() and "FOR UPDATE" not in stmt for stmt in statements)
        statements.clear()
        recovery.reconcile_stale_jobs_once(now=NOW)
        # Recovery is a separate global pass, irrespective of viewer count.
        assert len(statements) == 3  # timeout setting, advisory lock, candidate page
        assert all(stmt.lstrip().upper().startswith("SELECT") for stmt in statements)
        assert service.list_jobs(user_id=a[2], statuses=["completed"]).total == 0
        assert [j.job_id for j in service.list_jobs(user_id=a[2], limit=1, offset=1).jobs] == [str(a[0])]
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_latest_job_insert_committing_while_document_lock_waits_is_preserved(dataset, monkeypatch):
    old_id, doc_id, owner = dataset()
    reached_document = threading.Event()
    def capture(_conn, _cursor, statement, *_args):
        if "FROM pdf_documents" in statement and "FOR UPDATE" in statement:
            reached_document.set()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with SessionLocal() as writer, ThreadPoolExecutor() as pool:
            # The insert is uncommitted when recovery starts. Its FK lock holds
            # recovery at the document read until the new latest job is visible.
            new_job = PdfProcessingJob(
                document_id=doc_id, user_id=owner, status="running",
                created_at=NOW, updated_at=NOW, progress_percentage=0,
            )
            writer.add(new_job)
            writer.flush()
            pending = pool.submit(recovery.reconcile_stale_jobs_once, now=NOW)
            try:
                assert reached_document.wait(5)
            finally:
                writer.commit()
            pending.result(timeout=5)
        assert states(old_id, doc_id)[:2] == ("failed", "processing")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
