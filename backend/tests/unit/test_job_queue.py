"""Tests for Postgres-based embedding job queue with LISTEN/NOTIFY support."""

from __future__ import annotations

import os
import select
import uuid
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    Base,
    EmbeddingJob,
    JobStatus,
    JobType,
    PDFDocument,
    ExtractionMethod,
)
from lib.job_queue import JobQueue


@pytest.fixture(scope="module")
def test_database_url() -> str:
    """Return the test database URL, skipping if unavailable."""
    return os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://curation_user:curation_pass@postgres-test:5432/ai_curation_test",  # pragma: allowlist secret
    )


@pytest.fixture(scope="module")
def engine(test_database_url: str) -> Engine:
    """Provide a SQLAlchemy engine connected to the test database."""
    try:
        engine = create_engine(test_database_url)
        # Verify connection early so we can skip cleanly when DB is down
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:  # pragma: no cover - infrastructure issue
        pytest.skip(f"Test database not available: {exc}")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def session_factory(engine: Engine):
    """Create a session factory bound to the test engine."""
    return sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def clean_embedding_jobs(engine: Engine):
    """Ensure job-related tables start empty for every test."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE embedding_jobs RESTART IDENTITY CASCADE"))
        conn.execute(text("TRUNCATE TABLE pdf_documents RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def job_queue(engine: Engine) -> JobQueue:
    """Instantiate the job queue against the test engine."""
    return JobQueue(engine=engine, channel="embedding_queue")


@pytest.fixture
def listen_connection(test_database_url: str):
    """Open a dedicated connection that LISTENs on the embedding_queue channel."""
    conn = psycopg2.connect(test_database_url)
    conn.set_session(autocommit=True)
    cursor = conn.cursor()
    cursor.execute("LISTEN embedding_queue;")
    yield conn
    cursor.close()
    conn.close()


def _wait_for_notification(
    conn: psycopg2.extensions.connection, timeout: float = 2.0
) -> Optional[str]:
    """Wait for a single NOTIFY payload on the given connection."""
    if select.select([conn], [], [], timeout) == ([], [], []):
        return None

    conn.poll()
    if conn.notifies:
        notify = conn.notifies.pop(0)
        return notify.payload
    return None


def _fetch_job(session_factory, job_id):
    with session_factory() as session:
        return session.get(EmbeddingJob, job_id)


def _assert_timestamp_recent(ts: Optional[datetime]):
    assert ts is not None
    now = datetime.now(timezone.utc)
    target = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    assert (now - target).total_seconds() < 5


def _create_pdf(session_factory) -> UUID:
    with session_factory() as session:
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/tmp/test.pdf",
            file_hash=str(uuid.uuid4()).replace("-", "")[:32],
            content_hash_normalized=str(uuid.uuid4()).replace("-", "")[:32],
            file_size=2048,
            page_count=1,
            extraction_method=ExtractionMethod.UNSTRUCTURED_FAST,
        )
        session.add(pdf)
        session.commit()
        session.refresh(pdf)
        return pdf.id


def test_enqueue_job_creates_pending_record_and_notifies(
    job_queue: JobQueue, session_factory, listen_connection
):
    """enqueue_job should persist a pending job and emit a LISTEN/NOTIFY event."""
    pdf_id = _create_pdf(session_factory)

    payload = _wait_for_notification(listen_connection, timeout=0.1)
    # Drain any stray notifications before the enqueue call
    while payload:
        payload = _wait_for_notification(listen_connection, timeout=0.1)

    job = job_queue.enqueue_job(
        pdf_id=pdf_id,
        job_type=JobType.EMBED_PDF,
        priority=7,
        config={"model": "text-embedding-3-small"},
        total_items=42,
    )

    stored = _fetch_job(session_factory, job.id)
    assert stored is not None
    assert stored.status == JobStatus.PENDING
    assert stored.priority == 7
    assert stored.total_items == 42
    assert stored.retry_count == 0
    assert stored.progress == 0

    payload = _wait_for_notification(listen_connection, timeout=2.0)
    assert payload == str(job.id)


def test_dequeue_job_moves_pending_to_running(job_queue: JobQueue, session_factory):
    """dequeue_job should lock the highest priority pending job and mark it RUNNING."""
    pdf_low = _create_pdf(session_factory)
    pdf_high = _create_pdf(session_factory)

    job_queue.enqueue_job(pdf_id=pdf_low, job_type=JobType.EMBED_PDF, priority=1)
    job_high = job_queue.enqueue_job(
        pdf_id=pdf_high, job_type=JobType.EXTRACT_TABLES, priority=9
    )

    dequeued = job_queue.dequeue_job(worker_id="worker-1")

    assert dequeued is not None
    assert dequeued.id == job_high.id  # Highest priority should run first
    assert dequeued.status == JobStatus.RUNNING
    assert dequeued.worker_id == "worker-1"
    _assert_timestamp_recent(dequeued.started_at)

    stored = _fetch_job(session_factory, dequeued.id)
    assert stored.status == JobStatus.RUNNING
    assert stored.worker_id == "worker-1"


def test_update_progress_tracks_work(job_queue: JobQueue, session_factory):
    """update_progress should persist processed_items and compute percentage."""
    job = job_queue.enqueue_job(
        pdf_id=_create_pdf(session_factory),
        job_type=JobType.EMBED_PDF,
        priority=4,
        total_items=20,
    )
    job_queue.dequeue_job(worker_id="worker-2")

    updated = job_queue.update_progress(job.id, processed_items=5)
    assert updated.progress == 25
    assert updated.processed_items == 5

    stored = _fetch_job(session_factory, job.id)
    assert stored.progress == 25
    assert stored.processed_items == 5


def test_mark_job_done_sets_status_and_result(job_queue: JobQueue, session_factory):
    """mark_job_done should finalize the job with DONE status and 100% progress."""
    job = job_queue.enqueue_job(
        pdf_id=_create_pdf(session_factory),
        job_type=JobType.REEMBED_PDF,
        total_items=10,
    )
    job_queue.dequeue_job(worker_id="worker-3")

    result_payload = {"embedded_chunks": 10}
    completed = job_queue.mark_job_done(job.id, result=result_payload)

    assert completed.status == JobStatus.DONE
    assert completed.progress == 100
    assert completed.result == result_payload
    _assert_timestamp_recent(completed.completed_at)

    stored = _fetch_job(session_factory, job.id)
    assert stored.status == JobStatus.DONE
    assert stored.progress == 100
    assert stored.result == result_payload
    assert stored.worker_id is None


def test_mark_job_failed_requeues_when_retry_allowed(
    job_queue: JobQueue, session_factory
):
    """mark_job_failed should increment retry count and reset status for retryable jobs."""
    job = job_queue.enqueue_job(
        pdf_id=_create_pdf(session_factory),
        job_type=JobType.EXTRACT_TABLES,
        priority=6,
    )
    job_queue.dequeue_job(worker_id="worker-4")

    failed = job_queue.mark_job_failed(job.id, error_log="OCR timeout", retry=True)

    assert failed.status == JobStatus.PENDING
    assert failed.retry_count == 1
    assert failed.worker_id is None
    assert failed.error_log == "OCR timeout"

    stored = _fetch_job(session_factory, job.id)
    assert stored.status == JobStatus.PENDING
    assert stored.retry_count == 1
    assert stored.worker_id is None


def test_mark_job_failed_finalizes_when_no_retry(job_queue: JobQueue, session_factory):
    """Non-retryable failures should move job to FAILED state and clear worker info."""
    job = job_queue.enqueue_job(
        pdf_id=_create_pdf(session_factory),
        job_type=JobType.EXTRACT_TABLES,
    )
    job_queue.dequeue_job(worker_id="worker-5")

    failed = job_queue.mark_job_failed(
        job.id, error_log="Fatal schema error", retry=False
    )

    assert failed.status == JobStatus.FAILED
    assert failed.retry_count == 1
    assert failed.worker_id is None
    assert failed.completed_at is not None

    stored = _fetch_job(session_factory, job.id)
    assert stored.status == JobStatus.FAILED
    assert stored.retry_count == 1
    assert stored.worker_id is None
    assert stored.completed_at is not None
