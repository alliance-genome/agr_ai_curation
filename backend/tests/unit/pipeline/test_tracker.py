"""Unit tests for pipeline status tracker behavior."""

import asyncio as py_asyncio
from datetime import datetime, timedelta

import pytest

import src.lib.pipeline.tracker as tracker_module
from src.lib.pipeline.tracker import PipelineTracker, RetryConfig
from src.models.pipeline import ProcessingStage


@pytest.mark.asyncio
async def test_track_pipeline_progress_sets_terminal_completed_at():
    tracker = PipelineTracker()

    status = await tracker.track_pipeline_progress("doc-terminal", ProcessingStage.COMPLETED)
    assert status.progress_percentage == 100
    assert status.completed_at is not None


@pytest.mark.asyncio
async def test_track_pipeline_progress_clears_completed_at_when_resumed():
    tracker = PipelineTracker()
    document_id = "doc-resume"

    terminal_status = await tracker.track_pipeline_progress(document_id, ProcessingStage.COMPLETED)
    assert terminal_status.completed_at is not None

    resumed_status = await tracker.track_pipeline_progress(document_id, ProcessingStage.PARSING)
    assert resumed_status.completed_at is None
    assert resumed_status.progress_percentage == 30


@pytest.mark.asyncio
async def test_track_pipeline_progress_clamps_explicit_progress_values():
    tracker = PipelineTracker()
    await tracker.track_pipeline_progress("doc-clamp", ProcessingStage.UPLOAD, progress_percentage=110)
    status = await tracker.track_pipeline_progress("doc-clamp", ProcessingStage.PARSING, progress_percentage=-5)
    assert status.progress_percentage == 0


@pytest.mark.asyncio
async def test_get_pipeline_status_enriches_results_and_errors():
    tracker = PipelineTracker()
    document_id = "doc-status"

    await tracker.track_pipeline_progress(document_id, ProcessingStage.PARSING)
    tracker.record_stage_result(document_id, ProcessingStage.PARSING, success=True, duration_seconds=0.25)
    await tracker.handle_pipeline_failure(document_id, RuntimeError("boom"), stage=ProcessingStage.PARSING)

    status = await tracker.get_pipeline_status(document_id)
    assert status is not None
    assert status.processing_time_seconds is not None
    assert status.error_count == 1
    assert len(status.stage_results) == 1


@pytest.mark.asyncio
async def test_handle_pipeline_failure_uses_current_stage_and_preserves_retry_count():
    tracker = PipelineTracker()
    document_id = "doc-fail"
    await tracker.track_pipeline_progress(document_id, ProcessingStage.PARSING)
    tracker.retry_attempts[(document_id, ProcessingStage.PARSING)] = 2

    error = await tracker.handle_pipeline_failure(document_id, ValueError("bad parse"), stage=None)
    status = await tracker.get_pipeline_status(document_id)

    assert error.stage == ProcessingStage.PARSING
    assert error.retry_count == 2
    assert status is not None
    assert status.current_stage == ProcessingStage.FAILED
    assert status.completed_at is not None


@pytest.mark.asyncio
async def test_retry_failed_stage_returns_error_for_non_retryable_stage():
    tracker = PipelineTracker()
    result = await tracker.retry_failed_stage("doc-non-retry", ProcessingStage.CHUNKING)
    assert result["success"] is False
    assert "not retryable" in result["message"]


@pytest.mark.asyncio
async def test_retry_failed_stage_returns_error_when_no_stage_errors():
    tracker = PipelineTracker()
    result = await tracker.retry_failed_stage("doc-no-errors", ProcessingStage.PARSING)
    assert result["success"] is False
    assert "No errors found" in result["message"]


