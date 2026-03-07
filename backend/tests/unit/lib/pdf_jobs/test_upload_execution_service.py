"""Unit tests for upload execution orchestration service."""

from pathlib import Path

import pytest
from fastapi import BackgroundTasks

from src.lib.exceptions import PDFCancellationError
from src.lib.pdf_jobs import upload_execution_service as service_module
from src.lib.pdf_jobs.upload_execution_service import (
    JobAwarePipelineTracker,
    UploadExecutionRequest,
    UploadExecutionService,
)
from src.models.pipeline import ProcessingStage
from src.models.sql.pdf_processing_job import PdfJobStatus


class _Tracker:
    def __init__(self):
        self.calls = []

    async def track_pipeline_progress(self, document_id, stage, progress_percentage=None, message=None):
        self.calls.append(
            {
                "document_id": document_id,
                "stage": stage,
                "progress_percentage": progress_percentage,
                "message": message,
            }
        )
        return None


class _Orchestrator:
    def __init__(self, result):
        self.result = result

    async def process_pdf_document(self, **_kwargs):
        return self.result


class _RaisingOrchestrator:
    def __init__(self, exc):
        self.exc = exc

    async def process_pdf_document(self, **_kwargs):
        raise self.exc


class _RaisingTracker(_Tracker):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    async def track_pipeline_progress(self, document_id, stage, progress_percentage=None, message=None):
        raise self.exc


class _MidRunCancellingOrchestrator:
    def __init__(self, tracker, cancel_state):
        self.tracker = tracker
        self.cancel_state = cancel_state

    async def process_pdf_document(
        self,
        *,
        document_id,
        cancel_requested_callback,
        process_id_callback,
        **_kwargs,
    ):
        assert await cancel_requested_callback() is False
        await process_id_callback("proc-123")
        self.cancel_state["value"] = True
        await self.tracker.track_pipeline_progress(
            document_id,
            ProcessingStage.PARSING,
            progress_percentage=35,
            message="Parsing started",
        )


@pytest.mark.asyncio
async def test_execute_upload_marks_completed_for_success(monkeypatch):
    job_id = "00000000-0000-0000-0000-000000000001"
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _Orchestrator({"status": "completed"}),
    )

    events = {"completed": [], "failed": [], "cancelled": []}
    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_completed", lambda **kwargs: events["completed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: events["failed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-1",
            job_id=job_id,
            user_id="user-1",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert events["completed"] == [{"job_id": job_id, "message": "Processing completed"}]
    assert not events["failed"]
    assert not events["cancelled"]
    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED


@pytest.mark.asyncio
async def test_execute_upload_marks_failed_for_failure_result(monkeypatch):
    job_id = "00000000-0000-0000-0000-000000000002"
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _Orchestrator({"status": "failed", "error": "boom"}),
    )

    events = {"completed": [], "failed": [], "cancelled": []}
    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_completed", lambda **kwargs: events["completed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: events["failed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-2",
            job_id=job_id,
            user_id="user-2",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert events["failed"] == [{"job_id": job_id, "message": "boom", "stage": ProcessingStage.FAILED.value}]
    assert not events["completed"]
    assert not events["cancelled"]
    assert tracker.calls[-1]["stage"] == ProcessingStage.FAILED


@pytest.mark.asyncio
async def test_execute_upload_handles_pre_start_cancellation(monkeypatch):
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _Orchestrator({"status": "completed"}),
    )

    events = {"cancelled": []}
    status_updates = []

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: True)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-3",
            job_id="job-3",
            user_id="user-3",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert len(events["cancelled"]) == 1
    assert status_updates == [("doc-3", "user-3", "failed")]
    assert tracker.calls[-1]["stage"] == ProcessingStage.FAILED


@pytest.mark.asyncio
async def test_job_aware_tracker_raises_for_mid_run_cancellation(monkeypatch):
    tracker = _Tracker()
    job_tracker = JobAwarePipelineTracker(base_tracker=tracker, job_id="job-mid-cancel")
    progress_updates = []

    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: True)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "update_progress",
        lambda **kwargs: progress_updates.append(kwargs),
    )

    with pytest.raises(PDFCancellationError, match="Processing cancelled by user request"):
        await job_tracker.track_pipeline_progress(
            "doc-mid-cancel",
            ProcessingStage.PARSING,
            progress_percentage=35,
            message="Parsing in progress",
        )

    assert tracker.calls == []
    assert progress_updates == []


@pytest.mark.asyncio
async def test_job_aware_tracker_preserves_cancel_requested_on_failed_terminal_race(monkeypatch):
    tracker = _Tracker()
    job_tracker = JobAwarePipelineTracker(base_tracker=tracker, job_id="job-terminal-race")
    progress_updates = []
    failed_events = []

    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: True)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "update_progress",
        lambda **kwargs: progress_updates.append(kwargs),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs),
    )

    await job_tracker.track_pipeline_progress(
        "doc-terminal-race",
        ProcessingStage.FAILED,
        progress_percentage=63,
        message="Cancellation requested",
    )

    assert tracker.calls[-1]["stage"] == ProcessingStage.FAILED
    assert progress_updates == [
        {
            "job_id": "job-terminal-race",
            "stage": "cancel_requested",
            "progress_percentage": 63,
            "message": "Cancellation requested",
            "status": "cancel_requested",
        }
    ]
    assert failed_events == []


@pytest.mark.asyncio
async def test_job_aware_tracker_allows_completed_terminal_race_after_cancel(monkeypatch):
    tracker = _Tracker()
    job_tracker = JobAwarePipelineTracker(base_tracker=tracker, job_id="job-terminal-complete")
    completed_events = []
    progress_updates = []

    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: True)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: completed_events.append(kwargs),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "update_progress",
        lambda **kwargs: progress_updates.append(kwargs),
    )

    await job_tracker.track_pipeline_progress(
        "doc-terminal-complete",
        ProcessingStage.COMPLETED,
        progress_percentage=100,
        message="Completed before cancellation landed",
    )

    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED
    assert completed_events == [
        {
            "job_id": "job-terminal-complete",
            "message": "Completed before cancellation landed",
        }
    ]
    assert progress_updates == []


