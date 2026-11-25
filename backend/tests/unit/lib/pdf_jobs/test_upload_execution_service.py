"""Unit tests for upload execution orchestration service."""

import asyncio
import hashlib
import inspect
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast, get_type_hints
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
import sentry_sdk
from fastapi import BackgroundTasks
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport
from sqlalchemy.exc import NoResultFound, OperationalError
from src.lib.document_sources.main_text import select_preferred_main_text_artifact
from src.lib.document_sources.models import (
    DocumentSourceAccessDenied,
    DocumentSourceError,
    DocumentSourceProvider,
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceConversionResult,
    SourceConversionStatus,
)
from src.lib.exceptions import PDFCancellationError
from src.lib.observability import sentry
from src.lib.pdf_jobs import upload_execution_service as service_module
from src.lib.pdf_jobs.upload_execution_service import (
    JobAwarePipelineTracker,
    ProviderConversionExecutionRequest,
    ProviderMarkdownExecutionRequest,
    UploadExecutionRequest,
    UploadExecutionService,
)
from src.lib.pipeline.orchestrator import ProcessingResult
from src.lib.pipeline.tracker import PipelineTracker
from src.models.document import ProcessingStatus
from src.models.pipeline import ProcessingStage
from src.models.sql import database as database_module
from src.models.sql.pdf_processing_job import PdfJobStatus
from src.lib.document_sources.models import ProviderBearerKind


@pytest.fixture(autouse=True)
def _conversion_requests_recorded(monkeypatch):
    """The job's record of the conversion requests it sent is a database write;
    here it is kept in memory (KANBAN-2191 / ALL-1725)."""

    recorded: list[dict] = []
    monkeypatch.setattr(
        service_module.pdf_job_service, "record_provider_conversion_request",
        lambda **kwargs: recorded.append(kwargs),
    )
    return recorded


class _Tracker:
    def __init__(self):
        self.calls = []
        self.failure_calls = []

    async def track_pipeline_progress(self, document_id, stage, progress_percentage=None, message=None):
        self.calls.append(
            {
                "document_id": document_id,
                "stage": stage,
                "progress_percentage": progress_percentage,
                "message": message,
            }
        )

    async def handle_pipeline_failure(self, document_id, error, stage=None, *, sentry_reported=False):
        self.failure_calls.append(
            {
                "document_id": document_id,
                "error": error,
                "stage": stage,
            }
        )


class _Orchestrator:
    def __init__(self, result: ProcessingResult):
        self.result = result

    async def process_pdf_document(self, **_kwargs) -> ProcessingResult:
        return self.result


class _TerminalSignallingOrchestrator(_Orchestrator):
    def __init__(self, tracker, result: ProcessingResult):
        super().__init__(result)
        self.tracker = tracker

    async def process_pdf_document(self, *, document_id, **_kwargs) -> ProcessingResult:
        stage = ProcessingStage.COMPLETED if self.result.success else ProcessingStage.FAILED
        if not self.result.success:
            await self.tracker.handle_pipeline_failure(
                document_id,
                RuntimeError(self.result.error or "Processing failed"),
                stage=stage,
            )
        await self.tracker.track_pipeline_progress(
            document_id,
            stage,
            message=self.result.error or "Processing completed",
        )
        return self.result


class _RaisingOrchestrator:
    def __init__(self, exc):
        self.exc = exc

    async def process_pdf_document(self, **_kwargs) -> ProcessingResult:
        raise self.exc


class _RaisingTracker(_Tracker):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    async def track_pipeline_progress(self, document_id, stage, progress_percentage=None, message=None):
        raise self.exc


class _Provider:
    def __init__(
        self,
        payload=b"# Title\n\nBody",
        *,
        provider_id="abc_literature",
        main_file_class="converted_merged_main",
    ):
        self.provider_id = provider_id
        self.main_file_class = main_file_class
        self.payload = payload
        self.payloads = {}
        self.download_errors = {}
        self.downloads = []
        self.conversion_requests = []
        self.list_artifact_calls = []
        self.artifacts = []
        self.conversion_results = []
        self.closed = False

    def conversion_exposes_main_text(self, result):
        if self.provider_id == "abc_literature":
            if "converted_merged_main" in result.converted_classes:
                return True
            if any(
                progress.get("converted", {}).get("file_class") == "converted_merged_main"
                for progress in result.per_file_progress
                if isinstance(progress.get("converted"), dict)
            ):
                return True
            return False
        return result.status in {
            SourceConversionStatus.CONVERTED,
            SourceConversionStatus.RUNNING,
        }

    def is_main_text_artifact(self, artifact):
        file_class = str(artifact.metadata.get("file_class") or "").lower()
        display_name = str(artifact.display_name or "").lower()
        if self.provider_id == "abc_literature":
            return file_class == self.main_file_class and "_tei" not in display_name
        return file_class == self.main_file_class

    def main_text_artifact_sort_key(self, artifact):
        display_name = str(artifact.display_name or "").lower()
        if self.provider_id != "abc_literature":
            return (int(artifact.metadata.get("provider_rank", 0)),)
        return (0,) if "_nxml" in display_name else (1,)

    def conversion_progress_percentage(self, result):
        if result.status in {
            SourceConversionStatus.FAILED,
            SourceConversionStatus.NO_SOURCES,
        }:
            return 100
        return 35 if self.conversion_exposes_main_text(result) else 20

    def conversion_progress_message(self, result):
        if self.conversion_exposes_main_text(result):
            return "ABC Literature main text is ready; importing converted Markdown"
        return "ABC Literature conversion running"

    def conversion_failure_message(self, result):
        if result.error_message:
            return result.error_message
        failures = [
            str(progress.get("error"))
            for progress in result.per_file_progress
            if progress.get("status") == "failed" and progress.get("error")
        ]
        return "; ".join(failures) or "ABC Literature conversion failed"

    async def request_conversion(self, reference, *, wait=False, request_bearer_token=None):
        self.conversion_requests.append(
            {
                "reference": reference,
                "wait": wait,
                "request_bearer_token": request_bearer_token,
            }
        )
        if self.conversion_results:
            return self.conversion_results.pop(0)
        return SourceConversionResult(
            provider=self.provider_id,
            status=SourceConversionStatus.RUNNING,
        )

    async def list_artifacts(self, reference, *, request_bearer_token=None):
        self.list_artifact_calls.append(
            {"reference": reference, "request_bearer_token": request_bearer_token}
        )
        return list(self.artifacts)

    async def download_artifact(self, artifact_id, *, request_bearer_token=None):
        self.downloads.append(
            {
                "artifact_id": artifact_id,
                "request_bearer_token": request_bearer_token,
            }
        )
        if artifact_id in self.download_errors:
            raise self.download_errors[artifact_id]
        return self.payloads.get(artifact_id, self.payload)

    def provider_metadata_artifacts_for_source(self, source_artifact, artifacts):
        return tuple(
            artifact
            for artifact in artifacts
            if artifact.role is SourceArtifactRole.PROVIDER_METADATA
            and (
                artifact.parent_artifact_id == source_artifact.artifact_id
                or (
                    artifact.parent_artifact_id is None
                    and artifact.reference_curie == source_artifact.reference_curie
                )
            )
        )

    async def aclose(self):
        self.closed = True


class _RaisingProvider(_Provider):
    async def download_artifact(self, artifact_id, *, request_bearer_token=None):
        self.downloads.append(
            {
                "artifact_id": artifact_id,
                "request_bearer_token": request_bearer_token,
            }
        )
        raise RuntimeError("provider unavailable")


async def _async_value(value):
    return value


def _pipeline_result(
    *,
    success: bool,
    document_id: str,
    error: str | None = None,
    cancelled: bool = False,
    observability_receipt: dict | None = None,
) -> ProcessingResult:
    return ProcessingResult(
        success=success,
        document_id=document_id,
        stages_completed=[],
        error=error,
        cancelled=cancelled,
        observability_receipt=observability_receipt or {},
    )


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
    ) -> ProcessingResult:
        assert await cancel_requested_callback() is False
        await process_id_callback("proc-123")
        self.cancel_state["value"] = True
        await self.tracker.track_pipeline_progress(
            document_id,
            ProcessingStage.PARSING,
            progress_percentage=35,
            message="Parsing started",
        )
        raise AssertionError("cancellation should interrupt pipeline progress")


def test_provider_execution_requests_require_local_pdf_path():
    for request_type in (
        ProviderMarkdownExecutionRequest,
        ProviderConversionExecutionRequest,
    ):
        file_path = inspect.signature(request_type).parameters["file_path"]
        assert get_type_hints(request_type)["file_path"] is Path
        assert file_path.default is inspect.Parameter.empty


@pytest.mark.asyncio
async def test_execute_upload_marks_completed_for_success(monkeypatch):
    job_id = "00000000-0000-0000-0000-000000000001"
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, job_tracker: _TerminalSignallingOrchestrator(
            job_tracker,
            _pipeline_result(
                success=True,
                document_id="doc-1",
                observability_receipt={
                    "schema_version": 1,
                    "outcome": "completed",
                    "stages": {"total": {"status": "completed"}},
                },
            )
        ),
    )

    events = {"completed": [], "failed": [], "cancelled": []}
    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: events["completed"].append(kwargs)
        or SimpleNamespace(
            status=PdfJobStatus.COMPLETED.value,
            message=kwargs["message"],
        ),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: events["failed"].append(kwargs)
        or SimpleNamespace(
            status=PdfJobStatus.FAILED.value,
            message=kwargs["message"],
        ),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_cancelled",
        lambda **kwargs: events["cancelled"].append(kwargs)
        or SimpleNamespace(
            status=PdfJobStatus.CANCELLED.value,
            message=kwargs["reason"],
        ),
    )

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-1",
            job_id=job_id,
            user_id="user-1",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert events["completed"] == [
        {
            "job_id": job_id,
            "message": "Processing completed",
            "metadata": {
                "pdf_processing_receipt": {
                    "schema_version": 1,
                    "outcome": "completed",
                    "stages": {"total": {"status": "completed"}},
                }
            },
        }
    ]
    assert not events["failed"]
    assert not events["cancelled"]
    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED


@pytest.mark.asyncio
async def test_execute_upload_marks_failed_for_failure_result(monkeypatch):
    job_id = "00000000-0000-0000-0000-000000000002"
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, job_tracker: _TerminalSignallingOrchestrator(
            job_tracker,
            _pipeline_result(
                success=False,
                document_id="doc-2",
                error="boom",
                observability_receipt={
                    "schema_version": 2,
                    "outcome": "failed",
                    "stages": {
                        "external_request": {
                            "status": "failed",
                            "duration_ms": 456.7,
                            "process_id": "proc-failed",
                            "failure_category": "provider_terminal_failure",
                            "failure_boundary": "poll",
                            "provider_status": "failed",
                            "provider_error_code": "publish_failed",
                            "submit_attempt_count": 1,
                            "poll_attempt_count": 4,
                        }
                    },
                },
            ),
        ),
    )

    events = {"completed": [], "failed": [], "cancelled": []}
    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_completed", lambda **kwargs: events["completed"].append(kwargs))
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: events["failed"].append(kwargs)
        or SimpleNamespace(
            status=PdfJobStatus.FAILED.value,
            message=kwargs["message"],
        ),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))

    await service.execute_upload(
        UploadExecutionRequest(
            document_id="doc-2",
            job_id=job_id,
            user_id="user-2",
            file_path=Path("/tmp/paper.pdf"),
        )
    )

    assert events["failed"] == [
        {
            "job_id": job_id,
            "message": "boom",
            "stage": ProcessingStage.FAILED.value,
            "metadata": {
                "pdf_processing_receipt": {
                    "schema_version": 2,
                    "outcome": "failed",
                    "stages": {
                        "external_request": {
                            "status": "failed",
                            "duration_ms": 456.7,
                            "process_id": "proc-failed",
                            "failure_category": "provider_terminal_failure",
                            "failure_boundary": "poll",
                            "provider_status": "failed",
                            "provider_error_code": "publish_failed",
                            "submit_attempt_count": 1,
                            "poll_attempt_count": 4,
                        }
                    },
                }
            },
        }
    ]
    assert not events["completed"]
    assert not events["cancelled"]
    assert len(tracker.failure_calls) == 1
    assert str(tracker.failure_calls[0]["error"]) == "boom"
    assert tracker.calls[-1]["stage"] == ProcessingStage.FAILED


@pytest.mark.asyncio
async def test_execute_upload_handles_pre_start_cancellation(monkeypatch):
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _Orchestrator(
            _pipeline_result(success=True, document_id="doc-3")
        ),
    )

    events = {"cancelled": []}
    status_updates = []

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: True)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_cancelled",
        lambda **kwargs: events["cancelled"].append(kwargs)
        or SimpleNamespace(
            status=PdfJobStatus.CANCELLED.value,
            message=kwargs["reason"],
        ),
    )
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
async def test_job_aware_tracker_leaves_completed_terminal_write_to_executor(monkeypatch):
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
    assert completed_events == []
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
    assert background_tasks.tasks[0].func.__observability_original_task__ == service.execute_upload
    assert background_tasks.tasks[0].func.__observability_task_name__ == "pdf_jobs.execute_upload"
    assert background_tasks.tasks[0].args == (request,)


@pytest.mark.asyncio
@pytest.mark.parametrize("markdown", ["# Title\n\nBody", "\ufeff # Résults\r\n\r\nβ cells.  \r\n"])
async def test_execute_provider_markdown_downloads_with_curator_token_and_marks_completed(monkeypatch, markdown):
    tracker = _Tracker()
    provider = _Provider()
    provider.payload = markdown.encode("utf-8")
    ingestion_calls = []
    progress_updates = []
    completed_events = []

    async def _ingest(request, *, weaviate_client):
        ingestion_calls.append({"request": request, "weaviate_client": weaviate_client})

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=_ingest,
    )

    monkeypatch.setattr(service_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "update_progress",
        lambda **kwargs: progress_updates.append(kwargs),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: completed_events.append(kwargs),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            converted_artifact_id="markdown-1",
            curator_token="curator-token",
            source_provenance={
                "provider": "fake_provider",
                "access_scope": "global",
                "converted_artifact_id": "stale-artifact",
                "converted_artifact_sha256": "0" * 64,
            },
        )
    )

    assert provider.downloads == [
        {
            "artifact_id": "markdown-1",
            "request_bearer_token": "curator-token",
        }
    ]
    assert provider.closed is True
    assert ingestion_calls[0]["weaviate_client"] == "weaviate-client"
    ingestion_request = ingestion_calls[0]["request"]
    assert ingestion_request.document_id == "doc-provider"
    assert ingestion_request.user_id == "user-provider"
    assert ingestion_request.document_owner_user_id == 42
    assert ingestion_request.markdown == markdown
    assert ingestion_request.source_provenance["converted_artifact_id"] == "markdown-1"
    assert ingestion_request.source_provenance["converted_artifact_sha256"] == hashlib.sha256(
        provider.payload
    ).hexdigest()
    assert ingestion_request.filename == "paper.pdf"
    assert progress_updates[0]["message"] == "Downloading converted Markdown"
    assert progress_updates[1]["message"] == "Ingesting converted Markdown"
    assert completed_events == [
        {
            "job_id": "job-provider",
            "message": "Processing completed",
        }
    ]


