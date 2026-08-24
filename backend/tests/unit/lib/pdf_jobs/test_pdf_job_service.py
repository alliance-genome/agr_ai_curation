"""Unit tests for durable PDF job service helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.lib.pdf_jobs import service as service_module
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

    def execute(self, *_args, **_kwargs):
        if not self.responses:
            raise AssertionError("unexpected query")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _ScalarResult(response)

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
    assert session.commit_calls == 1


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