@pytest.mark.asyncio
async def test_dispatch_upload_execution_tracks_upload_and_queues_task():
    tracker = _Tracker()
    service = UploadExecutionService(pipeline_tracker=tracker)
    background_tasks = BackgroundTasks()
    request = UploadExecutionRequest(
        document_id="doc-dispatch",
        job_id="job-dispatch",
        user_id="user-dispatch",
        file_path=Path("/tmp/paper.pdf"),
    )

    await service.dispatch_upload_execution(background_tasks=background_tasks, request=request)

    assert tracker.calls[-1]["document_id"] == "doc-dispatch"
    assert tracker.calls[-1]["stage"] == ProcessingStage.UPLOAD
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func == service.execute_upload
    assert background_tasks.tasks[0].args == (request,)


@pytest.mark.asyncio
async def test_execute_upload_marks_failed_when_orchestrator_raises(monkeypatch):
    job_id = "00000000-0000-0000-0000-000000000003"
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _RaisingOrchestrator(RuntimeError("pipeline down")),
    )

    events = {"failed": [], "cancelled": []}
    status_updates = []

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: events["failed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-exc-1",
            job_id=job_id,
            user_id="user-exc-1",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert status_updates == [("doc-exc-1", "user-exc-1", "failed")]
    assert events["failed"] == [{"job_id": job_id, "message": "pipeline down", "stage": ProcessingStage.FAILED.value}]
    assert not events["cancelled"]


@pytest.mark.asyncio
async def test_execute_upload_marks_cancelled_when_orchestrator_returns_cancelled_result(monkeypatch):
    job_id = "00000000-0000-0000-0000-000000000004"
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _Orchestrator(
            {"status": "cancelled", "message": "cancelled by user"}
        ),
    )

    events = {"completed": [], "failed": [], "cancelled": []}
    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_completed", lambda **kwargs: events["completed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: events["failed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-cancelled-result",
            job_id=job_id,
            user_id="user-cancelled-result",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert tracker.calls[-1]["stage"] == ProcessingStage.FAILED
    assert tracker.calls[-1]["message"] == "cancelled by user"
    assert events["cancelled"] == [{"job_id": job_id, "reason": "cancelled by user"}]
    assert events["completed"] == []
    assert events["failed"] == []


@pytest.mark.asyncio
async def test_execute_upload_marks_failed_when_terminal_tracker_sync_raises(monkeypatch):
    job_id = "00000000-0000-0000-0000-000000000005"
    tracker = _RaisingTracker(RuntimeError("tracker unavailable"))
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _Orchestrator({"status": "failed", "error": "boom"}),
    )

    events = {"failed": [], "cancelled": []}
    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: events["failed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-tracker-failure",
            job_id=job_id,
            user_id="user-tracker-failure",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert events["failed"] == [
        {
            "job_id": job_id,
            "message": "boom",
            "stage": ProcessingStage.FAILED.value,
        }
    ]
    assert events["cancelled"] == []


@pytest.mark.asyncio
async def test_execute_upload_marks_cancelled_when_orchestrator_raises_cancellation(monkeypatch):
    job_id = "00000000-0000-0000-0000-000000000006"
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _RaisingOrchestrator(PDFCancellationError("cancelled in pipeline")),
    )

    events = {"failed": [], "cancelled": []}
    status_updates = []

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: events["failed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-exc-2",
            job_id=job_id,
            user_id="user-exc-2",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert status_updates == [("doc-exc-2", "user-exc-2", "failed")]
    assert not events["failed"]
    assert events["cancelled"] == [{"job_id": job_id, "reason": "cancelled in pipeline"}]


@pytest.mark.asyncio
async def test_execute_upload_marks_cancelled_for_mid_run_cancellation(monkeypatch):
    job_id = "00000000-0000-0000-0000-000000000007"
    tracker = _Tracker()
    cancel_state = {"value": False}
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, tracker: _MidRunCancellingOrchestrator(tracker, cancel_state),
    )

    events = {"failed": [], "cancelled": []}
    status_updates = []
    process_ids = []

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: cancel_state["value"])
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "set_process_id",
        lambda **kwargs: process_ids.append(kwargs),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: events["failed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-mid",
            job_id=job_id,
            user_id="user-mid",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert process_ids == [{"job_id": job_id, "process_id": "proc-123"}]
    assert status_updates == [("doc-mid", "user-mid", "failed")]
    assert not events["failed"]
    assert events["cancelled"] == [{"job_id": job_id, "reason": "Processing cancelled by user request"}]


@pytest.mark.parametrize(
    "status",
    [
        PdfJobStatus.RUNNING.value,
        PdfJobStatus.CANCEL_REQUESTED.value,
        PdfJobStatus.COMPLETED.value,
        PdfJobStatus.FAILED.value,
        PdfJobStatus.CANCELLED.value,
    ],
)
@pytest.mark.asyncio
async def test_execute_upload_skips_replayed_job_for_non_pending_durable_status(monkeypatch, status):
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda *_args, **_kwargs: pytest.fail("orchestrator should not run for replayed jobs"),
    )

    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "get_job_by_id",
        lambda **_kwargs: type("Job", (), {"status": status})(),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "update_progress",
        lambda **_kwargs: pytest.fail("progress should not update for replayed jobs"),
    )

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-replay",
            job_id="job-replay",
            user_id="user-replay",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert tracker.calls == []
