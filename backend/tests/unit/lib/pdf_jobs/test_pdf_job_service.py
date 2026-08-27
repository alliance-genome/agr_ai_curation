"""Unit tests for durable PDF job service helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from src.lib.pdf_jobs import service as service_module
from src.lib.pipeline.processing_receipt import PDF_PROCESSING_RECEIPT_KEY
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.pdf_processing_job import PdfJobStatus, PdfProcessingJob


class _ScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalar_one(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return self._row


class _FakeSession:
    def __init__(self, responses, *, commit_error: Exception | None = None):
        self.responses = list(responses) if isinstance(responses, list) else [responses]
        self.commit_error = commit_error
        self.commit_calls = 0
        self.refresh_calls = 0
        self.rollback_calls = 0
        self.closed = False
        self.added = []
        self.flush_calls = 0
        self.statements = []

    def execute(self, statement, *_args, **_kwargs):
        self.statements.append(statement)
        if not self.responses:
            raise AssertionError("unexpected query")
        response = self.responses.pop(0)
        if callable(response):
            response = response(self)
        if isinstance(response, Exception):
            raise response
        return _ScalarResult(response)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flush_calls += 1
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid4()

    def commit(self):
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    def refresh(self, row):
        self.refresh_calls += 1

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.closed = True


def _build_job(*, status: str) -> PdfProcessingJob:
    now = datetime.now(timezone.utc)
    return PdfProcessingJob(
        id=uuid4(),
        document_id=uuid4(),
        user_id=42,
        filename="paper.pdf",
        status=status,
        current_stage="parsing",
        progress_percentage=35,
        message="Still running",
        process_id=None,
        cancel_requested=status == PdfJobStatus.CANCEL_REQUESTED.value,
        error_message=None,
        metadata_json=None,
        created_at=now - timedelta(hours=5),
        started_at=now - timedelta(hours=4),
        updated_at=now - timedelta(hours=3),
        completed_at=None,
    )


def _build_document(job: PdfProcessingJob, *, status: str = "processing") -> PDFDocument:
    return PDFDocument(
        id=job.document_id,
        filename=job.filename or "paper.pdf",
        file_path=f"/tmp/{job.document_id}.pdf",
        file_hash=str(job.document_id).replace("-", ""),
        file_size=100,
        page_count=1,
        user_id=job.user_id,
        status=status,
        processing_started_at=None,
        processing_completed_at=None,
        error_message=None,
    )


def test_get_job_by_id_returns_none_when_missing(monkeypatch):
    session = _FakeSession(None)
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)

    assert service_module.get_job_by_id(job_id=uuid4()) is None
    assert session.commit_calls == 0
    assert session.refresh_calls == 0
    assert session.closed is True


@pytest.mark.parametrize("reader", ["get_job", "get_job_by_id", "get_latest_job_for_document"])
@pytest.mark.parametrize(
    ("job_status", "document_status", "expected_stage"),
    [
        (PdfJobStatus.RUNNING.value, "processing", "parsing"),
        (PdfJobStatus.PENDING.value, "pending", "pending"),
    ],
)
def test_single_job_readers_reconcile_stale_nonterminal_job(
    monkeypatch,
    reader,
    job_status,
    document_status,
    expected_stage,
):
    job = _build_job(status=job_status)
    if job_status == PdfJobStatus.PENDING.value:
        job.current_stage = "pending"
        job.started_at = None
    document = _build_document(job, status=document_status)
    session = _FakeSession([job, job, document])

    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(service_module, "_stale_timeout_seconds", lambda: 60)

    if reader == "get_job":
        response = service_module.get_job(job_id=job.id, user_id=job.user_id)
    elif reader == "get_job_by_id":
        response = service_module.get_job_by_id(job_id=job.id)
    else:
        response = service_module.get_latest_job_for_document(
            document_id=job.document_id,
            user_id=job.user_id,
        )

    assert response is not None
    assert response.job_id == str(job.id)
    assert response.status == PdfJobStatus.FAILED.value
    assert response.current_stage == expected_stage
    assert response.error_message == (
        "Job marked failed automatically after stale inactivity; "
        "likely interrupted before terminal state update"
    )
    assert response.metadata is not None
    receipt = response.metadata[PDF_PROCESSING_RECEIPT_KEY]
    assert receipt["outcome"] == "failed"
    assert receipt["stages"]["total"]["status"] == "failed"
    assert session.commit_calls == 1
    assert session.refresh_calls == 1
    assert document.status == "failed"
    assert document.processing_started_at == job.started_at
    assert document.processing_completed_at == job.completed_at
    assert document.error_message == response.error_message
    assert session.closed is True


def test_list_jobs_reconciles_stale_cancel_requested_job(monkeypatch):
    job = _build_job(status=PdfJobStatus.CANCEL_REQUESTED.value)
    document = _build_document(job)
    session = _FakeSession([[job], job, document, 1, [job]])
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(service_module, "_stale_timeout_seconds", lambda: 60)

    response = service_module.list_jobs(user_id=job.user_id)

    assert response.total == 1
    assert response.jobs[0].status == PdfJobStatus.CANCELLED.value
    assert response.jobs[0].metadata is not None
    receipt = response.jobs[0].metadata[PDF_PROCESSING_RECEIPT_KEY]
    assert receipt["outcome"] == "cancelled"
    assert document.status == "failed"
    assert document.processing_completed_at == job.completed_at
    assert document.error_message == "Cancellation finalized automatically after stale inactivity"
    assert session.commit_calls == 1


@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
def test_stale_job_reconciliation_leaves_terminal_document_unchanged(
    monkeypatch,
    terminal_status,
):
    job = _build_job(status=PdfJobStatus.RUNNING.value)
    document = _build_document(job, status=terminal_status)
    completed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    document.processing_started_at = completed_at - timedelta(hours=1)
    document.processing_completed_at = completed_at
    session = _FakeSession([job, job, document])
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(service_module, "_stale_timeout_seconds", lambda: 60)

    response = service_module.get_job_by_id(job_id=job.id)

    assert response is not None
    assert response.status == PdfJobStatus.FAILED.value
    assert document.status == terminal_status
    assert document.processing_started_at == completed_at - timedelta(hours=1)
    assert document.processing_completed_at == completed_at
    assert document.error_message is None
    assert session.commit_calls == 1


@pytest.mark.parametrize(
    ("finalizer", "expected_job_status", "expected_error"),
    [
        ("failed", PdfJobStatus.FAILED.value, "Extractor crashed"),
        ("cancelled", PdfJobStatus.CANCELLED.value, "Curator cancelled"),
    ],
)
def test_explicit_terminal_finalizers_reconcile_document(
    monkeypatch,
    finalizer,
    expected_job_status,
    expected_error,
):
    job = _build_job(status=PdfJobStatus.RUNNING.value)
    document = _build_document(job)
    session = _FakeSession([job, job, document])
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)

    if finalizer == "failed":
        response = service_module.mark_failed(job_id=job.id, message=expected_error)
    else:
        response = service_module.mark_cancelled(job_id=job.id, reason=expected_error)

    assert response is not None
    assert response.status == expected_job_status
    assert document.status == "failed"
    assert document.processing_started_at == job.started_at
    assert document.processing_completed_at == job.completed_at
    assert document.error_message == expected_error
    assert response.metadata is not None
    receipt = response.metadata[PDF_PROCESSING_RECEIPT_KEY]
    assert receipt["outcome"] == (
        "cancelled" if finalizer == "cancelled" else "failed"
    )
    assert receipt["stages"]["total"]["status"] == receipt["outcome"]
    assert session.commit_calls == 1


def test_mark_completed_atomically_preserves_detailed_receipt_in_api_metadata(monkeypatch):
    job = _build_job(status=PdfJobStatus.RUNNING.value)
    session = _FakeSession(job)
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)
    detailed_receipt = {
        "schema_version": 1,
        "outcome": "completed",
        "selection": {"cache_hit": True},
        "stages": {"external_request": {"status": "completed", "duration_ms": 12.3}},
    }

    response = service_module.mark_completed(
        job_id=job.id,
        metadata={PDF_PROCESSING_RECEIPT_KEY: detailed_receipt},
    )

    assert response is not None
    assert response.metadata == {PDF_PROCESSING_RECEIPT_KEY: detailed_receipt}
    assert job.metadata_json == response.metadata
    assert session.commit_calls == 1


def test_mark_failed_atomically_preserves_detailed_receipt_in_api_metadata(monkeypatch):
    job = _build_job(status=PdfJobStatus.RUNNING.value)
    document = _build_document(job)
    session = _FakeSession([job, job, document])
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)
    detailed_receipt = {
        "schema_version": 1,
        "outcome": "failed",
        "selection": {"extraction_method": "pdf_service"},
        "stages": {
            "external_request": {
                "status": "failed",
                "duration_ms": 456.7,
            }
        },
    }

    response = service_module.mark_failed(
        job_id=job.id,
        message="extractor failed",
        metadata={PDF_PROCESSING_RECEIPT_KEY: detailed_receipt},
    )

    assert response is not None
    assert response.metadata == {PDF_PROCESSING_RECEIPT_KEY: detailed_receipt}
    assert job.metadata_json == response.metadata
    assert document.status == "failed"
    assert session.commit_calls == 1


def test_terminal_reconciliation_honors_configured_document_error_limit(monkeypatch):
    job = _build_job(status=PdfJobStatus.RUNNING.value)
    document = _build_document(job)
    session = _FakeSession([job, job, document])
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)
    monkeypatch.setenv("PDF_DOCUMENT_ERROR_MESSAGE_MAX_CHARS", "7")

    response = service_module.mark_failed(job_id=job.id, message="extractor crashed")

    assert response is not None
    assert response.error_message == "extractor crashed"
    assert document.error_message == "extract"


@pytest.mark.parametrize("finalizer", ["failed", "cancelled"])
def test_explicit_terminal_finalizers_leave_completed_document_unchanged(
    monkeypatch,
    finalizer,
):
    job = _build_job(status=PdfJobStatus.RUNNING.value)
    document = _build_document(job, status="completed")
    completed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    document.processing_started_at = completed_at - timedelta(hours=1)
    document.processing_completed_at = completed_at
    document.error_message = None
    session = _FakeSession([job, job, document])
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)

    if finalizer == "failed":
        response = service_module.mark_failed(job_id=job.id, message="Late failure")
    else:
        response = service_module.mark_cancelled(job_id=job.id, reason="Late cancellation")

    assert response is not None
    assert document.status == "completed"
    assert document.processing_started_at == completed_at - timedelta(hours=1)
    assert document.processing_completed_at == completed_at
    assert document.error_message is None
    assert session.commit_calls == 1


def test_repeated_finalizer_reconciles_from_durable_first_terminal_winner(monkeypatch):
    job = _build_job(status=PdfJobStatus.CANCELLED.value)
    job.completed_at = datetime.now(timezone.utc)
    job.message = "Original cancellation"
    document = _build_document(job)
    session = _FakeSession([job, job, document])
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)

    response = service_module.mark_failed(job_id=job.id, message="Late failure")

    assert response is not None
    assert response.status == PdfJobStatus.CANCELLED.value
    assert response.message == "Original cancellation"
    assert document.status == "failed"
    assert document.error_message == "Original cancellation"
    assert session.commit_calls == 1


def test_no_job_orphan_dry_run_reports_without_writes():
    now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    document = PDFDocument(
        id=uuid4(),
        filename="private-paper.pdf",
        file_path="private-paper.pdf",
        file_hash=uuid4().hex,
        file_size=100,
        page_count=1,
        user_id=42,
        status="pending",
        upload_timestamp=now - timedelta(days=7),
    )
    session = _FakeSession([[document]])

    summary = service_module._reconcile_pending_documents_without_jobs(
        session,
        apply=False,
        cutoff=now - timedelta(days=1),
        batch_size=25,
        now=now,
    )

    assert summary.dry_run is True
    assert summary.qualifying_count == 1
    assert summary.records[0].status == "would_fail"
    assert summary.records[0].job_id is None
    assert "private-paper.pdf" not in str(summary.to_json())
    assert session.added == []
    assert session.flush_calls == 0
    compiled = str(session.statements[0])
    assert "pdf_documents.status" in compiled
    assert "pdf_documents.upload_timestamp" in compiled
    assert "NOT (EXISTS" in compiled
    assert "pdf_processing_jobs.document_id = pdf_documents.id" in compiled


def test_no_job_orphan_apply_creates_canonical_failed_job_and_reconciles_document():
    now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    document = PDFDocument(
        id=uuid4(),
        filename="paper.pdf",
        file_path="paper.pdf",
        file_hash=uuid4().hex,
        file_size=100,
        page_count=1,
        user_id=42,
        status="pending",
        upload_timestamp=now - timedelta(days=7),
        error_message=None,
    )
    session = _FakeSession(
        [
            [document],
            lambda current_session: current_session.added[-1],
            document,
        ]
    )

    summary = service_module._reconcile_pending_documents_without_jobs(
        session,
        apply=True,
        cutoff=now - timedelta(days=1),
        batch_size=25,
        now=now,
    )

    assert summary.dry_run is False
    assert summary.qualifying_count == 1
    assert summary.records[0].status == "failed"
    assert summary.records[0].job_id is not None
    job = session.added[0]
    assert job.status == PdfJobStatus.FAILED.value
    assert job.error_message == service_module.NO_JOB_ORPHAN_FAILURE_MESSAGE
    assert job.started_at == document.upload_timestamp
    assert job.completed_at == now
    assert job.metadata_json[PDF_PROCESSING_RECEIPT_KEY]["outcome"] == "failed"
    assert document.status == "failed"
    assert document.processing_started_at == document.upload_timestamp
    assert document.processing_completed_at == now
    assert document.error_message == service_module.NO_JOB_ORPHAN_FAILURE_MESSAGE


def test_no_job_orphan_repair_is_idempotent_when_no_pending_no_job_rows_remain():
    now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    session = _FakeSession([[]])

    summary = service_module._reconcile_pending_documents_without_jobs(
        session,
        apply=True,
        cutoff=now - timedelta(days=1),
        batch_size=25,
        now=now,
    )

    assert summary.qualifying_count == 0
    assert session.added == []
    assert session.flush_calls == 0


def test_no_job_orphan_repair_retries_transient_database_failure(monkeypatch):
    failure = OperationalError("select", {}, RuntimeError("transient"))
    failed_session = _FakeSession([failure])
    successful_session = _FakeSession([[]])
    sessions = iter((failed_session, successful_session))
    monkeypatch.setattr(service_module, "SessionLocal", lambda: next(sessions))
    monkeypatch.setattr(
        service_module,
        "get_pdf_no_job_orphan_threshold_seconds",
        lambda: 86400,
    )
    monkeypatch.setattr(
        service_module,
        "get_pdf_no_job_orphan_batch_size",
        lambda: 10,
    )
    monkeypatch.setattr(
        service_module,
        "get_pdf_no_job_orphan_repair_timeout_seconds",
        lambda: 30,
    )
    monkeypatch.setattr(
        service_module,
        "get_pdf_no_job_orphan_repair_retry_count",
        lambda: 1,
    )

    summary = service_module.reconcile_pending_documents_without_jobs(
        apply=False,
        now=datetime(2026, 8, 27, 12, tzinfo=timezone.utc),
    )

    assert summary.qualifying_count == 0
    assert failed_session.rollback_calls == 1
    assert failed_session.closed is True
    assert successful_session.rollback_calls == 1
    assert successful_session.closed is True


def test_terminal_reconciliation_rejects_missing_failure_message(monkeypatch):
    job = _build_job(status=PdfJobStatus.FAILED.value)
    job.completed_at = datetime.now(timezone.utc)
    job.message = None
    job.error_message = None
    document = _build_document(job)
    session = _FakeSession([job, job, document])
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)

    with pytest.raises(ValueError, match=f"Terminal job {job.id} has no failure message"):
        service_module.mark_failed(job_id=job.id, message="Late failure")

    assert session.commit_calls == 0
    assert session.rollback_calls == 1
    assert document.status == "processing"


def test_terminal_reconciliation_rejects_missing_completed_at(monkeypatch):
    job = _build_job(status=PdfJobStatus.CANCELLED.value)
    job.message = "Original cancellation"
    document = _build_document(job)
    session = _FakeSession([job, job, document])
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)

    with pytest.raises(ValueError, match=f"Terminal job {job.id} has no completed_at"):
        service_module.mark_cancelled(job_id=job.id, reason="Late cancellation")

    assert session.commit_calls == 0
    assert session.rollback_calls == 1
    assert document.status == "processing"
    assert document.processing_started_at is None
    assert document.processing_completed_at is None
    assert document.error_message is None


def test_document_reconciliation_failure_rolls_back_job_transition(monkeypatch):
    job = _build_job(status=PdfJobStatus.RUNNING.value)
    document = _build_document(job)
    session = _FakeSession(
        [job, job, document],
        commit_error=RuntimeError("document write failed"),
    )
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)

    with pytest.raises(RuntimeError, match="document write failed"):
        service_module.mark_failed(job_id=job.id, message="Extractor crashed")

    assert session.commit_calls == 1
    assert session.rollback_calls == 1
    assert session.closed is True


def test_older_terminal_job_does_not_overwrite_document_when_newer_job_exists(monkeypatch):
    old_job = _build_job(status=PdfJobStatus.RUNNING.value)
    newer_job = _build_job(status=PdfJobStatus.RUNNING.value)
    newer_job.document_id = old_job.document_id
    newer_job.created_at = old_job.created_at + timedelta(minutes=1)
    document = _build_document(old_job)
    session = _FakeSession([old_job, newer_job])
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)

    response = service_module.mark_cancelled(job_id=old_job.id, reason="Superseded")

    assert response is not None
    assert response.status == PdfJobStatus.CANCELLED.value
    assert document.status == "processing"
    assert document.processing_completed_at is None
    assert session.commit_calls == 1