@pytest.mark.asyncio
async def test_execute_provider_markdown_passes_downloaded_figure_metadata_to_ingestion(monkeypatch):
    tracker = _Tracker()
    provider = _Provider()
    provider.payloads = {
        "markdown-1": b"# Results\n\nNative result.",
        "meta-1": (
            b'{"display_name":"paper_image_001","figure_label":"Figure 1",'
            b'"caption_text":"Fig. 1A shows wg expression."}'
        ),
    }
    ingestion_calls = []

    async def _ingest(request, *, weaviate_client):
        ingestion_calls.append({"request": request, "weaviate_client": weaviate_client})

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=_ingest,
    )

    monkeypatch.setattr(service_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: SimpleNamespace(
            status=PdfJobStatus.COMPLETED.value,
            message=kwargs["message"],
        ),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            converted_artifact_id="markdown-1",
            curator_token="curator-token",
            source_provenance={
                "provider": "fake_provider",
                "access_scope": "global",
            },
            figure_metadata_artifact_ids=("meta-1",),
        )
    )

    assert provider.downloads[:2] == [
        {
            "artifact_id": "markdown-1",
            "request_bearer_token": "curator-token",
        },
        {
            "artifact_id": "meta-1",
            "request_bearer_token": "curator-token",
        },
    ]
    ingestion_request = ingestion_calls[0]["request"]
    assert ingestion_request.markdown == "# Results\n\nNative result."
    assert ingestion_request.provider_figure_metadata == (
        {
            "metadata_artifact_id": "meta-1",
            "display_name": "paper_image_001",
            "figure_label": "Figure 1",
            "caption_text": "Fig. 1A shows wg expression.",
        },
    )
    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED


@pytest.mark.asyncio
async def test_execute_provider_markdown_discovers_figure_metadata_from_source_provenance(monkeypatch):
    tracker = _Tracker()
    provider = _Provider()
    provider.payloads = {
        "markdown-1": b"# Results\n\nNative result.",
        "meta-discovered": (
            b'{"display_name":"paper_image_001","figure_label":"Figure 1",'
            b'"caption_text":"Fig. 1A shows wg expression."}'
        ),
    }
    provider.artifacts = [
        SourceArtifact(
            provider="fake_provider",
            artifact_id="source-pdf-1",
            role=SourceArtifactRole.SOURCE_PDF,
            artifact_format=SourceArtifactFormat.PDF,
            status=SourceArtifactStatus.AVAILABLE,
            reference_curie="AGRKB:101",
            display_name="paper",
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
            metadata={"file_class": "main", "file_extension": "pdf"},
        ),
        SourceArtifact(
            provider="fake_provider",
            artifact_id="meta-discovered",
            role=SourceArtifactRole.PROVIDER_METADATA,
            artifact_format=SourceArtifactFormat.JSON,
            status=SourceArtifactStatus.AVAILABLE,
            reference_curie="AGRKB:101",
            display_name="paper_image_001",
            parent_artifact_id="source-pdf-1",
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
            metadata={
                "file_class": "converted_main_figure_metadata",
                "file_extension": "json",
            },
        ),
    ]
    ingestion_calls = []

    async def _ingest(request, *, weaviate_client):
        ingestion_calls.append({"request": request, "weaviate_client": weaviate_client})

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=_ingest,
    )

    monkeypatch.setattr(service_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: SimpleNamespace(
            status=PdfJobStatus.COMPLETED.value,
            message=kwargs["message"],
        ),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            converted_artifact_id="markdown-1",
            curator_token="curator-token",
            source_provenance={
                "provider": "fake_provider",
                "reference_curie": "   ",
                "reference_id": "AGRKB:101",
                "pdf_artifact_id": "   ",
                "source_file_id": "source-pdf-1",
                "access_scope": "global",
            },
        )
    )

    assert provider.list_artifact_calls == [
        {"reference": "AGRKB:101", "request_bearer_token": "curator-token"}
    ]
    assert provider.downloads[:2] == [
        {
            "artifact_id": "markdown-1",
            "request_bearer_token": "curator-token",
        },
        {
            "artifact_id": "meta-discovered",
            "request_bearer_token": "curator-token",
        },
    ]
    ingestion_request = ingestion_calls[0]["request"]
    assert ingestion_request.provider_figure_metadata == (
        {
            "metadata_artifact_id": "meta-discovered",
            "display_name": "paper_image_001",
            "figure_label": "Figure 1",
            "caption_text": "Fig. 1A shows wg expression.",
        },
    )
    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED


@pytest.mark.asyncio
async def test_execute_provider_conversion_polls_then_ingests_main_markdown(monkeypatch):
    tracker = _Tracker()
    provider = _Provider()
    provider.conversion_results = [
        SourceConversionResult(
            provider="fake_provider",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:101",
            job_id="abc-job-1",
            converted_classes=("converted_merged_main",),
            per_file_progress=(
                {
                    "source": {"display_name": "paper", "file_class": "main"},
                    "converted": {
                        "display_name": "paper_nxml",
                        "file_class": "converted_merged_main",
                        "referencefile_id": 88,
                    },
                    "figures": [
                        {
                            "display_name": "paper_image_001",
                            "referencefile_id": 109,
                            "metadata_referencefile_id": "meta-bogus",
                        }
                    ],
                    "status": "success",
                    "error": None,
                },
            ),
        )
    ]
    provider.payloads = {
        "meta-valid": (
            b'{"display_name":"paper_image_001","figure_label":"Figure 1",'
            b'"caption_text":"Fig. 1A shows wg expression."}'
        ),
    }
    provider.artifacts = [
        SourceArtifact(
            provider="fake_provider",
            artifact_id="source-pdf-1",
            role=SourceArtifactRole.SOURCE_PDF,
            artifact_format=SourceArtifactFormat.PDF,
            status=SourceArtifactStatus.AVAILABLE,
            reference_curie="AGRKB:101",
            display_name="paper",
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
            metadata={"file_class": "main", "file_extension": "pdf"},
        ),
        SourceArtifact(
            provider="fake_provider",
            artifact_id="markdown-88",
            role=SourceArtifactRole.CONVERTED_TEXT,
            artifact_format=SourceArtifactFormat.MARKDOWN,
            status=SourceArtifactStatus.AVAILABLE,
            reference_curie="AGRKB:101",
            display_name="paper_nxml.md",
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
            metadata={"file_class": "converted_merged_main", "file_extension": "md"},
        ),
        SourceArtifact(
            provider="fake_provider",
            artifact_id="meta-valid",
            role=SourceArtifactRole.PROVIDER_METADATA,
            artifact_format=SourceArtifactFormat.JSON,
            status=SourceArtifactStatus.AVAILABLE,
            reference_curie="AGRKB:101",
            display_name="paper_image_001",
            parent_artifact_id="source-pdf-1",
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
            metadata={
                "file_class": "converted_main_figure_metadata",
                "file_extension": "json",
            },
        ),
    ]
    progress_updates = []
    ingested = []
    sql_selections = []

    async def _ingest(request, *, weaviate_client):
        ingested.append({"request": request, "weaviate_client": weaviate_client})

    async def _sync_selection(self, request, converted_artifact):
        sql_selections.append((request.document_id, converted_artifact.artifact_id))

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=_ingest,
    )

    monkeypatch.setattr(service_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 5)
    monkeypatch.setattr(service_module, "get_document_source_poll_interval_seconds", lambda: 0.01)
    job_statuses = [
        SimpleNamespace(status=PdfJobStatus.PENDING.value, metadata=None),
        SimpleNamespace(status=PdfJobStatus.RUNNING.value, metadata=None),
    ]

    def _get_job_by_id(**_kwargs):
        if job_statuses:
            return job_statuses.pop(0)
        return SimpleNamespace(status=PdfJobStatus.RUNNING.value, metadata=None)

    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", _get_job_by_id)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "update_progress",
        lambda **kwargs: progress_updates.append(kwargs),
    )
    completed_events = []
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: completed_events.append(kwargs),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_conversion_sql_selection",
        _sync_selection,
    )

    await service.execute_provider_conversion(
        ProviderConversionExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-conversion",
            job_id="job-conversion",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            reference="AGRKB:101",
            source_artifact_id="source-pdf-1",
            curator_token="curator-token",
            source_provenance={
                "provider": "abc_literature",
                "reference_curie": "AGRKB:101",
                "pdf_artifact_id": "source-pdf-1",
                "viewer_mode": "local_pdf",
            },
            figure_metadata_artifact_ids=("meta-valid",),
        )
    )

    assert provider.conversion_requests == [
        {
            "reference": "AGRKB:101",
            "wait": False,
            "request_bearer_token": "curator-token",
        }
    ]
    assert provider.list_artifact_calls == [
        {"reference": "AGRKB:101", "request_bearer_token": "curator-token"}
    ]
    assert sql_selections == [("doc-conversion", "markdown-88")]
    assert provider.downloads == [
        {"artifact_id": "markdown-88", "request_bearer_token": "curator-token"},
        {"artifact_id": "meta-valid", "request_bearer_token": "curator-token"},
    ]
    # The replay guard, then the check before writing that no cancel finalized the job.
    assert job_statuses == []
    assert ingested[0]["request"].source_provenance["converted_artifact_id"] == "markdown-88"
    assert ingested[0]["request"].provider_figure_metadata == (
        {
            "metadata_artifact_id": "meta-valid",
            "display_name": "paper_image_001",
            "figure_label": "Figure 1",
            "caption_text": "Fig. 1A shows wg expression.",
        },
    )
    assert progress_updates[0]["stage"] == "provider_conversion"
    assert progress_updates[0]["metadata"]["document_source"]["conversion_job_id"] == "abc-job-1"
    assert completed_events == [{"job_id": "job-conversion", "message": "Processing completed"}]


@pytest.fixture
def conversion_selection(monkeypatch):
    request = replace(
        _wait_request(), job_id=str(uuid4()), document_id=str(uuid4()), filename="private-paper.pdf",
    )
    provider = _Provider()
    provider.artifacts = [_conversion_markdown("private-markdown", request.source_artifact_id)]
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: provider,
    )
    session = Mock()
    monkeypatch.setattr(database_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", Mock(return_value=None))
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", Mock(return_value=False))
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", Mock())
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", Mock(
        return_value=SimpleNamespace(status=PdfJobStatus.FAILED.value, message="failed"),
    ))
    monkeypatch.setattr(service_module, "update_document_status", AsyncMock())
    monkeypatch.setattr(service, "_sync_provider_conversion_sql_failure", AsyncMock())
    monkeypatch.setattr(service, "_execute_provider_markdown_unbounded", AsyncMock())
    return service, request, session, provider


def _selection_result(row):
    result = Mock()
    result.scalar_one_or_none.return_value = row
    if row is None:
        result.scalar_one.side_effect = NoResultFound()
    else:
        result.scalar_one.return_value = row
    return result


@pytest.mark.asyncio
async def test_conversion_selection_commits_before_dispatch(conversion_selection):
    service, request, session, provider = conversion_selection
    main_file = SimpleNamespace()
    session.execute.side_effect = [
        _selection_result(SimpleNamespace(status=PdfJobStatus.RUNNING.value)),
        _selection_result(SimpleNamespace(main_file=main_file)),
    ]

    async def ingest(markdown_request, *, skip_replay_guard):
        session.commit.assert_called_once_with()
        session.close.assert_called_once_with()
        assert skip_replay_guard is True
        assert main_file.source_provider_converted_artifact_id == markdown_request.converted_artifact_id
        assert markdown_request.converted_artifact_id == provider.artifacts[0].artifact_id

    service._execute_provider_markdown_unbounded.side_effect = ingest
    await service._execute_provider_conversion_unbounded(request)

    service._execute_provider_markdown_unbounded.assert_awaited_once()
    service_module.pdf_job_service.mark_failed.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["lock", "read", "commit", "reporter", "missing_job", "missing_document"])
async def test_conversion_selection_failure_aborts_ingestion(conversion_selection, monkeypatch, failure):
    service, request, session, provider = conversion_selection
    sql = "SELECT private_selection_column"
    db_error = OperationalError(sql, (request.document_id, request.filename), Exception("private-db-error"))
    job = None if failure == "missing_job" else SimpleNamespace(status=PdfJobStatus.RUNNING.value)
    document = None if failure == "missing_document" else SimpleNamespace(main_file=SimpleNamespace())
    session.execute.side_effect = [
        db_error if failure == "lock" else _selection_result(job),
        db_error if failure == "read" else _selection_result(document),
    ]
    if failure in {"commit", "reporter"}:
        session.commit.side_effect = db_error

    reporter = Mock(wraps=service._report_execution_failure)
    if failure == "reporter":
        reporter.side_effect = RuntimeError("private-reporter-error")
    monkeypatch.setattr(service, "_report_execution_failure", reporter)
    monkeypatch.setenv("SENTRY_CONTENT_REDACTION_ENABLED", "false")
    events = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            events.extend(item.get_event() for item in envelope.items if item.headers["type"] == "event")

    with sentry_sdk.Client(
        dsn="http://public@example.invalid/1", transport=MemoryTransport,
        before_send=sentry.before_send, include_local_variables=False, default_integrations=False,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    ) as client, sentry_sdk.isolation_scope() as scope:
        scope.set_client(client)
        await service._execute_provider_conversion_unbounded(request)
        client.flush()

    service._execute_provider_markdown_unbounded.assert_not_awaited()
    assert provider.downloads == []
    if failure in {"commit", "reporter"}:
        session.commit.assert_called_once_with()
    else:
        session.commit.assert_not_called()
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()
    reporter.assert_called_once()
    error, = reporter.call_args.args
    message = "Provider conversion selection could not be persisted"
    assert str(error) == message
    assert error.__context__ is None and error.__cause__ is None
    service_module.update_document_status.assert_awaited_once_with(request.document_id, request.user_id, "failed")
    service._sync_provider_conversion_sql_failure.assert_awaited_once_with(request, error)
    service_module.pdf_job_service.mark_failed.assert_called_once_with(
        job_id=request.job_id, message=message, stage=ProcessingStage.FAILED.value,
    )
    if failure == "reporter":
        assert events == []
    else:
        event, = events
        assert event["tags"]["failure_stage"] == "provider_conversion"
        assert [exc["value"] for exc in event["exception"]["values"]] == [message]
        for private in (sql, request.document_id, request.job_id, request.filename,
                        request.curator_token, provider.artifacts[0].artifact_id, "private-db-error"):
            assert private not in repr(event)


