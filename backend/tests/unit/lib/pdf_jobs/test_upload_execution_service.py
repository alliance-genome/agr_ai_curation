"""Unit tests for upload execution orchestration service."""

from pathlib import Path

import pytest

from src.lib.pdf_jobs import upload_execution_service as service_module
from src.lib.pdf_jobs.upload_execution_service import UploadExecutionRequest, UploadExecutionService
from src.models.pipeline import ProcessingStage


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


@pytest.mark.asyncio
async def test_execute_upload_marks_completed_for_success(monkeypatch):
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _Orchestrator({"status": "completed"}),
    )

    events = {"completed": [], "failed": [], "cancelled": []}
    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_completed", lambda **kwargs: events["completed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: events["failed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-1",
            job_id="job-1",
            user_id="user-1",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert len(events["completed"]) == 1
    assert not events["failed"]
    assert not events["cancelled"]
    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED


@pytest.mark.asyncio
async def test_execute_upload_marks_failed_for_failure_result(monkeypatch):
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _Orchestrator({"status": "failed", "error": "boom"}),
    )

    events = {"completed": [], "failed": [], "cancelled": []}
    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_completed", lambda **kwargs: events["completed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: events["failed"].append(kwargs))
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-2",
            job_id="job-2",
            user_id="user-2",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert len(events["failed"]) == 1
    assert events["failed"][0]["message"] == "boom"
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
