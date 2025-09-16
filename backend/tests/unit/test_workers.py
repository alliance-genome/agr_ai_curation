"""Unit tests for the embedding worker pool handling job execution and rate limiting."""

from __future__ import annotations

from typing import Dict
from uuid import uuid4
from unittest.mock import MagicMock

import pytest

from app.models import EmbeddingJob, JobType


@pytest.fixture
def job_queue_mock() -> MagicMock:
    """Provide a job queue mock with the required interface."""
    mock = MagicMock()
    mock.dequeue_job.return_value = None
    return mock


@pytest.fixture
def rate_limiter_mock() -> MagicMock:
    """Provide a rate limiter mock so sleeps can be asserted."""
    limiter = MagicMock()
    return limiter


@pytest.fixture
def sleep_mock() -> MagicMock:
    """Mock sleep function to avoid real delays in tests."""
    return MagicMock()


def _make_job(
    job_type: JobType = JobType.EMBED_PDF, config: Dict | None = None
) -> EmbeddingJob:
    return EmbeddingJob(
        id=uuid4(),
        pdf_id=uuid4(),
        job_type=job_type,
        config=config or {"model": "text-embedding-3-small"},
        total_items=10,
    )


def test_worker_processes_embed_job_and_marks_done(
    job_queue_mock, rate_limiter_mock, sleep_mock
):
    """Worker should pull an embedding job, process it, and mark it done."""
    job = _make_job()
    job_queue_mock.dequeue_job.return_value = job

    embedding_service = MagicMock()
    embedding_service.embed_pdf.return_value = {"embedded_chunks": 10}

    from app.workers.embedding_worker import EmbeddingWorkerPool  # noqa: PLC0415

    worker = EmbeddingWorkerPool(
        job_queue=job_queue_mock,
        embedding_service=embedding_service,
        worker_id="worker-1",
        rate_limiter=rate_limiter_mock,
        poll_interval=0.0,
        sleep_fn=sleep_mock,
    )

    processed = worker.process_once()

    assert processed is True
    rate_limiter_mock.acquire.assert_called_once()
    embedding_service.embed_pdf.assert_called_once_with(
        pdf_id=job.pdf_id,
        config=job.config or {},
    )
    job_queue_mock.mark_job_done.assert_called_once_with(
        job.id, result={"embedded_chunks": 10}
    )
    job_queue_mock.mark_job_failed.assert_not_called()
    sleep_mock.assert_not_called()


def test_worker_rate_limit_error_requeues_job(
    job_queue_mock, rate_limiter_mock, sleep_mock
):
    """Rate limit errors should mark the job for retry and trigger a backoff sleep."""
    job = _make_job()
    job_queue_mock.dequeue_job.return_value = job

    embedding_service = MagicMock()

    from app.workers.embedding_worker import (
        EmbeddingWorkerPool,
        RateLimitError,
    )  # noqa: PLC0415

    embedding_service.embed_pdf.side_effect = RateLimitError("slow down")

    worker = EmbeddingWorkerPool(
        job_queue=job_queue_mock,
        embedding_service=embedding_service,
        worker_id="worker-2",
        rate_limiter=rate_limiter_mock,
        poll_interval=0.0,
        sleep_fn=sleep_mock,
        backoff_seconds=2.5,
    )

    processed = worker.process_once()

    assert processed is True
    job_queue_mock.mark_job_done.assert_not_called()
    job_queue_mock.mark_job_failed.assert_called_once()
    _, kwargs = job_queue_mock.mark_job_failed.call_args
    assert kwargs["retry"] is True
    assert "slow down" in kwargs["error_log"]
    sleep_mock.assert_called_once_with(pytest.approx(2.5))


def test_worker_unexpected_error_marks_failed_without_retry(
    job_queue_mock, rate_limiter_mock, sleep_mock
):
    """Unexpected errors should mark the job as failed without retry and avoid sleeping."""
    job = _make_job()
    job_queue_mock.dequeue_job.return_value = job

    embedding_service = MagicMock()
    embedding_service.embed_pdf.side_effect = RuntimeError("boom")

    from app.workers.embedding_worker import EmbeddingWorkerPool  # noqa: PLC0415

    worker = EmbeddingWorkerPool(
        job_queue=job_queue_mock,
        embedding_service=embedding_service,
        worker_id="worker-3",
        rate_limiter=rate_limiter_mock,
        poll_interval=0.0,
        sleep_fn=sleep_mock,
    )

    processed = worker.process_once()

    assert processed is True
    job_queue_mock.mark_job_done.assert_not_called()
    job_queue_mock.mark_job_failed.assert_called_once()
    _, kwargs = job_queue_mock.mark_job_failed.call_args
    assert kwargs["retry"] is False
    assert "boom" in kwargs["error_log"]
    sleep_mock.assert_not_called()