@pytest.mark.asyncio
async def test_conversion_selection_cancelled_under_lock_stops_quietly(conversion_selection, monkeypatch):
    service, request, session, provider = conversion_selection
    session.execute.return_value = _selection_result(SimpleNamespace(status=PdfJobStatus.CANCELLED.value))
    reporter = Mock()
    monkeypatch.setattr(service, "_report_execution_failure", reporter)

    await service._execute_provider_conversion_unbounded(request)

    session.execute.assert_called_once()
    assert "FOR UPDATE" in str(session.execute.call_args.args[0])
    session.commit.assert_not_called()
    session.close.assert_called_once_with()
    service._execute_provider_markdown_unbounded.assert_not_awaited()
    assert provider.downloads == []
    service_module.pdf_job_service.mark_failed.assert_not_called()
    service_module.update_document_status.assert_not_awaited()
    service._sync_provider_conversion_sql_failure.assert_not_awaited()
    reporter.assert_not_called()


@pytest.mark.asyncio
async def test_execute_provider_conversion_falls_back_to_local_pdf_when_main_markdown_is_denied(
    monkeypatch,
    tmp_path,
):
    tracker = _Tracker()
    provider = _Provider()
    provider.conversion_results = [
        SourceConversionResult(
            provider="fake_provider",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:101",
            converted_classes=("converted_merged_main",),
        )
    ]
    provider.artifacts = [
        SourceArtifact(
            provider="fake_provider",
            artifact_id="markdown-88",
            role=SourceArtifactRole.CONVERTED_TEXT,
            artifact_format=SourceArtifactFormat.MARKDOWN,
            status=SourceArtifactStatus.AVAILABLE,
            reference_curie="AGRKB:101",
            display_name="paper_nxml.md",
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
            metadata={"file_class": "converted_merged_main", "file_extension": "md"},
        )
    ]
    provider.download_errors["markdown-88"] = DocumentSourceAccessDenied(
        "Document-source artifact access was denied"
    )
    progress_updates = []
    sql_selections = []
    source_import_failures = []
    orchestrator_calls = []
    completed_events = []
    failed_events = []

    class _RecordingOrchestrator:
        async def process_pdf_document(self, **kwargs) -> ProcessingResult:
            orchestrator_calls.append(kwargs)
            return _pipeline_result(success=True, document_id=kwargs["document_id"])

    async def _sync_selection(self, request, converted_artifact):
        sql_selections.append((request.document_id, converted_artifact.artifact_id))

    async def _sync_source_import_failure(self, request):
        source_import_failures.append((request.document_id, request.owner_user_id))

    service = UploadExecutionService(
        pipeline_tracker=cast(PipelineTracker, tracker),
        orchestrator_factory=lambda _connection, _tracker: _RecordingOrchestrator(),
        document_source_provider_factory=lambda: cast(DocumentSourceProvider, provider),
    )

    monkeypatch.setattr(service_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 5)
    monkeypatch.setattr(service_module, "get_document_source_poll_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "update_progress",
        lambda **kwargs: progress_updates.append(kwargs),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: completed_events.append(kwargs)
        or SimpleNamespace(
            status=PdfJobStatus.COMPLETED.value,
            message=kwargs["message"],
        ),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs),
    )
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_conversion_sql_selection",
        _sync_selection,
    )
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_source_import_failure",
        _sync_source_import_failure,
    )

    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"%PDF-1.7 local upload")
    await service.execute_provider_conversion(
        ProviderConversionExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-conversion",
            job_id="job-conversion",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=file_path,
            reference="AGRKB:101",
            source_artifact_id="source-pdf-1",
            curator_token="curator-token",
            source_provenance={
                "provider": "abc_literature",
                "reference_curie": "AGRKB:101",
                "pdf_artifact_id": "source-pdf-1",
            },
        )
    )

    assert provider.conversion_requests == [
        {
            "reference": "AGRKB:101",
            "wait": False,
            "request_bearer_token": "curator-token",
        }
    ]
    assert provider.list_artifact_calls == [
        {"reference": "AGRKB:101", "request_bearer_token": "curator-token"}
    ]
    assert provider.downloads == [
        {"artifact_id": "markdown-88", "request_bearer_token": "curator-token"}
    ]
    assert sql_selections == [("doc-conversion", "markdown-88")]
    assert source_import_failures == [("doc-conversion", 42)]
    assert len(orchestrator_calls) == 1
    assert orchestrator_calls[0]["file_path"] == file_path
    assert orchestrator_calls[0]["document_id"] == "doc-conversion"
    assert completed_events == [
        {"job_id": "job-conversion", "message": "Processing completed"}
    ]
    assert failed_events == []
    assert progress_updates[-2]["metadata"] == {
        "document_source": {
            "converted_markdown_status": "access_denied",
            "text_source": "local_pdf",
        }
    }
    assert tracker.calls[-1]["stage"] is ProcessingStage.COMPLETED
    assert provider.closed is True


@pytest.mark.asyncio
async def test_execute_provider_markdown_does_not_fallback_for_denied_figure_metadata(
    monkeypatch,
    tmp_path,
):
    tracker = _Tracker()
    provider = _Provider()
    provider.payloads = {"markdown-1": b"# Results\n\nNative result."}
    provider.download_errors = {
        "meta-bad": DocumentSourceAccessDenied("sidecar access denied")
    }
    ingestion_calls = []
    status_updates = []

    async def _ingest(request, *, weaviate_client):
        ingestion_calls.append({"request": request, "weaviate_client": weaviate_client})

    async def _update_document_status(document_id, user_id, status):
        status_updates.append(
            {
                "document_id": document_id,
                "user_id": user_id,
                "status": status,
            }
        )

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda *_args: _RaisingOrchestrator(
            AssertionError("local PDF fallback should not run for a denied sidecar")
        ),
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=_ingest,
    )

    monkeypatch.setattr(service_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)
    completed_events = []
    failed_events = []
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: completed_events.append(kwargs),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs)
        or SimpleNamespace(
            status=PdfJobStatus.FAILED.value,
            message=kwargs["message"],
        ),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)
    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"%PDF-1.7 local upload")

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            converted_artifact_id="markdown-1",
            curator_token="curator-token",
            source_provenance={"provider": "fake_provider"},
            figure_metadata_artifact_ids=("meta-bad",),
            file_path=file_path,
        )
    )

    assert provider.downloads == [
        {"artifact_id": "markdown-1", "request_bearer_token": "curator-token"},
        {"artifact_id": "meta-bad", "request_bearer_token": "curator-token"},
    ]
    assert ingestion_calls == []
    assert completed_events == []
    assert failed_events == [
        {
            "job_id": "job-provider",
            "message": "sidecar access denied",
            "stage": ProcessingStage.FAILED.value,
        }
    ]
    assert status_updates == [
        {
            "document_id": "doc-provider",
            "user_id": "user-provider",
            "status": ProcessingStatus.FAILED.value,
        }
    ]


def test_select_preferred_main_text_rejects_abc_tei_only_artifact():
    artifact = SourceArtifact(
        provider="abc_literature",
        artifact_id="tei-markdown-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_curie="AGRKB:101",
        display_name="paper_tei.md",
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
        metadata={"file_class": "converted_merged_main", "file_extension": "md"},
    )

    selected, ambiguous_count = select_preferred_main_text_artifact(
        _Provider(),
        [artifact],
    )

    assert selected is None
    assert ambiguous_count == 0


def test_select_preferred_main_text_reports_ambiguous_equal_candidates():
    artifacts = [
        SourceArtifact(
            provider="abc_literature",
            artifact_id=artifact_id,
            role=SourceArtifactRole.CONVERTED_TEXT,
            artifact_format=SourceArtifactFormat.MARKDOWN,
            status=SourceArtifactStatus.AVAILABLE,
            reference_curie="AGRKB:101",
            display_name=display_name,
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
            metadata={"file_class": "converted_merged_main", "file_extension": "md"},
        )
        for artifact_id, display_name in (
            ("nxml-markdown-1", "paper_a_nxml.md"),
            ("nxml-markdown-2", "paper_b_nxml.md"),
        )
    ]

    selected, ambiguous_count = select_preferred_main_text_artifact(
        _Provider(),
        artifacts,
    )

    assert selected is None
    assert ambiguous_count == 2


@pytest.mark.asyncio
async def test_upload_selection_accepts_provider_mapped_readiness():
    provider = _Provider()
    artifact = SourceArtifact(
        provider="abc_literature",
        artifact_id="markdown-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_curie="AGRKB:101",
        metadata={"file_class": "converted_merged_main"},
    )
    provider.artifacts = [artifact]
    service = UploadExecutionService(pipeline_tracker=_Tracker())

    selected, metadata_ids = await service._select_converted_main_artifact(
        provider=provider,
        request=ProviderConversionExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-1",
            job_id="job-1",
            user_id="user-1",
            owner_user_id=1,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            reference="AGRKB:101",
            source_artifact_id="pdf-1",
            curator_token="token",
            source_provenance={"provider": "abc_literature"},
        ),
        curator_token="token",
    )

    assert selected is artifact
    assert metadata_ids == ()


def _conversion_markdown(artifact_id, parent=None):
    return SourceArtifact(
        provider="abc_literature",
        artifact_id=artifact_id,
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_curie="AGRKB:101",
        parent_artifact_id=parent,
        display_name=f"{artifact_id}_nxml.md",
        metadata={"file_class": "converted_merged_main"},
    )


def _conversion_request(bearer_kind):
    return ProviderConversionExecutionRequest(
        bearer_kind=bearer_kind,
        document_id="doc-1",
        job_id="job-1",
        user_id="user-1",
        owner_user_id=1,
        filename="paper.pdf",
        file_path=Path("/tmp/paper.pdf"),
        reference="AGRKB:101",
        source_artifact_id="pdf-1",
        curator_token="token",
        source_provenance={"provider": "abc_literature"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bearer_kind", "artifacts", "expected"),
    [
        # A machine reader accepts only text bound to the authorized source PDF.
        (
            ProviderBearerKind.SERVICE,
            [_conversion_markdown("reference-level"), _conversion_markdown("other-pdf", "pdf-2"),
             _conversion_markdown("bound", "pdf-1")],
            "bound",
        ),
        (
            ProviderBearerKind.SERVICE,
            [_conversion_markdown("reference-level"), _conversion_markdown("other-pdf", "pdf-2")],
            None,
        ),
        # A curator bearer keeps reference-level selection.
        (ProviderBearerKind.CURATOR, [_conversion_markdown("reference-level")], "reference-level"),
    ],
)
async def test_conversion_polling_binds_text_to_the_source_pdf_for_service_bearers(
    bearer_kind, artifacts, expected
):
    provider = _Provider()
    provider.artifacts = artifacts
    service = UploadExecutionService(pipeline_tracker=_Tracker())

    selected, _ = await service._select_converted_main_artifact(
        provider=provider,
        request=_conversion_request(bearer_kind),
        curator_token="token",
    )

    assert (selected.artifact_id if selected else None) == expected


@pytest.mark.asyncio
async def test_upload_selection_uses_non_abc_provider_declared_main_markdown():
    provider = _Provider(provider_id="other_provider", main_file_class="canonical_article")
    canonical = SourceArtifact(
        provider="other_provider",
        artifact_id="canonical-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="other:101",
        metadata={"file_class": "canonical_article", "provider_rank": 0},
    )
    provider.artifacts = [
        SourceArtifact(
            provider="other_provider",
            artifact_id="abc-named-1",
            role=SourceArtifactRole.CONVERTED_TEXT,
            artifact_format=SourceArtifactFormat.MARKDOWN,
            status=SourceArtifactStatus.AVAILABLE,
            reference_id="other:101",
            metadata={"file_class": "converted_merged_main", "provider_rank": 10},
        ),
        canonical,
    ]
    service = UploadExecutionService(pipeline_tracker=_Tracker())

    selected, _ = await service._select_converted_main_artifact(
        provider=provider,
        request=ProviderConversionExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-1",
            job_id="job-1",
            user_id="user-1",
            owner_user_id=1,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            reference="other:101",
            source_artifact_id="pdf-1",
            curator_token="token",
            source_provenance={"provider": "other_provider"},
        ),
        curator_token="token",
    )

    assert selected is canonical


def test_upload_selection_rejects_explicit_unknown_artifact():
    artifact = SourceArtifact(
        provider="abc_literature",
        artifact_id="unknown-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.UNKNOWN,
        metadata={"file_class": "converted_merged_main"},
    )

    assert select_preferred_main_text_artifact(_Provider(), [artifact]) == (None, 0)


@pytest.mark.asyncio
async def test_execute_provider_conversion_fails_when_completed_without_canonical_markdown(monkeypatch):
    tracker = _Tracker()
    provider = _Provider()
    provider.conversion_results = [
        SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.CONVERTED,
            reference_curie="AGRKB:101",
            job_id="abc-job-1",
            converted_classes=("converted_merged_main",),
        )
    ]
    provider.artifacts = [
        SourceArtifact(
            provider="abc_literature",
            artifact_id="tei-markdown-1",
            role=SourceArtifactRole.CONVERTED_TEXT,
            artifact_format=SourceArtifactFormat.MARKDOWN,
            status=SourceArtifactStatus.AVAILABLE,
            reference_curie="AGRKB:101",
            display_name="paper_tei.md",
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
            metadata={"file_class": "converted_merged_main", "file_extension": "md"},
        )
    ]
    progress_updates = []
    failed_events = []
    status_updates = []
    sql_failures = []

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    async def _sync_sql_failure(self, request, exc):
        sql_failures.append((request.document_id, str(exc)))

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider,
    )

    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 5)
    monkeypatch.setattr(service_module, "get_document_source_poll_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
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
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_conversion_sql_failure",
        _sync_sql_failure,
    )

    await service.execute_provider_conversion(
        ProviderConversionExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-conversion",
            job_id="job-conversion",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            reference="AGRKB:101",
            source_artifact_id="source-pdf-1",
            curator_token="curator-token",
            source_provenance={"provider": "abc_literature"},
        )
    )

    assert failed_events == [
        {
            "job_id": "job-conversion",
            "message": "Provider conversion completed without canonical converted Markdown",
            "stage": ProcessingStage.FAILED.value,
        }
    ]
    assert status_updates == [("doc-conversion", "user-provider", "failed")]
    assert sql_failures == [
        ("doc-conversion", "Provider conversion completed without canonical converted Markdown")
    ]
    assert progress_updates[0]["metadata"]["document_source"]["conversion_status"] == "converted"


