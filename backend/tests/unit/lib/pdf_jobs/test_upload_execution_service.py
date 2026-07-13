"""Unit tests for upload execution orchestration service."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from src.lib.exceptions import PDFCancellationError
from src.lib.document_sources.main_text import select_preferred_main_text_artifact
from src.lib.pdf_jobs import upload_execution_service as service_module
from src.lib.pdf_jobs.upload_execution_service import (
    JobAwarePipelineTracker,
    ProviderConversionExecutionRequest,
    ProviderMarkdownExecutionRequest,
    UploadExecutionRequest,
    UploadExecutionService,
)
from src.lib.document_sources.models import (
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceConversionResult,
    SourceConversionStatus,
)
from src.models.document import ProcessingStatus
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
            return any(status.get("main_converted") is True for status in result.per_mod_status)
        return result.status in {
            SourceConversionStatus.CONVERTED,
            SourceConversionStatus.RUNNING,
        }

    def is_main_text_artifact(self, artifact):
        return artifact.metadata.get("file_class") == self.main_file_class

    def main_text_artifact_sort_key(self, artifact):
        display_name = str(artifact.display_name or "").lower()
        if self.provider_id != "abc_literature":
            return (int(artifact.metadata.get("provider_rank", 0)),)
        if "_nxml" in display_name:
            return (0,)
        if "_tei" in display_name:
            return (100,)
        return (1,)

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
    assert getattr(background_tasks.tasks[0].func, "__observability_original_task__") == service.execute_upload
    assert getattr(background_tasks.tasks[0].func, "__observability_task_name__") == "pdf_jobs.execute_upload"
    assert background_tasks.tasks[0].args == (request,)


@pytest.mark.asyncio
async def test_execute_provider_markdown_downloads_with_curator_token_and_marks_completed(monkeypatch):
    tracker = _Tracker()
    provider = _Provider()
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
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
    assert ingestion_calls[0]["weaviate_client"] == "weaviate-client"
    ingestion_request = ingestion_calls[0]["request"]
    assert ingestion_request.document_id == "doc-provider"
    assert ingestion_request.user_id == "user-provider"
    assert ingestion_request.document_owner_user_id == 42
    assert ingestion_request.markdown == "# Title\n\nBody"
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
    monkeypatch.setattr(service_module.pdf_job_service, "mark_completed", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
    monkeypatch.setattr(service_module.pdf_job_service, "mark_completed", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_failed", lambda **_kwargs: None)
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
        SimpleNamespace(status=PdfJobStatus.PENDING.value),
        SimpleNamespace(status=PdfJobStatus.RUNNING.value),
    ]

    def _get_job_by_id(**_kwargs):
        if job_statuses:
            return job_statuses.pop(0)
        return SimpleNamespace(status=PdfJobStatus.RUNNING.value)

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
            document_id="doc-conversion",
            job_id="job-conversion",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
    assert len(job_statuses) == 1
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


@pytest.mark.asyncio
async def test_execute_provider_markdown_fails_failed_figure_metadata_download(monkeypatch):
    tracker = _Tracker()
    provider = _Provider()
    provider.payloads = {"markdown-1": b"# Results\n\nNative result."}
    provider.download_errors = {"meta-bad": RuntimeError("missing sidecar")}
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
        lambda **kwargs: failed_events.append(kwargs),
    )
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)

    await service.execute_provider_markdown(
        ProviderMarkdownExecutionRequest(
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            converted_artifact_id="markdown-1",
            curator_token="curator-token",
            source_provenance={"provider": "fake_provider"},
            figure_metadata_artifact_ids=("meta-bad",),
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
            "message": "missing sidecar",
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


def test_select_preferred_main_text_accepts_abc_tei_only_artifact():
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

    assert selected is artifact
    assert ambiguous_count == 1


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
async def test_upload_selection_accepts_per_mod_only_readiness():
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
            document_id="doc-1",
            job_id="job-1",
            user_id="user-1",
            owner_user_id=1,
            filename="paper.pdf",
            reference="AGRKB:101",
            source_artifact_id="pdf-1",
            curator_token="token",
            source_provenance={"provider": "abc_literature"},
        ),
        conversion_result=SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.RUNNING,
            per_mod_status=({"mod": "FB", "main_converted": True},),
        ),
        curator_token="token",
    )

    assert selected is artifact
    assert metadata_ids == ()


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
            document_id="doc-1",
            job_id="job-1",
            user_id="user-1",
            owner_user_id=1,
            filename="paper.pdf",
            reference="other:101",
            source_artifact_id="pdf-1",
            curator_token="token",
            source_provenance={"provider": "other_provider"},
        ),
        conversion_result=SourceConversionResult(
            provider="other_provider",
            status=SourceConversionStatus.CONVERTED,
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
            metadata={"file_class": "converted_tei_main", "file_extension": "md"},
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
            document_id="doc-conversion",
            job_id="job-conversion",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
            document_id="doc-conversion",
            job_id="job-conversion",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
            document_id="doc-conversion",
            job_id="job-conversion",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
            document_id="doc-conversion",
            job_id="job-conversion",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
            reference="AGRKB:101",
            source_artifact_id="source-pdf-1",
            curator_token="curator-token",
            source_provenance={"provider": "abc_literature"},
        )
    )

    assert status_updates == [("doc-conversion", "user-provider", "failed")]
    assert sql_failures == [
        ("doc-conversion", "Provider conversion exceeded 0.01 seconds")
    ]
    assert failed_events == [
        {
            "job_id": "job-conversion",
            "message": "Provider conversion exceeded 0.01 seconds",
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
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
            document_id="doc-provider",
            job_id="job-provider",
            user_id="user-provider",
            owner_user_id=42,
            filename="paper.pdf",
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
    monkeypatch.setattr(service_module.pdf_job_service, "mark_cancelled", lambda **kwargs: events["cancelled"].append(kwargs))
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
