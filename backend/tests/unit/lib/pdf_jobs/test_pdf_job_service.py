"""Unit tests for durable PDF job service helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.lib.pdf_jobs import service as service_module
from src.models.sql.pdf_processing_job import PdfJobStatus, PdfProcessingJob


class _ScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self.row = row
        self.commit_calls = 0
        self.refresh_calls = 0
        self.closed = False

    def execute(self, *_args, **_kwargs):
        return _ScalarResult(self.row)

    def commit(self):
        self.commit_calls += 1

    def refresh(self, row):
        assert row is self.row
        self.refresh_calls += 1

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


def test_get_job_by_id_returns_none_when_missing(monkeypatch):
    session = _FakeSession(None)
    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)

    assert service_module.get_job_by_id(job_id=uuid4()) is None
    assert session.commit_calls == 0
    assert session.refresh_calls == 0
    assert session.closed is True


def test_get_job_by_id_reconciles_stale_active_job(monkeypatch):
    job = _build_job(status=PdfJobStatus.RUNNING.value)
    session = _FakeSession(job)

    monkeypatch.setattr(service_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(service_module, "_stale_timeout_seconds", lambda: 60)

    response = service_module.get_job_by_id(job_id=job.id)

    assert response is not None
    assert response.job_id == str(job.id)
    assert response.status == PdfJobStatus.FAILED.value
    assert response.current_stage == "parsing"
    assert response.error_message == (
        "Job marked failed automatically after stale inactivity; "
        "likely interrupted before terminal state update"
    )
    assert session.commit_calls == 1
    assert session.refresh_calls == 1
    assert session.closed is True