@pytest.mark.asyncio
async def test_execute_provider_conversion_marks_failed_when_provider_fails(monkeypatch):
    tracker = _Tracker()
    provider = _Provider()
    provider.conversion_results = [
        SourceConversionResult(
            provider="fake_provider",
            status=SourceConversionStatus.FAILED,
            reference_curie="AGRKB:101",
            job_id="abc-job-1",
            error_message="pdfx 500",
            per_file_progress=(
                {
                    "source": {"display_name": "paper", "file_class": "main"},
                    "converted": None,
                    "status": "failed",
                    "error": "pdfx 500",
                },
            ),
        )
    ]
    progress_updates = []
    failed_events = []
    status_updates = []
    sql_failures = []

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    async def _sync_sql_failure(self, request, exc):
        sql_failures.append((request.document_id, str(exc)))

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider,
    )

    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 5)
    monkeypatch.setattr(service_module, "get_document_source_poll_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
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
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_conversion_sql_failure",
        _sync_sql_failure,
    )

    await service.execute_provider_conversion(
        ProviderConversionExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-conversion",
            job_id="job-conversion",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            reference="AGRKB:101",
            source_artifact_id="source-pdf-1",
            curator_token="curator-token",
            source_provenance={"provider": "abc_literature"},
        )
    )

    assert failed_events == [
        {
            "job_id": "job-conversion",
            "message": "pdfx 500",
            "stage": ProcessingStage.FAILED.value,
        }
    ]
    assert status_updates == [("doc-conversion", "user-provider", "failed")]
    assert sql_failures == [("doc-conversion", "pdfx 500")]
    assert progress_updates[-1]["metadata"]["document_source"]["per_file_progress"][0]["error"] == "pdfx 500"
    assert provider.closed is True


@pytest.mark.asyncio
async def test_execute_provider_conversion_marks_failed_when_provider_has_no_sources(monkeypatch):
    tracker = _Tracker()
    provider = _Provider()
    provider.conversion_results = [
        SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.NO_SOURCES,
            reference_curie="AGRKB:101",
            job_id="abc-job-1",
        )
    ]
    progress_updates = []
    failed_events = []
    status_updates = []
    sql_failures = []

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    async def _sync_sql_failure(self, request, exc):
        sql_failures.append((request.document_id, str(exc)))

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider,
    )

    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 5)
    monkeypatch.setattr(service_module, "get_document_source_poll_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
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
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_conversion_sql_failure",
        _sync_sql_failure,
    )

    await service.execute_provider_conversion(
        ProviderConversionExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-conversion",
            job_id="job-conversion",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            reference="AGRKB:101",
            source_artifact_id="source-pdf-1",
            curator_token="curator-token",
            source_provenance={"provider": "abc_literature"},
        )
    )

    assert failed_events == [
        {
            "job_id": "job-conversion",
            "message": "Provider conversion found no convertible source files",
            "stage": ProcessingStage.FAILED.value,
        }
    ]
    assert status_updates == [("doc-conversion", "user-provider", "failed")]
    assert sql_failures == [
        ("doc-conversion", "Provider conversion found no convertible source files")
    ]
    assert progress_updates[-1]["metadata"]["document_source"]["conversion_status"] == "no_sources"
    assert provider.closed is True


@pytest.mark.asyncio
async def test_execute_provider_conversion_times_out_and_syncs_sql_failure(monkeypatch):
    tracker = _Tracker()
    failed_events = []
    status_updates = []
    sql_failures = []

    async def _slow_conversion(self, request):
        del self, request
        await asyncio.sleep(10)

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    async def _sync_sql_failure(self, request, exc):
        sql_failures.append((request.document_id, str(exc)))

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: _Provider(),
    )

    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 0.01)
    monkeypatch.setattr(
        service_module,
        "get_document_source_provider_metadata",
        lambda provider_id: (
            {"display_label": "ABC Literature", "short_label": "ABC"}
            if provider_id == "abc_literature"
            else None
        ),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(
        UploadExecutionService,
        "_execute_provider_conversion_unbounded",
        _slow_conversion,
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs),
    )
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_conversion_sql_failure",
        _sync_sql_failure,
    )

    await service.execute_provider_conversion(
        ProviderConversionExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-conversion",
            job_id="job-conversion",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            reference="AGRKB:101",
            source_artifact_id="source-pdf-1",
            curator_token="curator-token",
            source_provenance={"provider": "abc_literature"},
        )
    )

    expected_timeout = (
        "ABC had not converted this paper 1 second after the import started, "
        "so the import stopped. The time includes any wait behind other imports."
    )
    assert status_updates == [("doc-conversion", "user-provider", "failed")]
    assert sql_failures == [("doc-conversion", expected_timeout)]
    assert failed_events == [
        {
            "job_id": "job-conversion",
            "message": expected_timeout,
            "stage": ProcessingStage.FAILED.value,
        }
    ]


@pytest.mark.asyncio
async def test_execute_provider_markdown_times_out_and_marks_failed(monkeypatch):
    tracker = _Tracker()
    provider = _Provider()
    failed_events = []
    status_updates = []
    sql_failures = []

    async def _ingest(_request, *, weaviate_client):
        del weaviate_client
        await asyncio.sleep(10)

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    async def _sync_sql_failure(self, request, exc):
        sql_failures.append((request.document_id, str(exc)))

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=_ingest,
    )

    monkeypatch.setattr(service_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 0.01)
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs),
    )
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_markdown_sql_failure",
        _sync_sql_failure,
    )

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            converted_artifact_id="markdown-1",
            curator_token="curator-token",
            source_provenance={
                "provider": "fake_provider",
                "access_scope": "global",
            },
        )
    )

    assert provider.closed is True
    assert status_updates == [("doc-provider", "user-provider", "failed")]
    assert sql_failures == [
        ("doc-provider", "Provider Markdown import exceeded 0.01 seconds")
    ]
    assert failed_events == [
        {
            "job_id": "job-provider",
            "message": "Provider Markdown import exceeded 0.01 seconds",
            "stage": ProcessingStage.FAILED.value,
        }
    ]


@pytest.mark.asyncio
async def test_execute_provider_markdown_rejects_blank_curator_token_before_download(monkeypatch):
    tracker = _Tracker()
    provider_calls = []
    failed_events = []
    sql_failures = []

    async def _sync_sql_failure(self, request, exc):
        sql_failures.append((request.document_id, str(exc)))

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider_calls.append("provider") or _Provider(),
        provider_markdown_ingestion_fn=lambda *_args, **_kwargs: None,
    )

    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs),
    )
    monkeypatch.setattr(service_module, "update_document_status", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_markdown_sql_failure",
        _sync_sql_failure,
    )

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            converted_artifact_id="markdown-1",
            curator_token="  ",
            source_provenance={
                "provider": "fake_provider",
                "access_scope": "global",
            },
        )
    )

    assert provider_calls == []
    assert failed_events[0]["message"] == "Provider Markdown download requires a request curator token"
    assert sql_failures == [
        ("doc-provider", "Provider Markdown download requires a request curator token")
    ]


@pytest.mark.asyncio
async def test_execute_provider_markdown_syncs_sql_failure_when_download_fails(monkeypatch):
    tracker = _Tracker()
    provider = _RaisingProvider()
    failed_events = []
    sql_failures = []

    async def _sync_sql_failure(self, request, exc):
        sql_failures.append((request.document_id, str(exc)))

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=lambda *_args, **_kwargs: None,
    )

    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs)
        or SimpleNamespace(
            status=PdfJobStatus.FAILED.value,
            message=kwargs["message"],
        ),
    )
    monkeypatch.setattr(service_module, "update_document_status", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_markdown_sql_failure",
        _sync_sql_failure,
    )

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            converted_artifact_id="markdown-1",
            curator_token="curator-token",
            source_provenance={
                "provider": "fake_provider",
                "access_scope": "global",
            },
        )
    )

    assert provider.downloads == [
        {
            "artifact_id": "markdown-1",
            "request_bearer_token": "curator-token",
        }
    ]
    assert provider.closed is True
    assert failed_events[0]["message"] == "provider unavailable"
    assert sql_failures == [("doc-provider", "provider unavailable")]
    assert tracker.calls[-1]["stage"] == ProcessingStage.FAILED


@pytest.mark.asyncio
async def test_provider_access_denial_marks_failed_when_saved_pdf_is_missing(
    monkeypatch,
    tmp_path,
):
    service = UploadExecutionService(
        pipeline_tracker=cast(PipelineTracker, _Tracker())
    )
    failure_reports = []
    status_updates = []
    sql_failures = []
    terminal_failures = []

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    async def _sync_sql_failure(request, exc):
        sql_failures.append((request.document_id, str(exc)))

    async def _mark_failed(request, *, message, stage):
        terminal_failures.append((request.document_id, message, stage))

    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)
    monkeypatch.setattr(service, "_sync_provider_markdown_sql_failure", _sync_sql_failure)
    monkeypatch.setattr(service, "_mark_failed_and_sync_tracker", _mark_failed)
    monkeypatch.setattr(
        service,
        "_report_execution_failure",
        lambda exc, **kwargs: failure_reports.append((str(exc), kwargs)),
    )

    access_denied = DocumentSourceAccessDenied("provider access denied")
    request = ProviderMarkdownExecutionRequest(
        bearer_kind=ProviderBearerKind.CURATOR,
        document_id="doc-provider",
        job_id="job-provider",
        user_id="user-provider",
        owner_user_id=42,
        filename="paper.pdf",
        file_path=tmp_path / "missing.pdf",
        converted_artifact_id="markdown-1",
        curator_token="curator-token",
        source_provenance={"provider": "fake_provider"},
    )

    await service._continue_with_local_pdf_after_provider_access_denied(
        request,
        access_denied,
        task_name="provider_markdown_upload",
    )

    assert failure_reports[0][0] == "provider access denied"
    assert failure_reports[0][1]["failure_stage"] == "provider_markdown_access"
    assert status_updates == [("doc-provider", "user-provider", "failed")]
    assert sql_failures == [("doc-provider", "provider access denied")]
    assert terminal_failures == [
        ("doc-provider", "provider access denied", ProcessingStage.FAILED.value)
    ]


@pytest.mark.asyncio
async def test_sync_provider_source_import_failure_updates_owned_document(monkeypatch):
    main_file = SimpleNamespace(source_import_status="pending", source_converted_artifact_sha256="a" * 64)
    document = SimpleNamespace(main_file=main_file)
    session = SimpleNamespace(
        statement=None,
        commit_calls=0,
        rollback_calls=0,
        close_calls=0,
    )

    def _execute(statement):
        session.statement = statement
        return SimpleNamespace(scalar_one_or_none=lambda: document)

    session.execute = _execute
    session.commit = lambda: setattr(session, "commit_calls", session.commit_calls + 1)
    session.rollback = lambda: setattr(session, "rollback_calls", session.rollback_calls + 1)
    session.close = lambda: setattr(session, "close_calls", session.close_calls + 1)
    monkeypatch.setattr(database_module, "SessionLocal", lambda: session)

    service = UploadExecutionService(pipeline_tracker=cast(PipelineTracker, _Tracker()))
    request = ProviderMarkdownExecutionRequest(
        bearer_kind=ProviderBearerKind.CURATOR,
        document_id="11111111-1111-1111-1111-111111111111",
        job_id="job-provider",
        user_id="user-provider",
        owner_user_id=42,
        filename="paper.pdf",
        converted_artifact_id="markdown-1",
        curator_token="curator-token",
        source_provenance={"provider": "fake_provider"},
        file_path=Path("/tmp/paper.pdf"),
    )

    await service._sync_provider_source_import_failure(request)

    compiled = session.statement.compile()
    assert "pdf_documents.id" in str(session.statement.whereclause)
    assert "pdf_documents.user_id" in str(session.statement.whereclause)
    assert set(compiled.params.values()) == {
        request.owner_user_id,
        UUID(request.document_id),
    }
    assert main_file.source_import_status == "failed"
    assert main_file.source_converted_artifact_sha256 is None
    assert session.commit_calls == 1
    assert session.rollback_calls == 0
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_sync_provider_source_import_failure_rejects_missing_or_other_owner_document(
    monkeypatch,
):
    session = SimpleNamespace(commit_calls=0, rollback_calls=0, close_calls=0)
    session.execute = lambda _statement: SimpleNamespace(scalar_one_or_none=lambda: None)
    session.commit = lambda: setattr(session, "commit_calls", session.commit_calls + 1)
    session.rollback = lambda: setattr(session, "rollback_calls", session.rollback_calls + 1)
    session.close = lambda: setattr(session, "close_calls", session.close_calls + 1)
    monkeypatch.setattr(database_module, "SessionLocal", lambda: session)

    service = UploadExecutionService(pipeline_tracker=cast(PipelineTracker, _Tracker()))
    request = ProviderConversionExecutionRequest(
        bearer_kind=ProviderBearerKind.CURATOR,
        document_id="11111111-1111-1111-1111-111111111111",
        job_id="job-provider",
        user_id="user-provider",
        owner_user_id=42,
        filename="paper.pdf",
        reference="AGRKB:101",
        source_artifact_id="source-pdf-1",
        curator_token="curator-token",
        source_provenance={"provider": "fake_provider"},
        file_path=Path("/tmp/paper.pdf"),
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot persist provider source-import failure for missing document",
    ):
        await service._sync_provider_source_import_failure(request)

    assert session.commit_calls == 0
    assert session.rollback_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_provider_markdown_invalid_utf8_never_reaches_ingestion(monkeypatch):
    provider = _Provider(payload=b"\xffinvalid UTF-8")
    ingest = AsyncMock()
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=ingest,
    )
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **kwargs: None)
    monkeypatch.setattr(service_module, "update_document_status", AsyncMock())
    monkeypatch.setattr(service, "_report_execution_failure", Mock())
    monkeypatch.setattr(service, "_sync_provider_markdown_sql_failure", AsyncMock())
    failed = AsyncMock()
    monkeypatch.setattr(service, "_mark_failed_and_sync_tracker", failed)
    await service._execute_provider_markdown_unbounded(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider", job_id="job-provider", user_id="user-provider",
            owner_user_id=42, filename="paper.pdf", converted_artifact_id="markdown-1",
            curator_token="curator-token", source_provenance={"provider": "fake_provider"},
            file_path=Path("/tmp/paper.pdf"),
        )
    )
    ingest.assert_not_awaited()
    failed.assert_awaited_once()
    assert provider.closed is True