@pytest.mark.asyncio
async def test_retry_failed_stage_success_sets_attempt_and_backoff(monkeypatch):
    tracker = PipelineTracker(RetryConfig(max_retries=3, retry_delay_seconds=5, exponential_backoff=True))
    document_id = "doc-retry"

    await tracker.track_pipeline_progress(document_id, ProcessingStage.PARSING)
    await tracker.handle_pipeline_failure(document_id, RuntimeError("transient"), stage=ProcessingStage.PARSING)

    delays = []

    async def _fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(tracker_module.asyncio, "sleep", _fake_sleep)

    result = await tracker.retry_failed_stage(document_id, ProcessingStage.PARSING)
    assert result["success"] is True
    assert result["retry_attempt"] == 1
    assert result["delay_seconds"] == 5
    assert delays == [5]
    assert tracker.retry_attempts[(document_id, ProcessingStage.PARSING)] == 1
    assert tracker.processing_errors[document_id][-1].retry_count == 1
    assert tracker.pipeline_states[document_id].completed_at is None


@pytest.mark.asyncio
async def test_retry_failed_stage_enforces_max_retries_from_tracker_state():
    tracker = PipelineTracker(RetryConfig(max_retries=1, retry_delay_seconds=1, exponential_backoff=False))
    document_id = "doc-max"

    await tracker.track_pipeline_progress(document_id, ProcessingStage.PARSING)
    await tracker.handle_pipeline_failure(document_id, RuntimeError("err"), stage=ProcessingStage.PARSING)
    tracker.retry_attempts[(document_id, ProcessingStage.PARSING)] = 1

    result = await tracker.retry_failed_stage(document_id, ProcessingStage.PARSING)
    assert result["success"] is False
    assert "Maximum retries" in result["message"]


@pytest.mark.asyncio
async def test_retry_failed_stage_serializes_parallel_attempts(monkeypatch):
    tracker = PipelineTracker(RetryConfig(max_retries=1, retry_delay_seconds=0, exponential_backoff=False))
    document_id = "doc-concurrent"

    await tracker.track_pipeline_progress(document_id, ProcessingStage.PARSING)
    await tracker.handle_pipeline_failure(document_id, RuntimeError("err"), stage=ProcessingStage.PARSING)

    original_sleep = py_asyncio.sleep

    async def _yielding_sleep(_delay):
        await original_sleep(0)

    monkeypatch.setattr(tracker_module.asyncio, "sleep", _yielding_sleep)

    results = await py_asyncio.gather(
        tracker.retry_failed_stage(document_id, ProcessingStage.PARSING),
        tracker.retry_failed_stage(document_id, ProcessingStage.PARSING),
    )
    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "Maximum retries" in failures[0]["message"]


@pytest.mark.asyncio
async def test_statistics_and_clear_completed_pipeline_cleanup_retry_state():
    tracker = PipelineTracker()
    old_completed_doc = "doc-old-completed"
    failed_doc = "doc-failed"
    in_progress_doc = "doc-in-progress"

    await tracker.track_pipeline_progress(old_completed_doc, ProcessingStage.COMPLETED)
    tracker.pipeline_states[old_completed_doc].completed_at = datetime.now() - timedelta(hours=48)
    tracker.record_stage_result(old_completed_doc, ProcessingStage.COMPLETED, success=True, duration_seconds=1.0)
    tracker.processing_errors[old_completed_doc] = []
    tracker.retry_attempts[(old_completed_doc, ProcessingStage.PARSING)] = 1
    tracker._retry_locks[(old_completed_doc, ProcessingStage.PARSING)] = py_asyncio.Lock()

    await tracker.track_pipeline_progress(failed_doc, ProcessingStage.PARSING)
    await tracker.handle_pipeline_failure(failed_doc, RuntimeError("boom"), stage=ProcessingStage.PARSING)

    await tracker.track_pipeline_progress(in_progress_doc, ProcessingStage.UPLOAD)

    stats = tracker.get_pipeline_statistics()
    assert stats["total_pipelines"] == 3
    assert stats["completed"] == 1
    assert stats["failed"] == 1
    assert stats["in_progress"] == 1
    assert stats["total_errors"] >= 1

    cleared = tracker.clear_completed_pipelines(older_than_hours=24)
    assert cleared == 1
    assert old_completed_doc not in tracker.pipeline_states
    assert (old_completed_doc, ProcessingStage.PARSING) not in tracker.retry_attempts
    assert (old_completed_doc, ProcessingStage.PARSING) not in tracker._retry_locks