@pytest.mark.asyncio
async def test_execute_provider_markdown_falls_back_to_local_pdf_on_access_denied(
    monkeypatch,
    tmp_path,
):
    tracker = _Tracker()
    provider = _Provider()
    provider.download_errors["markdown-1"] = DocumentSourceAccessDenied(
        "Document-source artifact access was denied"
    )
    completed_events = []
    failed_events = []
    source_import_failures = []
    ingest = AsyncMock()

    async def _sync_source_import_failure(self, request):
        source_import_failures.append(request.document_id)

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _Orchestrator(
            _pipeline_result(success=True, document_id="doc-provider")
        ),
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=ingest,
    )

    monkeypatch.setattr(service_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: completed_events.append(kwargs)
        or SimpleNamespace(status=PdfJobStatus.COMPLETED.value, message=kwargs["message"], metadata=None),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs)
        or SimpleNamespace(status=PdfJobStatus.FAILED.value, message=kwargs["message"], metadata=None),
    )
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_source_import_failure",
        _sync_source_import_failure,
    )

    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"%PDF-1.7 local upload")
    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            converted_artifact_id="markdown-1",
            curator_token="curator-token",
            source_provenance={
                "provider": "abc_literature",
                "access_scope": "global",
            },
            file_path=file_path,
        )
    )

    assert source_import_failures == ["doc-provider"]
    ingest.assert_not_awaited()
    assert completed_events[-1] == {
        "job_id": "job-provider",
        "message": "Processing completed",
    }
    assert failed_events == []
    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED
    assert provider.closed is True


@pytest.mark.asyncio
async def test_execute_provider_markdown_fails_when_local_pdf_source_state_cannot_persist(
    monkeypatch,
    tmp_path,
):
    tracker = _Tracker()
    provider = _Provider()
    provider.download_errors["markdown-1"] = DocumentSourceAccessDenied(
        "Document-source artifact access was denied"
    )
    orchestrator_calls = []
    failed_events = []

    class _RecordingOrchestrator:
        async def process_pdf_document(self, **_kwargs) -> ProcessingResult:
            orchestrator_calls.append("called")
            return _pipeline_result(success=True, document_id="doc-provider")

    async def _raise_source_import_failure(self, request):
        raise RuntimeError("database unavailable")

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _RecordingOrchestrator(),
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=lambda *_args, **_kwargs: None,
    )

    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs)
        or SimpleNamespace(
            status=PdfJobStatus.FAILED.value,
            message=kwargs["message"],
        ),
    )
    monkeypatch.setattr(
        UploadExecutionService,
        "_sync_provider_source_import_failure",
        _raise_source_import_failure,
    )
    monkeypatch.setattr(
        service_module,
        "update_document_status",
        lambda *_args, **_kwargs: _async_value(None),
    )
    monkeypatch.setattr(
        service_module,
        "report_background_task_exception",
        lambda *_args, **_kwargs: None,
    )

    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"%PDF-1.7 local upload")
    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            converted_artifact_id="markdown-1",
            curator_token="curator-token",
            source_provenance={
                "provider": "abc_literature",
                "access_scope": "global",
            },
            file_path=file_path,
        )
    )

    assert orchestrator_calls == []
    assert failed_events == [
        {
            "job_id": "job-provider",
            "message": "Provider source-import state could not be persisted",
            "stage": ProcessingStage.FAILED.value,
        }
    ]
    assert tracker.calls[-1]["stage"] == ProcessingStage.FAILED


@pytest.mark.asyncio
async def test_execute_provider_markdown_marks_cancelled_when_cancel_requested_after_download(monkeypatch):
    tracker = _Tracker()
    provider = _Provider()
    cancel_checks = {"count": 0}
    cancelled_events = []
    failed_events = []
    ingestion_calls = []

    def _is_cancel_requested(**_kwargs):
        cancel_checks["count"] += 1
        return cancel_checks["count"] >= 2

    async def _ingest(*_args, **_kwargs):
        ingestion_calls.append("ingest")

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=_ingest,
    )

    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", _is_cancel_requested)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_cancelled",
        lambda **kwargs: cancelled_events.append(kwargs),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs),
    )
    monkeypatch.setattr(service_module, "update_document_status", lambda *_args, **_kwargs: _async_value(None))

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            converted_artifact_id="markdown-1",
            curator_token="curator-token",
            source_provenance={
                "provider": "fake_provider",
                "access_scope": "global",
            },
        )
    )

    assert ingestion_calls == []
    assert failed_events == []
    assert cancelled_events == [
        {
            "job_id": "job-provider",
            "reason": "Processing cancelled by user request",
        }
    ]


@pytest.mark.asyncio
async def test_execute_provider_markdown_tracker_completed_failure_does_not_fail_job(monkeypatch):
    tracker = _RaisingTracker(RuntimeError("tracker unavailable"))
    provider = _Provider()
    completed_events = []
    failed_events = []

    async def _ingest(*_args, **_kwargs):
        return None

    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=_ingest,
    )

    monkeypatch.setattr(service_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: completed_events.append(kwargs),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs),
    )

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            converted_artifact_id="markdown-1",
            curator_token="curator-token",
            source_provenance={
                "provider": "fake_provider",
                "access_scope": "global",
            },
        )
    )

    assert completed_events == [
        {
            "job_id": "job-provider",
            "message": "Processing completed",
        }
    ]
    assert failed_events == []


@pytest.mark.asyncio
async def test_terminal_tracker_uses_durable_first_terminal_winner(monkeypatch):
    tracker = _Tracker()
    service = UploadExecutionService(pipeline_tracker=tracker)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **_kwargs: SimpleNamespace(
            status=PdfJobStatus.FAILED.value,
            message="Earlier failure won",
        ),
    )

    await service._mark_completed_and_sync_tracker(
        UploadExecutionRequest(
            document_id="doc-first-terminal",
            job_id="job-first-terminal",
            user_id="user-provider",
            file_path=Path("/tmp/not-used.pdf"),
        ),
        message="Processing completed",
    )

    assert tracker.calls[-1]["stage"] == ProcessingStage.FAILED
    assert tracker.calls[-1]["message"] == "Earlier failure won"


@pytest.mark.asyncio
async def test_terminal_tracker_does_not_invent_state_when_durable_job_is_missing(
    monkeypatch,
    caplog,
):
    tracker = _Tracker()
    service = UploadExecutionService(pipeline_tracker=tracker)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **_kwargs: None,
    )

    await service._mark_failed_and_sync_tracker(
        UploadExecutionRequest(
            document_id="doc-missing-job",
            job_id="job-missing",
            user_id="user-provider",
            file_path=Path("/tmp/not-used.pdf"),
        ),
        message="Processing failed",
        stage=ProcessingStage.FAILED.value,
    )

    assert tracker.calls == []
    assert "durable job is missing" in caplog.text


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
    report_calls = []

    async def _update_document_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "set_process_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: events["failed"].append(kwargs))
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_cancelled",
        lambda **kwargs: events["cancelled"].append(kwargs)
        or SimpleNamespace(
            status=PdfJobStatus.CANCELLED.value,
            message=kwargs["reason"],
        ),
    )
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)
    monkeypatch.setattr(
        service_module,
        "report_background_task_exception",
        lambda exc, *, task_name, tags=None, context=None: report_calls.append(
            {
                "exc_type": type(exc).__name__,
                "task_name": task_name,
                "tags": dict(tags or {}),
                "context": dict(context or {}),
            }
        ),
    )

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
    assert report_calls == [
        {
            "exc_type": "RuntimeError",
            "task_name": "pdf_jobs.execute_upload",
            "tags": {
                "component": "pdf_jobs",
                "document_id": "doc-exc-1",
                "job_id": job_id,
                "failure_stage": "pipeline",
            },
            "context": {},
        }
    ]


@pytest.mark.asyncio
async def test_execute_upload_marks_cancelled_when_orchestrator_returns_cancelled_result(monkeypatch):
    job_id = "00000000-0000-0000-0000-000000000004"
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        orchestrator_factory=lambda _connection, _tracker: _Orchestrator(
            _pipeline_result(
                success=False,
                document_id="doc-cancelled-result",
                error="cancelled by user",
                cancelled=True,
            )
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
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_cancelled",
        lambda **kwargs: events["cancelled"].append(kwargs)
        or SimpleNamespace(
            status=PdfJobStatus.CANCELLED.value,
            message=kwargs["reason"],
        ),
    )

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
        orchestrator_factory=lambda _connection, _tracker: _Orchestrator(
            _pipeline_result(success=False, document_id="doc-tracker-failure", error="boom")
        ),
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


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_fails", [False, True])
async def test_provider_timeout_waits_for_storage_before_terminal_job(monkeypatch, storage_fails):
    """Exercise the real wait_for deadline together with storage cancellation."""
    import threading
    from unittest.mock import AsyncMock
    from src.lib.pipeline import store

    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    events = []
    properties = {}
    provider = _Provider()

    def write():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        events.append("writer_finished")
        if storage_fails:
            raise RuntimeError("storage unavailable")
        return {"stored_count": 1, "failed_count": 0}

    async def chunks(*_args, chunk_range):
        assert chunk_range == (0, 0)
        return await loop.run_in_executor(None, write)

    async def metadata(_document, stats, *_args):
        properties["chunkCount"] = stats["stored_chunks"]
        events.append("metadata_finished")

    async def status(*_args, **kwargs):
        properties.update(kwargs)

    async def ingest(request, *, weaviate_client):
        await store.store_to_weaviate(
            [{"chunk_index": 0, "content": "paper"}], request.document_id,
            weaviate_client, request.user_id, chunk_range=(0, 0),
        )

    service = UploadExecutionService(
        pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=ingest,
    )
    monkeypatch.setattr(store, "store_chunks_to_weaviate", chunks)
    monkeypatch.setattr(store, "update_document_metadata", metadata)
    monkeypatch.setattr(store, "update_document_status_detailed", status)
    monkeypatch.setattr(service_module, "get_connection", lambda: object())
    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 0.01)
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **_: events.append("job_failed"))
    monkeypatch.setattr(service_module, "update_document_status", AsyncMock())
    sql_failure = AsyncMock()
    monkeypatch.setattr(service, "_sync_provider_markdown_sql_failure", sql_failure)
    monkeypatch.setattr(service, "_report_execution_failure", lambda *_args, **_: None)
    request = ProviderMarkdownExecutionRequest(
        bearer_kind=ProviderBearerKind.CURATOR,
        document_id="doc-provider", job_id="job-provider", user_id="user-provider",
        owner_user_id=42, filename="paper.pdf", file_path=Path("/tmp/paper.pdf"),
        converted_artifact_id="markdown-1", curator_token="curator-token",
        source_provenance={"provider": "fake_provider", "access_scope": "global"},
    )
    task = asyncio.create_task(service.execute_provider_markdown(request))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.sleep(0.05)  # Cross the actual 10 ms wait_for deadline.
        assert not task.done()
        assert "job_failed" not in events
        sql_failure.assert_not_awaited()
    finally:
        release.set()
        await task
    assert events.index("writer_finished") < events.index("job_failed")
    assert properties["embedding_status"] == properties["processing_status"] == "failed"
    # Storage writes one file's pieces only; the paper's summed count is
    # written by finalize_new_paper, which a timed-out import never reaches.
    assert "metadata_finished" not in events
    assert "chunkCount" not in properties
    sql_failure.assert_awaited_once()
    assert "exceeded 0.01 seconds" in str(sql_failure.await_args.args[1])
    assert provider.closed


# --- KANBAN-1976 / ALL-1460, P7w: a longer deadline and a slower poll -------
#
# These tests use the real default deadline and poll interval (no monkeypatched
# getters) on a virtual clock, so "ABC takes 11 minutes" means 11 minutes of
# the import's own time, whatever the poll interval is.


class _VirtualClock:
    """Stands in for the module's ``asyncio`` with virtual time.

    ``sleep`` advances the clock; ``wait_for`` cancels the awaited coroutine at
    its next sleep past the deadline and turns that into ``TimeoutError``, as
    ``asyncio.wait_for`` does.
    """

    TimeoutError = asyncio.TimeoutError

    def __init__(self):
        self.now = 0.0
        self.deadline = None
        self.sleeps = []
        self.to_thread = asyncio.to_thread

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.deadline is not None and self.now > self.deadline:
            raise asyncio.CancelledError()
        await _real_sleep(0)

    def get_running_loop(self):
        return self  # the loop's clock is the virtual one

    def time(self):
        return self.now

    async def to_thread(self, function, /, *args, **kwargs):
        return function(*args, **kwargs)

    async def wait_for(self, awaitable, timeout):
        self.deadline = self.now + timeout
        try:
            return await awaitable
        except asyncio.CancelledError:
            if self.now > self.deadline:
                raise asyncio.TimeoutError() from None
            raise
        finally:
            self.deadline = None


_real_sleep = asyncio.sleep


class _ProviderReadyAt(_Provider):
    """ABC answers ``running``; its listing shows the main text once the virtual
    clock reaches ``ready_at``."""

    def __init__(self, clock, ready_at):
        super().__init__()
        self.clock = clock
        self.ready_at = ready_at
        self.artifacts = [
            SourceArtifact(
                provider="abc_literature",
                artifact_id="markdown-88",
                role=SourceArtifactRole.CONVERTED_TEXT,
                artifact_format=SourceArtifactFormat.MARKDOWN,
                status=SourceArtifactStatus.AVAILABLE,
                reference_curie="AGRKB:101",
                display_name="paper_merged.md",
                access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
                metadata={"file_class": "converted_merged_main", "file_extension": "md"},
            )
        ]

    async def list_artifacts(self, reference, *, request_bearer_token=None):
        self.list_artifact_calls.append({"reference": reference, "at": self.clock.now})
        return list(self.artifacts) if self.clock.now >= self.ready_at else []

    async def request_conversion(self, reference, *, wait=False, request_bearer_token=None):
        self.conversion_requests.append(
            {"reference": reference, "wait": wait, "at": self.clock.now}
        )
        if self.clock.now >= self.ready_at:
            return SourceConversionResult(
                provider="abc_literature",
                status=SourceConversionStatus.RUNNING,
                reference_curie="AGRKB:101",
                converted_classes=("converted_merged_main",),
            )
        return SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:101",
        )


def _wait_request():
    return ProviderConversionExecutionRequest(
        bearer_kind=ProviderBearerKind.CURATOR,
        document_id="doc-conversion",
        job_id="job-conversion",
        user_id="user-provider",
        owner_user_id=42,
        filename="paper.pdf",
        file_path=Path("/tmp/paper.pdf"),
        reference="AGRKB:101",
        source_artifact_id="source-pdf-1",
        curator_token="curator-token",
        source_provenance={"provider": "abc_literature"},
    )


def _record_job_outcomes(monkeypatch):
    outcomes = {"completed": [], "failed": [], "status": [], "sql_failures": []}

    async def _update_document_status(document_id, user_id, status):
        outcomes["status"].append((document_id, status))

    async def _sync_selection(self, request, converted_artifact):
        del self, request, converted_artifact

    async def _sync_sql_failure(self, request, exc):
        outcomes["sql_failures"].append(str(exc))

    monkeypatch.delenv("DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.setattr(service_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: outcomes["completed"].append(kwargs),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: outcomes["failed"].append(kwargs),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)
    monkeypatch.setattr(service_module, "update_document_status", _update_document_status)
    monkeypatch.setattr(
        UploadExecutionService, "_sync_provider_conversion_sql_selection", _sync_selection
    )
    monkeypatch.setattr(
        UploadExecutionService, "_sync_provider_conversion_sql_failure", _sync_sql_failure
    )
    return outcomes


@pytest.mark.asyncio
async def test_main_import_that_abc_converts_in_eleven_minutes_completes(monkeypatch):
    """Failed at the old 600 s deadline; completes under the 1-hour default."""

    clock = _VirtualClock()
    provider = _ProviderReadyAt(clock, ready_at=11 * 60)
    outcomes = _record_job_outcomes(monkeypatch)
    ingested = []

    async def _ingest(request, *, weaviate_client):
        del weaviate_client
        ingested.append(request.document_id)

    monkeypatch.setattr(service_module, "asyncio", clock)
    monkeypatch.setattr(service_module, "time", SimpleNamespace(time=lambda: clock.now))
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=_ingest,
    )

    await service.execute_provider_conversion(_wait_request())

    assert outcomes["failed"] == []
    assert outcomes["completed"] == [
        {"job_id": "job-conversion", "message": "Processing completed"}
    ]
    assert ingested == ["doc-conversion"]
    # KANBAN-2191 / ALL-1725: one on-demand request (this import had not sent one),
    # then a listing read every 30 s until the main text is listed at 11 minutes.
    assert set(clock.sleeps) == {30.0}
    assert [request["at"] for request in provider.conversion_requests] == [0.0]
    assert [read["at"] for read in provider.list_artifact_calls] == [30.0 * poll for poll in range(23)]


@pytest.mark.asyncio
async def test_main_import_still_waiting_after_an_hour_fails_and_the_paper_is_failed(monkeypatch):
    clock = _VirtualClock()
    provider = _ProviderReadyAt(clock, ready_at=61 * 60)
    outcomes = _record_job_outcomes(monkeypatch)
    monkeypatch.setattr(service_module, "asyncio", clock)
    monkeypatch.setattr(service_module, "time", SimpleNamespace(time=lambda: clock.now))
    monkeypatch.setattr(
        service_module,
        "get_document_source_provider_metadata",
        lambda provider_id: (
            {"display_label": "ABC Literature", "short_label": "ABC"}
            if provider_id == "abc_literature"
            else None
        ),
    )
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: provider,
    )

    await service.execute_provider_conversion(_wait_request())

    expected_timeout = (
        "ABC had not converted this paper 1 hour after the import started, "
        "so the import stopped. The time includes any wait behind other imports."
    )
    assert outcomes["completed"] == []
    assert outcomes["failed"] == [
        {
            "job_id": "job-conversion",
            "message": expected_timeout,
            "stage": ProcessingStage.FAILED.value,
        }
    ]
    assert outcomes["status"] == [("doc-conversion", "failed")]
    assert outcomes["sql_failures"] == [expected_timeout]
    # KANBAN-2191 / ALL-1725: the on-demand request, then only listing reads, then one
    # request at the start of the end window (3600 - (30 + 10) s = 3560 s; a read is
    # made to land there).
    assert [request["at"] for request in provider.conversion_requests] == [0.0, 3560.0]
    # Reads every 30 s to 3540 s (119), at 3560 s, straight after the end request,
    # then at 3590 s and at the deadline.
    assert [read["at"] for read in provider.list_artifact_calls][-5:] == [3540.0, 3560.0, 3560.0, 3590.0, 3600.0]
    assert len(provider.list_artifact_calls) == 123
    assert provider.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "final_answer",
    [
        SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.CONVERTED,
            reference_curie="AGRKB:101",
        ),
        SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.FAILED,
            reference_curie="AGRKB:101",
            error_message="Conversion failed",
        ),
        SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.NO_SOURCES,
            reference_curie="AGRKB:101",
        ),
    ],
    ids=["converted", "failed", "no_sources"],
)
async def test_abc_is_never_asked_again_after_a_final_answer(monkeypatch, final_answer):
    """KANBAN-2191 / ALL-1725: the wait's own request, then listing reads, then the
    one request at the end; its final answer ends the wait at once, and nothing
    is asked after it."""

    clock = _VirtualClock()
    provider = _ProviderReadyAt(clock, ready_at=float("inf"))
    running = SourceConversionResult(
        provider="abc_literature",
        status=SourceConversionStatus.RUNNING,
        reference_curie="AGRKB:101",
    )
    answers = [running, final_answer]

    async def _answer(reference, *, wait=False, request_bearer_token=None):
        provider.conversion_requests.append({"reference": reference, "at": clock.now})
        return answers.pop(0) if answers else running

    provider.request_conversion = _answer
    outcomes = _record_job_outcomes(monkeypatch)
    monkeypatch.setattr(service_module, "asyncio", clock)
    monkeypatch.setattr(service_module, "time", SimpleNamespace(time=lambda: clock.now))
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: provider,
    )

    await service.execute_provider_conversion(_wait_request())

    assert [request["at"] for request in provider.conversion_requests] == [0.0, 3560.0]
    assert len(outcomes["failed"]) == 1
    assert outcomes["completed"] == []


# --- P7w: the papers queued behind a long import in the same request stay fresh


def _queued_request(job_id: str) -> ProviderConversionExecutionRequest:
    return ProviderConversionExecutionRequest(
        bearer_kind=ProviderBearerKind.CURATOR,
        document_id=f"doc-{job_id}",
        job_id=job_id,
        user_id="user-provider",
        owner_user_id=42,
        filename="paper.pdf",
        file_path=Path("/tmp/paper.pdf"),
        reference="AGRKB:101",
        source_artifact_id="source-pdf-1",
        curator_token="curator-token",
        source_provenance={"provider": "abc_literature"},
    )


@pytest.mark.asyncio
async def test_jobs_queued_behind_a_running_import_of_the_same_request_are_kept_fresh(monkeypatch):
    """One request's imports run one after another; while one waits on ABC, the
    jobs queued behind it must not look stale to the sweep."""

    touched = []
    ran = []

    def _touch(*, job_ids):
        touched.append(tuple(job_ids))
        return len(job_ids)

    async def _unbounded(self, request):
        ran.append(request.job_id)
        if request.job_id == "job-1":
            # Longer than two keep-alive intervals (the interval is at least 0.1 s).
            await asyncio.sleep(0.35)

    monkeypatch.setattr(service_module.pdf_job_service, "touch_pending_jobs", _touch)
    monkeypatch.setattr(service_module, "get_document_source_poll_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(UploadExecutionService, "_execute_provider_conversion_unbounded", _unbounded)
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: _Provider(),
    )
    background_tasks = BackgroundTasks()
    for job_id in ("job-1", "job-2", "job-3"):
        await service.dispatch_provider_conversion_execution(
            background_tasks=background_tasks, request=_queued_request(job_id)
        )

    await background_tasks()

    assert ran == ["job-1", "job-2", "job-3"]
    # While job-1 ran: jobs 2 and 3, several times; while job-2 ran (briefly):
    # at most job 3; the last job keeps nothing alive.
    assert touched.count(("job-2", "job-3")) >= 2
    assert set(touched) <= {("job-2", "job-3"), ("job-3",)}


@pytest.mark.asyncio
async def test_a_request_with_one_import_keeps_nothing_alive(monkeypatch):
    touched = []

    async def _unbounded(self, request):
        await asyncio.sleep(0.03)

    monkeypatch.setattr(
        service_module.pdf_job_service,
        "touch_pending_jobs",
        lambda *, job_ids: touched.append(tuple(job_ids)),
    )
    monkeypatch.setattr(service_module, "get_document_source_poll_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(UploadExecutionService, "_execute_provider_conversion_unbounded", _unbounded)
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: _Provider(),
    )
    background_tasks = BackgroundTasks()
    await service.dispatch_provider_conversion_execution(
        background_tasks=background_tasks, request=_queued_request("job-1")
    )

    await background_tasks()

    assert touched == []


# --- P7w review: a wait finalized by a dead-wait cancel writes nothing afterwards


@pytest.mark.asyncio
@pytest.mark.parametrize("late_answer", ["main_ready", "provider_error"])
async def test_an_iteration_in_flight_when_its_job_was_finalized_writes_nothing(
    monkeypatch, late_answer
):
    """The curator cancelled what looked like a dead wait (finalized at once)
    while one poll was still in flight. Whatever that poll returns, it must not
    write the paper's source fields or mark it failed: the paper may already be
    imported again."""

    clock = _VirtualClock()
    provider = _ProviderReadyAt(clock, ready_at=float("inf"))
    job = {"status": PdfJobStatus.PENDING.value}
    outcomes = _record_job_outcomes(monkeypatch)
    selections = []
    ingested = []

    reads = []

    async def _read(reference, *, request_bearer_token=None):
        reads.append(clock.now)
        if len(reads) == 1:
            return []
        # Meanwhile the dead-wait cancel finalized the job.
        job["status"] = PdfJobStatus.CANCELLED.value
        if late_answer == "provider_error":
            raise DocumentSourceError("ABC Literature artifact listing failed")
        return list(_ProviderReadyAt(clock, ready_at=0).artifacts)  # the main text is listed

    async def _select(self, request, converted_artifact):
        selections.append(converted_artifact.artifact_id)

    async def _ingest(request, *, weaviate_client):
        ingested.append(request.document_id)

    provider.list_artifacts = _read
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "get_job_by_id",
        lambda **_kwargs: SimpleNamespace(status=job["status"], metadata=None),
    )
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_conversion_sql_selection", _select)
    monkeypatch.setattr(service_module, "asyncio", clock)
    monkeypatch.setattr(service_module, "time", SimpleNamespace(time=lambda: clock.now))
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=_ingest,
    )

    await service.execute_provider_conversion(_wait_request())

    assert len(provider.conversion_requests) == 1  # the wait's own on-demand request
    assert len(reads) == 2
    assert selections == []
    assert ingested == []
    assert outcomes["status"] == []
    assert outcomes["sql_failures"] == []
    assert outcomes["failed"] == []
    assert outcomes["completed"] == []
    assert provider.closed is True


# --- P7w review: no bookkeeping outlives the request's tasks


def _forget(service):
    """The request's BackgroundTasks is gone (the caller dropped every reference)."""
    import gc

    gc.collect()
    return dict(service._queues_by_job)


@pytest.mark.asyncio
async def test_queue_bookkeeping_is_dropped_when_the_first_task_raises(monkeypatch):
    async def _unbounded(self, request):
        raise RuntimeError("made-up failure outside the job's own handling")

    monkeypatch.setattr(service_module.pdf_job_service, "touch_pending_jobs", lambda *, job_ids: 0)
    monkeypatch.setattr(UploadExecutionService, "_execute_provider_conversion_unbounded", _unbounded)
    service = UploadExecutionService(pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: _Provider())
    background_tasks = BackgroundTasks()
    for job_id in ("job-1", "job-2", "job-3"):
        await service.dispatch_provider_conversion_execution(
            background_tasks=background_tasks, request=_queued_request(job_id)
        )

    with pytest.raises(RuntimeError) as raised:
        await background_tasks()  # Starlette skips job-2 and job-3
    del raised, background_tasks

    assert _forget(service) == {}


@pytest.mark.asyncio
async def test_queue_bookkeeping_is_dropped_when_the_request_task_is_cancelled(monkeypatch):
    started = asyncio.Event()

    async def _unbounded(self, request):
        started.set()
        await _real_sleep(60)

    monkeypatch.setattr(service_module.pdf_job_service, "touch_pending_jobs", lambda *, job_ids: 0)
    monkeypatch.setattr(UploadExecutionService, "_execute_provider_conversion_unbounded", _unbounded)
    service = UploadExecutionService(pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: _Provider())
    background_tasks = BackgroundTasks()
    for job_id in ("job-1", "job-2", "job-3"):
        await service.dispatch_provider_conversion_execution(
            background_tasks=background_tasks, request=_queued_request(job_id)
        )
    running = asyncio.ensure_future(background_tasks())
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError) as raised:
        await running
    del raised, running, background_tasks

    assert _forget(service) == {}


@pytest.mark.asyncio
async def test_queue_bookkeeping_is_dropped_when_the_tasks_never_run():
    """The endpoint failed after dispatching, so no task runs at all."""

    service = UploadExecutionService(pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: _Provider())
    background_tasks = BackgroundTasks()
    for job_id in ("job-1", "job-2"):
        await service.dispatch_provider_conversion_execution(
            background_tasks=background_tasks, request=_queued_request(job_id)
        )
    del background_tasks

    assert _forget(service) == {}


# --- KANBAN-1976 / ALL-1460, P7b: the main paper hands its supplements to their own stage


def _plan(**overrides):
    from src.lib.document_sources.access import DocumentSourceRequestContext
    from src.lib.document_sources.supplement_plan import ChosenSupplement, SupplementPlan

    values = dict(
        chosen=(
            ChosenSupplement(
                provider_file_id="40",
                md5="md5-40",
                converted_md5="md5-41",
                display_name="Made_up_S40.xlsx",
            ),
        ),
        request_context=DocumentSourceRequestContext(
            provider_groups=("made-up-staff",), authorized_group_ids=("made-up-group",),
            bearer_kind=ProviderBearerKind.CURATOR, curator_token="curator-token",
        ),
        login_expires_at=None,
        owner_user_id=42,
    )
    values.update(overrides)
    return SupplementPlan(**values)


def _markdown_request(job_id="job-provider", **overrides):
    values = dict(
        bearer_kind=ProviderBearerKind.CURATOR,
        document_id=f"doc-{job_id}",
        job_id=job_id,
        user_id="user-provider",
        owner_user_id=42,
        filename="paper.pdf",
        file_path=Path("/tmp/paper.pdf"),
        converted_artifact_id="markdown-1",
        curator_token="curator-token",
        source_provenance={"provider": "fake_provider"},
    )
    values.update(overrides)
    return ProviderMarkdownExecutionRequest(**values)


def _quiet_jobs(monkeypatch, completed, failed=None):
    monkeypatch.setattr(service_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_completed", lambda **kwargs: completed.append(kwargs))
    monkeypatch.setattr(
        service_module.pdf_job_service, "mark_failed",
        lambda **kwargs: (failed if failed is not None else []).append(kwargs),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)


@pytest.mark.asyncio
async def test_a_ready_main_paper_completes_its_import_job_and_its_supplements_get_their_own(monkeypatch):
    """The paper completes and hands off its supplements atomically; the stage runs under its own job."""

    from src.lib.document_sources import supplement_wait

    completed, started, handoffs = [], [], []

    async def _ingest(request, *, weaviate_client):
        return None

    def _complete_and_handoff(**kwargs):
        handoffs.append(kwargs)
        main_resp = SimpleNamespace(
            job_id=kwargs["job_id"],
            status="completed",
            current_stage="completed",
            message=kwargs["message"],
            error_message=None,
        )
        stage_resp = SimpleNamespace(job_id="stage-job-1")
        return main_resp, stage_resp

    monkeypatch.setattr(
        supplement_wait, "start_supplement_stage",
        lambda *, job_id, run, outcomes: started.append((job_id, run, outcomes)),
    )
    _quiet_jobs(monkeypatch, completed)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "complete_import_and_start_supplement_wait",
        _complete_and_handoff,
    )
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: _Provider(),
        provider_markdown_ingestion_fn=_ingest,
    )

    await service.execute_provider_markdown(_markdown_request(supplements=_plan()))

    assert handoffs == [
        {
            "job_id": "job-provider",
            "document_id": "doc-job-provider",
            "user_id": 42,
            "filename": "paper.pdf",
            "message": "Processing completed",
            "metadata": None,
        }
    ]
    assert [job_id for job_id, _run, _outcomes in started] == ["stage-job-1"]
    await started[0][2].completed()
    assert completed[-1] == {"job_id": "stage-job-1", "message": "Supplements finished"}


@pytest.mark.asyncio
async def test_no_stage_starts_when_the_paper_became_busy_or_was_deleted_meanwhile(monkeypatch):
    from src.lib.document_sources import supplement_wait

    completed, started, handoffs = [], [], []

    async def _ingest(request, *, weaviate_client):
        return None

    def _busy_atomic(**kwargs):
        handoffs.append(kwargs)
        main_resp = SimpleNamespace(
            job_id=kwargs["job_id"],
            status="completed",
            current_stage="completed",
            cancel_requested=False,
            message=kwargs["message"],
            error_message=None,
        )
        return main_resp, None

    monkeypatch.setattr(
        supplement_wait, "start_supplement_stage",
        lambda *, job_id, run, outcomes: started.append(job_id),
    )
    _quiet_jobs(monkeypatch, completed)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "complete_import_and_start_supplement_wait",
        _busy_atomic,
    )
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: _Provider(),
        provider_markdown_ingestion_fn=_ingest,
    )

    await service.execute_provider_markdown(_markdown_request(supplements=_plan()))

    assert len(handoffs) == 1
    assert handoffs[0]["job_id"] == "job-provider"
    assert started == []


@pytest.mark.asyncio
async def test_complete_main_paper_post_commit_cancellation_still_launches_committed_stage_job(monkeypatch):
    """If cancellation arrives mid-to_thread during atomic handoff, the committed stage_job still launches."""
    import threading

    from src.lib.document_sources import supplement_wait

    entered_handoff = threading.Event()
    allow_commit_finish = threading.Event()
    started, completed = [], []

    def _slow_atomic_handoff(**kwargs):
        entered_handoff.set()
        allow_commit_finish.wait(timeout=5)
        main_resp = SimpleNamespace(
            job_id=kwargs["job_id"],
            status="completed",
            current_stage="completed",
            cancel_requested=False,
            message=kwargs["message"],
            error_message=None,
        )
        stage_resp = SimpleNamespace(job_id="committed-stage-job-1")
        return main_resp, stage_resp

    monkeypatch.setattr(
        supplement_wait,
        "start_supplement_stage",
        lambda *, job_id, run, outcomes: started.append((job_id, run, outcomes)),
    )
    _quiet_jobs(monkeypatch, completed)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "complete_import_and_start_supplement_wait",
        _slow_atomic_handoff,
    )
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: _Provider(),
    )

    task = asyncio.create_task(
        service._complete_main_paper(
            _markdown_request(supplements=_plan()),
            message="Processing completed",
        )
    )
    while not entered_handoff.is_set():
        await asyncio.sleep(0.005)
    task.cancel()
    await asyncio.sleep(0.01)
    allow_commit_finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Committed stage job MUST have launched its worker despite the mid-commit cancellation!
    assert [job_id for job_id, _run, _outcomes in started] == ["committed-stage-job-1"]


@pytest.mark.asyncio
async def test_complete_main_paper_marks_stage_job_failed_if_launch_raises(monkeypatch):
    """A launch failure after main-paper commit fails only the stage job and never corrupts the paper."""
    import threading

    from src.lib.document_sources import supplement_wait

    completed, failed, reported = [], [], []
    status_updates = []
    sql_failures = []

    async def _ingest(request, *, weaviate_client):
        del request, weaviate_client
        return None

    async def _update_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    async def _sync_sql_failure(self, request, exc):
        del self
        sql_failures.append((request.document_id, str(exc)))

    def _atomic_handoff(**kwargs):
        return (
            SimpleNamespace(
                job_id=kwargs["job_id"],
                status="completed",
                current_stage="completed",
                cancel_requested=False,
                message=kwargs["message"],
                error_message=None,
            ),
            SimpleNamespace(job_id="stage-launch-boom"),
        )

    def _boom_launch(**_kwargs):
        raise RuntimeError("event loop refused task")

    _quiet_jobs(monkeypatch, completed, failed)
    monkeypatch.setattr(service_module, "update_document_status", _update_status)
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_markdown_sql_failure", _sync_sql_failure)
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda exc, **kwargs: reported.append((exc, kwargs)),
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "complete_import_and_start_supplement_wait",
        _atomic_handoff,
    )
    monkeypatch.setattr(supplement_wait, "start_supplement_stage", _boom_launch)
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: _Provider(),
        provider_markdown_ingestion_fn=_ingest,
    )

    await service.execute_provider_markdown(_markdown_request(supplements=_plan()))

    assert len(failed) == 1
    assert failed[0]["job_id"] == "stage-launch-boom"
    assert failed[0]["message"] == supplement_wait.SUPPLEMENT_STAGE_FAILED_MESSAGE
    assert status_updates == []
    assert sql_failures == []
    assert tracker.calls[-1]["document_id"] == "doc-job-provider"
    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED
    assert tracker.failure_calls == []
    assert len(reported) == 1
    assert str(reported[0][0]) == "Supplement stage launch failed"
    assert reported[0][1]["component"] == "document_sources"
    assert reported[0][1]["operation"] == "supplement_stage"
    assert reported[0][1]["include_request"] is False

    # Even when cancelled mid-handoff AND launch raises, stage is failed, tracker syncs COMPLETED,
    # and the deferred CancelledError still propagates cleanly.
    entered = threading.Event()
    release = threading.Event()
    failed.clear()
    tracker.calls.clear()

    def _slow_handoff(**kwargs):
        entered.set()
        release.wait(timeout=5)
        return _atomic_handoff(**kwargs)

    monkeypatch.setattr(
        service_module.pdf_job_service,
        "complete_import_and_start_supplement_wait",
        _slow_handoff,
    )
    task = asyncio.create_task(
        service._complete_main_paper(_markdown_request(supplements=_plan()), message="Processing completed")
    )
    while not entered.is_set():
        await asyncio.sleep(0.005)
    task.cancel()
    await asyncio.sleep(0.01)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(failed) == 1 and failed[0]["job_id"] == "stage-launch-boom"
    assert tracker.calls[-1]["document_id"] == "doc-job-provider"
    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED
    assert tracker.failure_calls == []


@pytest.mark.asyncio
async def test_complete_main_paper_survives_stage_launch_and_mark_failed_both_raising(monkeypatch):
    """If both start_supplement_stage AND stage mark_failed raise, main paper stays completed and cancel propagates."""
    import threading

    from src.lib.document_sources import supplement_wait

    completed, mark_failed_calls = [], []
    status_updates, sql_failures = [], []

    async def _ingest(request, *, weaviate_client):
        del request, weaviate_client
        return None

    async def _update_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    async def _sync_sql_failure(self, request, exc):
        del self
        sql_failures.append((request.document_id, str(exc)))

    def _atomic_handoff(**kwargs):
        return (
            SimpleNamespace(
                job_id=kwargs["job_id"],
                status="completed",
                current_stage="completed",
                cancel_requested=False,
                message=kwargs["message"],
                error_message=None,
            ),
            SimpleNamespace(job_id="stage-double-boom"),
        )

    def _boom_launch(**_kwargs):
        raise RuntimeError("event loop refused task")

    def _boom_mark_failed(**kwargs):
        mark_failed_calls.append(kwargs)
        raise RuntimeError("db unavailable while failing stage job")

    _quiet_jobs(monkeypatch, completed)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", _boom_mark_failed)
    monkeypatch.setattr(service_module, "update_document_status", _update_status)
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_markdown_sql_failure", _sync_sql_failure)
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "complete_import_and_start_supplement_wait",
        _atomic_handoff,
    )
    monkeypatch.setattr(supplement_wait, "start_supplement_stage", _boom_launch)
    tracker = _Tracker()
    service = UploadExecutionService(
        pipeline_tracker=tracker,
        document_source_provider_factory=lambda: _Provider(),
        provider_markdown_ingestion_fn=_ingest,
    )

    # Normal execute_provider_markdown path: never corrupts main paper, syncs COMPLETED.
    await service.execute_provider_markdown(_markdown_request(supplements=_plan()))

    assert [call["job_id"] for call in mark_failed_calls] == ["stage-double-boom"]
    assert status_updates == []
    assert sql_failures == []
    assert tracker.calls[-1]["document_id"] == "doc-job-provider"
    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED
    assert tracker.failure_calls == []

    # Cancelled mid-handoff path: catches mark_failed error, syncs COMPLETED, and propagates CancelledError.
    entered = threading.Event()
    release = threading.Event()
    mark_failed_calls.clear()
    tracker.calls.clear()

    def _slow_handoff(**kwargs):
        entered.set()
        release.wait(timeout=5)
        return _atomic_handoff(**kwargs)

    monkeypatch.setattr(
        service_module.pdf_job_service,
        "complete_import_and_start_supplement_wait",
        _slow_handoff,
    )
    task = asyncio.create_task(
        service._complete_main_paper(_markdown_request(supplements=_plan()), message="Processing completed")
    )
    while not entered.is_set():
        await asyncio.sleep(0.005)
    task.cancel()
    await asyncio.sleep(0.01)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [call["job_id"] for call in mark_failed_calls] == ["stage-double-boom"]
    assert status_updates == []
    assert sql_failures == []
    assert tracker.calls[-1]["document_id"] == "doc-job-provider"
    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED
    assert tracker.failure_calls == []


@pytest.mark.asyncio
async def test_execute_provider_markdown_deadline_during_commit_launches_stage_and_never_fails_paper(monkeypatch):
    """If execute_provider_markdown times out mid-handoff commit, the stage launches and paper stays completed."""
    import threading

    from src.lib.document_sources import supplement_wait

    entered_handoff = threading.Event()
    allow_commit_finish = threading.Event()
    committed = False
    started, failed, status_updates, sql_failures = [], [], [], []

    async def _ingest(request, *, weaviate_client):
        del request, weaviate_client
        return None

    async def _update_status(document_id, user_id, status):
        status_updates.append((document_id, user_id, status))

    async def _sync_sql_failure(self, request, exc):
        del self
        sql_failures.append((request.document_id, str(exc)))

    def _slow_commit(**kwargs):
        nonlocal committed
        entered_handoff.set()
        allow_commit_finish.wait(timeout=5)
        committed = True
        return (
            SimpleNamespace(
                job_id=kwargs["job_id"],
                status="completed",
                current_stage="completed",
                cancel_requested=False,
                message=kwargs["message"],
                error_message=None,
            ),
            SimpleNamespace(job_id="stage-after-deadline"),
        )

    def _get_job_by_id(**_kwargs):
        return SimpleNamespace(status="completed" if committed else "pending", metadata={})

    _quiet_jobs(monkeypatch, [], failed)
    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 0.03)
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", _get_job_by_id)
    monkeypatch.setattr(service_module, "update_document_status", _update_status)
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_markdown_sql_failure", _sync_sql_failure)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "complete_import_and_start_supplement_wait",
        _slow_commit,
    )
    monkeypatch.setattr(
        supplement_wait,
        "start_supplement_stage",
        lambda *, job_id, run, outcomes: started.append(job_id),
    )
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: _Provider(),
        provider_markdown_ingestion_fn=_ingest,
    )

    run_task = asyncio.create_task(
        service.execute_provider_markdown(_markdown_request(supplements=_plan()))
    )
    while not entered_handoff.is_set():
        await asyncio.sleep(0.005)
    # Wait past the 0.03s bounded deadline so asyncio.wait_for cancels mid-commit, then let commit finish.
    await asyncio.sleep(0.06)
    allow_commit_finish.set()
    await run_task

    assert started == ["stage-after-deadline"]
    assert failed == []
    assert status_updates == []
    assert sql_failures == []


@pytest.mark.asyncio
async def test_late_worker_after_cancel_never_starts_supplement_stage(monkeypatch):
    from src.lib.document_sources import supplement_wait

    completed, started = [], []

    async def _ingest(request, *, weaviate_client):
        return None

    def _already_cancelled(**kwargs):
        return SimpleNamespace(
            job_id=kwargs["job_id"],
            status="cancelled",
            current_stage="cancelled",
            message="Cancelled by user",
            error_message=None,
        ), None

    monkeypatch.setattr(
        supplement_wait, "start_supplement_stage",
        lambda *, job_id, run, outcomes: started.append(job_id),
    )
    _quiet_jobs(monkeypatch, completed)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "complete_import_and_start_supplement_wait",
        _already_cancelled,
    )
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: _Provider(),
        provider_markdown_ingestion_fn=_ingest,
    )

    await service.execute_provider_markdown(_markdown_request(supplements=_plan()))

    assert started == []


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.01, "1 second"),
        (1, "1 second"),
        (12, "12 seconds"),
        (60, "1 minute"),
        (90, "90 seconds"),
        (120, "2 minutes"),
        (150, "2 minutes 30 seconds"),
        (2700, "45 minutes"),
        (3600, "1 hour"),
        (5400, "1 hour 30 minutes"),
        (10800, "3 hours"),
    ],
)
def test_format_conversion_wait_duration_uses_plain_english(seconds, expected):
    assert service_module._format_conversion_wait_duration(seconds) == expected


def test_conversion_timeout_message_resolves_canonical_provider_metadata(monkeypatch, caplog):
    # Real canonical registration for Alliance ABC resolves short_label="ABC" without monkeypatching.
    real_abc_msg = service_module._conversion_timeout_message(
        10800, source_provenance={"provider": "abc_literature"},
    )
    assert real_abc_msg == (
        "ABC had not converted this paper 3 hours after the import started, "
        "so the import stopped. The time includes any wait behind other imports."
    )

    monkeypatch.setattr(
        service_module,
        "get_document_source_provider_metadata",
        lambda provider_id: {
            "short_only": {"display_label": "Example Literature", "short_label": "EXL"},
            "display_only": {"display_label": "Example Literature"},
        }.get(provider_id),
    )

    short_msg = service_module._conversion_timeout_message(
        10800, source_provenance={"provider": "short_only"},
    )
    assert short_msg == (
        "EXL had not converted this paper 3 hours after the import started, "
        "so the import stopped. The time includes any wait behind other imports."
    )

    display_msg = service_module._conversion_timeout_message(
        5400, source_provenance={"provider": "display_only"},
    )
    assert display_msg == (
        "Example Literature had not converted this paper 1 hour 30 minutes after the import started, "
        "so the import stopped. The time includes any wait behind other imports."
    )

    caplog.set_level(logging.WARNING, logger=service_module.__name__)
    unregistered_msg = service_module._conversion_timeout_message(
        2700, source_provenance={"provider": "unregistered_source"},
    )
    assert unregistered_msg == (
        "The document source had not converted this paper 45 minutes after the import started, "
        "so the import stopped. The time includes any wait behind other imports."
    )
    assert "unregistered_source" in caplog.text

    fallback_msg = service_module._conversion_timeout_message(90)
    assert fallback_msg == (
        "The document source had not converted this paper 90 seconds after the import started, "
        "so the import stopped. The time includes any wait behind other imports."
    )


@pytest.mark.asyncio
async def test_execute_provider_conversion_bounded_timeout_includes_queue_wait_from_recorded_start(monkeypatch):
    from datetime import datetime, timedelta, timezone

    failed_events = []
    started_90_min_ago = datetime.now(timezone.utc) - timedelta(minutes=90, seconds=1)

    async def _slow_conversion(self, request):
        del self, request
        await asyncio.sleep(10)

    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: _Provider(),
    )
    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 0.01)
    monkeypatch.setattr(
        service_module.pdf_job_service,
        "get_job_by_id",
        lambda **_kwargs: SimpleNamespace(
            status="running",
            created_at=started_90_min_ago,
            metadata={},
        ),
    )
    monkeypatch.setattr(UploadExecutionService, "_execute_provider_conversion_unbounded", _slow_conversion)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: failed_events.append(kwargs))
    monkeypatch.setattr(service_module, "update_document_status", AsyncMock(return_value=None))
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_conversion_sql_failure", AsyncMock(return_value=None))

    await service.execute_provider_conversion(
        ProviderConversionExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-queued-wait",
            job_id="job-queued-wait",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            reference="AGRKB:101",
            source_artifact_id="source-pdf-1",
            curator_token="curator-token",
            source_provenance={"provider": "abc_literature"},
        )
    )

    assert len(failed_events) == 1
    assert failed_events[0]["message"] == (
        "ABC had not converted this paper 1 hour 30 minutes after the import started, "
        "so the import stopped. The time includes any wait behind other imports."
    )


@pytest.mark.asyncio
async def test_execute_provider_conversion_post_selection_ingestion_timeout_is_not_n32(monkeypatch):
    failed_events = []
    selected_artifact = SourceArtifact(
        provider="abc_literature",
        artifact_id="md-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="101",
        reference_curie="AGRKB:101",
        display_name="paper.md",
        md5sum="md5-1",
        parent_artifact_id="source-pdf-1",
    )

    async def _hang_during_ingestion(self, request, *, skip_replay_guard=False):
        del self, request, skip_replay_guard
        await asyncio.sleep(10)

    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: _Provider(),
    )
    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 0.05)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "record_provider_conversion_request", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        UploadExecutionService,
        "_select_converted_main_artifact",
        AsyncMock(return_value=(selected_artifact, ())),
    )
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_conversion_sql_selection", AsyncMock(return_value=None))
    monkeypatch.setattr(UploadExecutionService, "_execute_provider_markdown_unbounded", _hang_during_ingestion)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: failed_events.append(kwargs))
    monkeypatch.setattr(service_module, "update_document_status", AsyncMock(return_value=None))
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_conversion_sql_failure", AsyncMock(return_value=None))

    await service.execute_provider_conversion(
        ProviderConversionExecutionRequest(
            bearer_kind=ProviderBearerKind.CURATOR,
            document_id="doc-ingest-timeout",
            job_id="job-ingest-timeout",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            file_path=Path("/tmp/paper.pdf"),
            reference="AGRKB:101",
            source_artifact_id="source-pdf-1",
            curator_token="curator-token",
            source_provenance={"provider": "abc_literature"},
        )
    )

    assert len(failed_events) == 1
    assert failed_events[0]["message"] == "Provider Markdown import exceeded 0.05 seconds"
    assert "had not converted this paper" not in failed_events[0]["message"]


@pytest.mark.asyncio
async def test_overlapping_replay_for_same_job_id_cannot_clobber_ingesting_markdown_phase(monkeypatch):
    """Task-local ContextVar keeps an overlapping replay before phase flip from clearing state."""
    failed_events = []
    entered_selection = asyncio.Event()
    allow_selection_finish = asyncio.Event()
    selected_artifact = SourceArtifact(
        provider="abc_literature",
        artifact_id="md-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="101",
        reference_curie="AGRKB:101",
        display_name="paper.md",
        md5sum="md5-1",
        parent_artifact_id="source-pdf-1",
    )

    job_calls = 0

    def _get_job_by_id(**_kwargs):
        nonlocal job_calls
        job_calls += 1
        # Call 1 (worker 1 start): pending; Call 2+ (worker 2 replay & timeout check): running.
        return SimpleNamespace(status="pending" if job_calls == 1 else "running", metadata={})

    async def _select_then_wait(self, **_kwargs):
        del self
        entered_selection.set()
        await allow_selection_finish.wait()
        return selected_artifact, ()

    async def _hang_during_ingestion(self, request, *, skip_replay_guard=False):
        del self, request, skip_replay_guard
        await asyncio.sleep(10)

    service = UploadExecutionService(
        pipeline_tracker=_Tracker(),
        document_source_provider_factory=lambda: _Provider(),
    )
    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 0.08)
    monkeypatch.setattr(service_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(service_module.pdf_job_service, "get_job_by_id", _get_job_by_id)
    monkeypatch.setattr(service_module.pdf_job_service, "record_provider_conversion_request", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "update_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        UploadExecutionService,
        "_select_converted_main_artifact",
        _select_then_wait,
    )
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_conversion_sql_selection", AsyncMock(return_value=None))
    monkeypatch.setattr(UploadExecutionService, "_execute_provider_markdown_unbounded", _hang_during_ingestion)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **kwargs: failed_events.append(kwargs))
    monkeypatch.setattr(service_module, "update_document_status", AsyncMock(return_value=None))
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_conversion_sql_failure", AsyncMock(return_value=None))

    req = ProviderConversionExecutionRequest(
        bearer_kind=ProviderBearerKind.CURATOR,
        document_id="doc-overlap",
        job_id="job-overlap",
        user_id="user-provider",
        owner_user_id=42,
        filename="paper.pdf",
        file_path=Path("/tmp/paper.pdf"),
        reference="AGRKB:101",
        source_artifact_id="source-pdf-1",
        curator_token="curator-token",
        source_provenance={"provider": "abc_literature"},
    )
    worker_1 = asyncio.create_task(service.execute_provider_conversion(req))
    await entered_selection.wait()
    # Overlapping replay enters, hits replay guard, exits, and resets its own ContextVar token
    # BEFORE worker 1 reaches the ingesting_markdown = True transition (the exact shared-dict race).
    await service.execute_provider_conversion(req)
    allow_selection_finish.set()
    await worker_1

    assert len(failed_events) == 1
    assert failed_events[0]["message"] == "Provider Markdown import exceeded 0.08 seconds"
    assert "had not converted this paper" not in failed_events[0]["message"]


def test_recorded_import_started_at_treats_naive_on_demand_sent_at_as_utc():
    from datetime import datetime, timezone

    naive_job = SimpleNamespace(
        metadata={"provider_conversion_requests": {"on_demand_sent_at": "2026-11-24T15:00:00"}},
        created_at=None,
    )
    expected = datetime(2026, 11, 24, 15, 0, 0, tzinfo=timezone.utc).timestamp()
    assert service_module._recorded_import_started_at(naive_job, 999999.0) == expected


def _late_conversion_request(**overrides):
    import time as _time

    values = dict(
        bearer_kind=ProviderBearerKind.CURATOR, document_id="doc-late", job_id="job-late",
        user_id="user-provider", owner_user_id=42, filename="paper.pdf",
        file_path=Path("/tmp/paper.pdf"), reference="AGRKB:101", source_artifact_id="pdf-1",
        curator_token="curator-token", source_provenance={"provider": "abc_literature"},
        # Ten minutes left: inside the 15-minute reserve.
        login_expires_at=_time.time() + 600,
    )
    values.update(overrides)
    return ProviderConversionExecutionRequest(**values)


@pytest.mark.asyncio
async def test_a_queued_import_with_supplements_whose_login_is_about_to_expire_fails_by_name(monkeypatch):
    from src.lib.document_sources.models import LOGIN_EXPIRED_MESSAGE

    provider = _Provider()
    completed, failed, reported = [], [], []
    _quiet_jobs(monkeypatch, completed, failed)
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_conversion_sql_failure",
                        AsyncMock(return_value=None))
    monkeypatch.setattr(service_module, "update_document_status", AsyncMock(return_value=None))
    monkeypatch.delenv("DOCUMENT_SUPPLEMENT_ADD_RESERVE_SECONDS", raising=False)
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: provider,
    )
    monkeypatch.setattr(service, "_report_execution_failure", lambda exc, **kwargs: reported.append(exc))

    await service.execute_provider_conversion(_late_conversion_request(supplements=_plan()))

    assert provider.conversion_requests == []
    assert [event["message"] for event in failed] == [LOGIN_EXPIRED_MESSAGE]
    assert [event.get("supplement_failure_reason") for event in failed] == ["login_expired"]
    assert reported == []  # the curator's own condition, not a server failure


@pytest.mark.asyncio
async def test_markdown_import_login_expired_passes_login_expired_supplement_reason(monkeypatch):
    from src.lib.document_sources.models import LOGIN_EXPIRED_MESSAGE

    completed, failed, reported = [], [], []
    _quiet_jobs(monkeypatch, completed, failed)
    monkeypatch.setattr(
        UploadExecutionService, "_sync_provider_markdown_sql_failure", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(service_module, "update_document_status", AsyncMock(return_value=None))
    monkeypatch.delenv("DOCUMENT_SUPPLEMENT_ADD_RESERVE_SECONDS", raising=False)
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: _Provider(),
    )
    monkeypatch.setattr(service, "_report_execution_failure", lambda exc, **kwargs: reported.append(exc))

    import time as _time

    await service.execute_provider_markdown(
        _markdown_request(supplements=_plan(), login_expires_at=_time.time() + 600)
    )

    assert [event["message"] for event in failed] == [LOGIN_EXPIRED_MESSAGE]
    assert [event.get("supplement_failure_reason") for event in failed] == ["login_expired"]
    assert reported == []


@pytest.mark.asyncio
async def test_an_ordinary_import_is_never_refused_for_its_login(monkeypatch):
    """Without supplements, an import behaves exactly as before, whatever its login."""

    clock = _VirtualClock()
    provider = _ProviderReadyAt(clock, ready_at=0)
    outcomes = _record_job_outcomes(monkeypatch)
    monkeypatch.setattr(service_module, "asyncio", clock)
    monkeypatch.setattr(service_module, "time", SimpleNamespace(time=lambda: clock.now))
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: provider,
        provider_markdown_ingestion_fn=AsyncMock(return_value=None),
    )

    await service.execute_provider_conversion(_late_conversion_request())

    assert len(provider.conversion_requests) == 1
    assert outcomes["failed"] == []


@pytest.mark.asyncio
async def test_the_next_paper_of_a_request_starts_while_the_first_waits_for_its_supplements(monkeypatch):
    """One request's papers run one after another; the supplement stage runs
    beside them, so paper 2 does not wait up to 3 hours for paper 1's files."""

    from src.lib.document_sources import supplement_wait

    completed, ingested = [], []
    stage_may_end = asyncio.Event()

    async def _ingest(request, *, weaviate_client):
        ingested.append(request.document_id)

    async def _slow_stage(**kwargs):
        await stage_may_end.wait()
        await kwargs["outcomes"].completed()

    monkeypatch.setattr(supplement_wait, "run_supplement_stage", _slow_stage)
    _quiet_jobs(monkeypatch, completed)
    monkeypatch.setattr(service_module.pdf_job_service, "touch_pending_jobs", lambda *, job_ids: 0)

    def _complete_and_handoff(**kwargs):
        completed.append({"job_id": kwargs["job_id"], "message": kwargs["message"]})
        return (
            SimpleNamespace(
                job_id=kwargs["job_id"],
                status="completed",
                current_stage="completed",
                message=kwargs["message"],
                error_message=None,
            ),
            SimpleNamespace(job_id="stage-job-1"),
        )

    monkeypatch.setattr(
        service_module.pdf_job_service,
        "complete_import_and_start_supplement_wait",
        _complete_and_handoff,
    )
    service = UploadExecutionService(
        pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: _Provider(),
        provider_markdown_ingestion_fn=_ingest,
    )
    background_tasks = BackgroundTasks()
    await service.dispatch_provider_markdown_execution(
        background_tasks=background_tasks, request=_markdown_request("job-1", supplements=_plan()),
    )
    await service.dispatch_provider_markdown_execution(
        background_tasks=background_tasks, request=_markdown_request("job-2"),
    )

    await asyncio.wait_for(background_tasks(), timeout=10)

    assert ingested == ["doc-job-1", "doc-job-2"]
    assert [event["job_id"] for event in completed] == ["job-1", "job-2"]  # both papers ready
    stage_may_end.set()
    await asyncio.wait_for(asyncio.gather(*list(supplement_wait._stage_tasks)), timeout=10)
    assert [event["job_id"] for event in completed] == ["job-1", "job-2", "stage-job-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("deadline", "poll"), [(3600, 30), (3600, 9.2), (600, 30), (100, 7), (95, 13)])
async def test_a_main_wait_never_makes_more_polls_than_the_start_up_cap(monkeypatch, deadline, poll):
    """ALL-1678: the polling cap's start-up bound must hold for the real loop."""
    from src.lib.openai_agents.config import main_wait_max_polls

    clock = _VirtualClock()
    provider = _ProviderReadyAt(clock, ready_at=float("inf"))
    outcomes = _record_job_outcomes(monkeypatch)
    monkeypatch.setattr(service_module, "asyncio", clock)
    monkeypatch.setattr(service_module, "time", SimpleNamespace(time=lambda: clock.now))
    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: float(deadline))
    monkeypatch.setattr(service_module, "get_document_source_poll_interval_seconds", lambda: float(poll))
    service = UploadExecutionService(pipeline_tracker=_Tracker(), document_source_provider_factory=lambda: provider)

    await service.execute_provider_conversion(_wait_request())

    sent = len(provider.conversion_requests) + len(provider.list_artifact_calls)
    assert len(provider.conversion_requests) == 2  # the wait's own on-demand request and the end one
    assert sent <= main_wait_max_polls(deadline_seconds=deadline, poll_seconds=poll)
    assert len(outcomes["failed"]) == 1
