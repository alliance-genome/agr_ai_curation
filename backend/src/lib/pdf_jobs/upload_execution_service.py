"""Runtime service for upload execution orchestration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from fastapi import BackgroundTasks

from src.lib.document_sources.ingestion import (
    DocumentSourceIngestionError,
    ProviderMarkdownIngestionRequest,
    ingest_provider_markdown_document,
)
from src.lib.document_sources.models import DocumentSourceProvider
from src.lib.document_sources.models import (
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceConversionResult,
    SourceConversionStatus,
)
from src.lib.document_sources.registry import get_configured_document_source_provider
from src.lib.exceptions import PDFCancellationError
from src.lib.observability.background_tasks import (
    add_observed_background_task,
    report_background_task_exception,
)
from src.lib.openai_agents.config import (
    get_document_source_import_timeout_seconds,
    get_document_source_poll_interval_seconds,
)
from src.lib.pipeline.orchestrator import DocumentPipelineOrchestrator
from src.lib.pipeline.tracker import PipelineTracker
from src.lib.weaviate_helpers import get_connection
from src.lib.weaviate_client.documents import update_document_status
from src.models.document import ProcessingStatus
from src.models.pipeline import ProcessingStage
from src.models.sql.pdf_processing_job import PdfJobStatus

from . import service as pdf_job_service

logger = logging.getLogger(__name__)

_NON_PENDING_JOB_STATUSES = {
    PdfJobStatus.RUNNING.value,
    PdfJobStatus.CANCEL_REQUESTED.value,
    PdfJobStatus.COMPLETED.value,
    PdfJobStatus.FAILED.value,
    PdfJobStatus.CANCELLED.value,
}


def normalize_pipeline_result(result: Any) -> tuple[bool, bool, Optional[str]]:
    """Normalize pipeline result payloads across object and legacy dict shapes."""
    if isinstance(result, dict):
        status = str(result.get("status", "")).strip().lower()
        cancelled = bool(result.get("cancelled")) or status in {"cancelled", "canceled", "cancel_requested"}
        success_raw = result.get("success")
        if success_raw is None:
            success = status in {"completed", "complete", "success", "succeeded"} and not cancelled
        else:
            success = bool(success_raw)
        error = result.get("error") or result.get("message")
        return success, cancelled, error

    status_attr = str(getattr(result, "status", "") or "").strip().lower()
    cancelled = bool(getattr(result, "cancelled", False)) or status_attr in {"cancelled", "canceled", "cancel_requested"}
    success_attr = getattr(result, "success", None)
    if success_attr is None:
        success = status_attr in {"completed", "complete", "success", "succeeded"} and not cancelled
    else:
        success = bool(success_attr)
    error = getattr(result, "error", None) or getattr(result, "message", None)
    return success, cancelled, error


class JobAwarePipelineTracker:
    """Tracker proxy that mirrors in-memory pipeline progress into durable job rows."""

    def __init__(self, base_tracker: PipelineTracker, job_id: str):
        self.base_tracker = base_tracker
        self.job_id = job_id

    async def track_pipeline_progress(
        self,
        document_id: str,
        stage: ProcessingStage,
        progress_percentage: Optional[int] = None,
        message: Optional[str] = None,
    ):
        stage_value = stage.value if isinstance(stage, ProcessingStage) else str(stage)

        if (
            pdf_job_service.is_cancel_requested(job_id=self.job_id)
            and stage_value not in {ProcessingStage.FAILED.value, ProcessingStage.COMPLETED.value}
        ):
            raise PDFCancellationError("Processing cancelled by user request")

        status = await self.base_tracker.track_pipeline_progress(
            document_id=document_id,
            stage=stage,
            progress_percentage=progress_percentage,
            message=message,
        )

        if stage_value == ProcessingStage.COMPLETED.value:
            pdf_job_service.mark_completed(
                job_id=self.job_id,
                message=message or "Processing completed",
            )
        elif stage_value == ProcessingStage.FAILED.value:
            if pdf_job_service.is_cancel_requested(job_id=self.job_id):
                pdf_job_service.update_progress(
                    job_id=self.job_id,
                    stage=PdfJobStatus.CANCEL_REQUESTED.value,
                    progress_percentage=progress_percentage,
                    message=message or "Cancellation requested",
                    status=PdfJobStatus.CANCEL_REQUESTED.value,
                )
            else:
                pdf_job_service.mark_failed(
                    job_id=self.job_id,
                    message=message or "Processing failed",
                    stage=stage_value,
                )
        else:
            pdf_job_service.update_progress(
                job_id=self.job_id,
                stage=stage_value,
                progress_percentage=progress_percentage,
                message=message,
                status=PdfJobStatus.RUNNING.value,
            )

        return status

    async def get_pipeline_status(self, document_id: str):
        return await self.base_tracker.get_pipeline_status(document_id)

    async def handle_pipeline_failure(self, document_id: str, error: Exception, stage: Optional[ProcessingStage] = None):
        result = await self.base_tracker.handle_pipeline_failure(document_id, error, stage)
        stage_value = stage.value if isinstance(stage, ProcessingStage) else str(stage or ProcessingStage.FAILED.value)
        pdf_job_service.mark_failed(
            job_id=self.job_id,
            message=str(error),
            stage=stage_value,
        )
        return result


@dataclass(frozen=True)
class UploadExecutionRequest:
    """Context needed to execute upload processing in the background."""

    document_id: str
    job_id: str
    user_id: str
    file_path: Path


@dataclass(frozen=True)
class ProviderMarkdownExecutionRequest:
    """Context needed to ingest provider-converted Markdown in the background."""

    document_id: str
    job_id: str
    user_id: str
    owner_user_id: int
    filename: str
    converted_artifact_id: str
    curator_token: str = field(repr=False)
    source_provenance: Mapping[str, Any]
    figure_metadata_artifact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderConversionExecutionRequest:
    """Context needed to wait for provider conversion before Markdown ingestion."""

    document_id: str
    job_id: str
    user_id: str
    owner_user_id: int
    filename: str
    reference: str
    source_artifact_id: str
    curator_token: str = field(repr=False)
    source_provenance: Mapping[str, Any]
    figure_metadata_artifact_ids: tuple[str, ...] = ()


class UploadExecutionService:
    """Dispatch and execute the upload processing pipeline lifecycle."""

    def __init__(
        self,
        *,
        pipeline_tracker: PipelineTracker,
        orchestrator_factory: Optional[Callable[[Any, Any], DocumentPipelineOrchestrator]] = None,
        document_source_provider_factory: Callable[[], DocumentSourceProvider] = get_configured_document_source_provider,
        provider_markdown_ingestion_fn: Callable[..., Any] = ingest_provider_markdown_document,
    ) -> None:
        self.pipeline_tracker = pipeline_tracker
        self._orchestrator_factory = orchestrator_factory or self._default_orchestrator_factory
        self._document_source_provider_factory = document_source_provider_factory
        self._provider_markdown_ingestion = provider_markdown_ingestion_fn

    async def dispatch_upload_execution(
        self,
        *,
        background_tasks: BackgroundTasks,
        request: UploadExecutionRequest,
    ) -> None:
        """Prime tracking and queue upload execution on FastAPI background tasks."""
        await self.pipeline_tracker.track_pipeline_progress(request.document_id, ProcessingStage.UPLOAD)
        add_observed_background_task(
            background_tasks,
            self.execute_upload,
            request,
            task_name="pdf_jobs.execute_upload",
            tags={
                "component": "pdf_jobs",
                "document_id": request.document_id,
                "job_id": request.job_id,
            },
        )

    async def dispatch_provider_markdown_execution(
        self,
        *,
        background_tasks: BackgroundTasks,
        request: ProviderMarkdownExecutionRequest,
    ) -> None:
        """Prime tracking and queue provider Markdown ingestion on background tasks."""
        await self.pipeline_tracker.track_pipeline_progress(request.document_id, ProcessingStage.UPLOAD)
        add_observed_background_task(
            background_tasks,
            self.execute_provider_markdown,
            request,
            task_name="pdf_jobs.execute_provider_markdown",
            tags={
                "component": "pdf_jobs",
                "document_id": request.document_id,
                "job_id": request.job_id,
            },
        )

    async def dispatch_provider_conversion_execution(
        self,
        *,
        background_tasks: BackgroundTasks,
        request: ProviderConversionExecutionRequest,
    ) -> None:
        """Prime tracking and queue provider conversion polling."""
        await self.pipeline_tracker.track_pipeline_progress(request.document_id, ProcessingStage.UPLOAD)
        add_observed_background_task(
            background_tasks,
            self.execute_provider_conversion,
            request,
            task_name="pdf_jobs.execute_provider_conversion",
            tags={
                "component": "pdf_jobs",
                "document_id": request.document_id,
                "job_id": request.job_id,
            },
        )

    async def execute_upload(self, request: UploadExecutionRequest) -> None:
        """Run upload orchestration and persist durable job transitions."""
        if pdf_job_service.is_cancel_requested(job_id=request.job_id):
            await self._handle_pre_start_cancellation(request)
            return

        existing_job = pdf_job_service.get_job_by_id(job_id=request.job_id, reconcile_stale=False)
        if existing_job and existing_job.status in _NON_PENDING_JOB_STATUSES:
            logger.info(
                "Skipping replayed upload execution for job %s with durable status %s",
                request.job_id,
                existing_job.status,
            )
            return

        try:
            job_tracker = JobAwarePipelineTracker(
                base_tracker=self.pipeline_tracker,
                job_id=request.job_id,
            )
            pdf_job_service.update_progress(
                job_id=request.job_id,
                stage=ProcessingStage.UPLOAD.value,
                progress_percentage=10,
                message="Processing started",
                status=PdfJobStatus.RUNNING.value,
            )

            async def _cancel_requested() -> bool:
                return pdf_job_service.is_cancel_requested(job_id=request.job_id)

            async def _on_process_id(process_id: str) -> None:
                pdf_job_service.set_process_id(job_id=request.job_id, process_id=process_id)

            connection = get_connection()
            orchestrator = self._orchestrator_factory(connection, job_tracker)
            result = await orchestrator.process_pdf_document(
                file_path=request.file_path,
                document_id=request.document_id,
                user_id=request.user_id,
                validate_first=False,
                cancel_requested_callback=_cancel_requested,
                process_id_callback=_on_process_id,
            )
            logger.info("Document %s processing completed: %s", request.document_id, result)
            success, cancelled, error_message = normalize_pipeline_result(result)
            await self._finalize_pipeline_result(
                request=request,
                success=success,
                cancelled=cancelled,
                error_message=error_message,
            )
        except Exception as exc:
            logger.error("Error processing document %s: %s", request.document_id, exc, exc_info=True)
            if not isinstance(exc, PDFCancellationError):
                self._report_execution_failure(
                    exc,
                    task_name="pdf_jobs.execute_upload",
                    request=request,
                    failure_stage="pipeline",
                )
            try:
                await update_document_status(request.document_id, request.user_id, ProcessingStatus.FAILED.value)
            except Exception as status_err:
                logger.warning(
                    "Failed to sync document status for failed processing document=%s: %s",
                    request.document_id,
                    status_err,
                )
            if isinstance(exc, PDFCancellationError):
                pdf_job_service.mark_cancelled(job_id=request.job_id, reason=str(exc))
            else:
                pdf_job_service.mark_failed(
                    job_id=request.job_id,
                    message=str(exc),
                    stage=ProcessingStage.FAILED.value,
                )

    async def execute_provider_markdown(self, request: ProviderMarkdownExecutionRequest) -> None:
        """Run provider Markdown ingestion with the configured wall-clock bound."""
        timeout_seconds = get_document_source_import_timeout_seconds()
        try:
            await asyncio.wait_for(
                self._execute_provider_markdown_unbounded(request),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            timeout_error = RuntimeError(
                f"Provider Markdown import exceeded {timeout_seconds:g} seconds"
            )
            logger.error(
                "Provider Markdown import timed out for document %s after %s seconds",
                request.document_id,
                timeout_seconds,
            )
            self._report_execution_failure(
                timeout_error,
                task_name="pdf_jobs.execute_provider_markdown",
                request=request,
                failure_stage="timeout",
            )
            try:
                await update_document_status(
                    request.document_id,
                    request.user_id,
                    ProcessingStatus.FAILED.value,
                )
            except Exception as status_err:
                logger.warning(
                    "Failed to sync document status for timed-out provider Markdown document=%s: %s",
                    request.document_id,
                    status_err,
                )
            await self._sync_provider_markdown_sql_failure(request, timeout_error)
            pdf_job_service.mark_failed(
                job_id=request.job_id,
                message=str(timeout_error),
                stage=ProcessingStage.FAILED.value,
            )

    async def execute_provider_conversion(self, request: ProviderConversionExecutionRequest) -> None:
        """Poll provider conversion until main Markdown can be ingested."""
        timeout_seconds = get_document_source_import_timeout_seconds()
        try:
            await asyncio.wait_for(
                self._execute_provider_conversion_unbounded(request),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            timeout_error = RuntimeError(
                f"Provider conversion exceeded {timeout_seconds:g} seconds"
            )
            logger.error(
                "Provider conversion timed out for document %s after %s seconds",
                request.document_id,
                timeout_seconds,
            )
            self._report_execution_failure(
                timeout_error,
                task_name="pdf_jobs.execute_provider_conversion",
                request=request,
                failure_stage="timeout",
            )
            try:
                await update_document_status(
                    request.document_id,
                    request.user_id,
                    ProcessingStatus.FAILED.value,
                )
            except Exception as status_err:
                logger.warning(
                    "Failed to sync document status for timed-out provider conversion document=%s: %s",
                    request.document_id,
                    status_err,
                )
            await self._sync_provider_conversion_sql_failure(request, timeout_error)
            pdf_job_service.mark_failed(
                job_id=request.job_id,
                message=str(timeout_error),
                stage=ProcessingStage.FAILED.value,
            )

    async def _execute_provider_conversion_unbounded(
        self,
        request: ProviderConversionExecutionRequest,
    ) -> None:
        """Wait for provider main Markdown, then ingest it through provider Markdown flow."""
        if pdf_job_service.is_cancel_requested(job_id=request.job_id):
            await self._handle_pre_start_cancellation(request)
            return

        existing_job = pdf_job_service.get_job_by_id(job_id=request.job_id, reconcile_stale=False)
        if existing_job and existing_job.status in _NON_PENDING_JOB_STATUSES:
            logger.info(
                "Skipping replayed provider conversion execution for job %s with durable status %s",
                request.job_id,
                existing_job.status,
            )
            return

        provider: DocumentSourceProvider | None = None
        poll_interval_seconds = max(0.1, get_document_source_poll_interval_seconds())
        last_result: SourceConversionResult | None = None
        try:
            curator_token = (request.curator_token or "").strip()
            if not curator_token:
                raise RuntimeError("Provider conversion requires a request curator token")

            provider = self._document_source_provider_factory()
            while True:
                if pdf_job_service.is_cancel_requested(job_id=request.job_id):
                    raise PDFCancellationError("Processing cancelled by user request")

                last_result = await provider.request_conversion(
                    request.reference,
                    wait=False,
                    request_bearer_token=curator_token,
                )
                (
                    selected_artifact,
                    selected_figure_metadata_artifact_ids,
                ) = await self._select_converted_main_artifact(
                    provider=provider,
                    request=request,
                    conversion_result=last_result,
                    curator_token=curator_token,
                )
                progress_metadata = _conversion_job_metadata(last_result)
                progress_percentage = 35 if selected_artifact is not None else _conversion_progress_percentage(last_result)
                message = _conversion_progress_message(last_result)
                pdf_job_service.update_progress(
                    job_id=request.job_id,
                    stage="provider_conversion",
                    progress_percentage=progress_percentage,
                    message=message,
                    status=PdfJobStatus.RUNNING.value,
                    metadata=progress_metadata,
                )

                if selected_artifact is not None:
                    source_provenance = _source_provenance_with_converted_artifact(
                        request.source_provenance,
                        selected_artifact,
                    )
                    figure_metadata_artifact_ids = _dedupe_strings(
                        (
                            *request.figure_metadata_artifact_ids,
                            *selected_figure_metadata_artifact_ids,
                        )
                    )
                    await self._sync_provider_conversion_sql_selection(
                        request,
                        selected_artifact,
                    )
                    await self._execute_provider_markdown_unbounded(
                        ProviderMarkdownExecutionRequest(
                            document_id=request.document_id,
                            job_id=request.job_id,
                            user_id=request.user_id,
                            owner_user_id=request.owner_user_id,
                            filename=request.filename,
                            converted_artifact_id=selected_artifact.artifact_id,
                            curator_token=curator_token,
                            source_provenance=source_provenance,
                            figure_metadata_artifact_ids=figure_metadata_artifact_ids,
                        ),
                        skip_replay_guard=True,
                    )
                    return

                if last_result.status is SourceConversionStatus.FAILED:
                    raise RuntimeError(_conversion_failure_message(last_result))
                if last_result.status is SourceConversionStatus.NO_SOURCES:
                    raise RuntimeError("Provider conversion found no convertible source files")
                if last_result.status is SourceConversionStatus.CONVERTED:
                    raise RuntimeError("Provider conversion completed without canonical converted Markdown")

                await asyncio.sleep(poll_interval_seconds)
        except Exception as exc:
            logger.error(
                "Error waiting for provider conversion document %s: %s",
                request.document_id,
                exc,
                exc_info=True,
            )
            if not isinstance(exc, PDFCancellationError):
                self._report_execution_failure(
                    exc,
                    task_name="pdf_jobs.execute_provider_conversion",
                    request=request,
                    failure_stage="provider_conversion",
                )
            try:
                await update_document_status(
                    request.document_id,
                    request.user_id,
                    ProcessingStatus.FAILED.value,
                )
            except Exception as status_err:
                logger.warning(
                    "Failed to sync document status for failed provider conversion document=%s: %s",
                    request.document_id,
                    status_err,
                )
            if isinstance(exc, PDFCancellationError):
                pdf_job_service.mark_cancelled(job_id=request.job_id, reason=str(exc))
            else:
                failure_metadata = (
                    _conversion_job_metadata(last_result)
                    if last_result is not None
                    else {"document_source": {"conversion_status": "failed"}}
                )
                pdf_job_service.update_progress(
                    job_id=request.job_id,
                    stage="provider_conversion",
                    status=PdfJobStatus.RUNNING.value,
                    metadata=failure_metadata,
                )
                await self._sync_provider_conversion_sql_failure(request, exc)
                pdf_job_service.mark_failed(
                    job_id=request.job_id,
                    message=str(exc),
                    stage=ProcessingStage.FAILED.value,
                )
        finally:
            if provider is not None:
                try:
                    await provider.aclose()
                except Exception as cleanup_err:
                    logger.warning(
                        "Best-effort document-source provider cleanup failed: %s",
                        cleanup_err,
                    )

    async def _select_converted_main_artifact(
        self,
        *,
        provider: DocumentSourceProvider,
        request: ProviderConversionExecutionRequest,
        conversion_result: SourceConversionResult,
        curator_token: str,
    ) -> tuple[SourceArtifact | None, tuple[str, ...]]:
        if not _conversion_result_has_main_text(
            conversion_result,
            provider_id=str(request.source_provenance.get("provider") or ""),
        ):
            return None, ()
        artifacts = await provider.list_artifacts(
            request.reference,
            request_bearer_token=curator_token,
        )
        selected, ambiguous_count = _select_preferred_main_markdown(
            artifacts,
            reference=request.reference,
        )
        if ambiguous_count > 1:
            raise RuntimeError("Provider conversion produced multiple equally preferred Markdown artifacts")
        return selected, _dedupe_strings(
            (
                *_figure_metadata_artifact_ids_from_conversion_result(conversion_result),
                *_figure_metadata_artifact_ids_from_artifacts(
                    provider=provider,
                    artifacts=artifacts,
                    source_artifact_id=request.source_artifact_id,
                ),
            )
        )

    async def _execute_provider_markdown_unbounded(
        self,
        request: ProviderMarkdownExecutionRequest,
        *,
        skip_replay_guard: bool = False,
    ) -> None:
        """Download provider Markdown and ingest it through the local pipeline."""
        if pdf_job_service.is_cancel_requested(job_id=request.job_id):
            await self._handle_pre_start_cancellation(request)
            return

        existing_job = None
        if not skip_replay_guard:
            existing_job = pdf_job_service.get_job_by_id(
                job_id=request.job_id,
                reconcile_stale=False,
            )
        if existing_job and existing_job.status in _NON_PENDING_JOB_STATUSES:
            logger.info(
                "Skipping replayed provider Markdown execution for job %s with durable status %s",
                request.job_id,
                existing_job.status,
            )
            return

        provider: DocumentSourceProvider | None = None
        try:
            curator_token = (request.curator_token or "").strip()
            if not curator_token:
                raise RuntimeError("Provider Markdown download requires a request curator token")

            pdf_job_service.update_progress(
                job_id=request.job_id,
                stage=ProcessingStage.UPLOAD.value,
                progress_percentage=10,
                message="Downloading converted Markdown",
                status=PdfJobStatus.RUNNING.value,
            )
            provider = self._document_source_provider_factory()
            markdown_bytes = await provider.download_artifact(
                request.converted_artifact_id,
                request_bearer_token=curator_token,
            )
            if pdf_job_service.is_cancel_requested(job_id=request.job_id):
                raise PDFCancellationError("Processing cancelled by user request")
            markdown = markdown_bytes.decode("utf-8")
            figure_metadata_artifact_ids = await _figure_metadata_artifact_ids_for_markdown_request(
                provider=provider,
                request=request,
                request_bearer_token=curator_token,
            )
            figure_metadata_entries = await _download_provider_figure_metadata_entries(
                provider=provider,
                artifact_ids=figure_metadata_artifact_ids,
                request_bearer_token=curator_token,
            )

            pdf_job_service.update_progress(
                job_id=request.job_id,
                stage=ProcessingStage.PARSING.value,
                progress_percentage=25,
                message="Ingesting converted Markdown",
                status=PdfJobStatus.RUNNING.value,
            )
            if pdf_job_service.is_cancel_requested(job_id=request.job_id):
                raise PDFCancellationError("Processing cancelled by user request")
            connection = get_connection()
            await self._provider_markdown_ingestion(
                ProviderMarkdownIngestionRequest(
                    document_id=request.document_id,
                    user_id=request.user_id,
                    document_owner_user_id=request.owner_user_id,
                    markdown=markdown,
                    source_provenance=request.source_provenance,
                    filename=request.filename,
                    provider_figure_metadata=figure_metadata_entries,
                ),
                weaviate_client=connection,
            )
            if pdf_job_service.is_cancel_requested(job_id=request.job_id):
                raise PDFCancellationError("Processing cancelled by user request")
            try:
                await self.pipeline_tracker.track_pipeline_progress(
                    request.document_id,
                    ProcessingStage.COMPLETED,
                    progress_percentage=100,
                    message="Processing completed",
                )
            except Exception as tracker_err:
                logger.warning(
                    "Failed to sync in-memory tracker for completed provider Markdown document=%s: %s",
                    request.document_id,
                    tracker_err,
                )
            pdf_job_service.mark_completed(job_id=request.job_id, message="Processing completed")
        except Exception as exc:
            logger.error(
                "Error ingesting provider Markdown document %s: %s",
                request.document_id,
                exc,
                exc_info=True,
            )
            if not isinstance(exc, PDFCancellationError):
                self._report_execution_failure(
                    exc,
                    task_name="pdf_jobs.execute_provider_markdown",
                    request=request,
                    failure_stage="provider_markdown",
                )
            try:
                await update_document_status(
                    request.document_id,
                    request.user_id,
                    ProcessingStatus.FAILED.value,
                )
            except Exception as status_err:
                logger.warning(
                    "Failed to sync document status for failed provider Markdown document=%s: %s",
                    request.document_id,
                    status_err,
                )
            if isinstance(exc, PDFCancellationError):
                pdf_job_service.mark_cancelled(job_id=request.job_id, reason=str(exc))
            else:
                await self._sync_provider_markdown_sql_failure(request, exc)
                pdf_job_service.mark_failed(
                    job_id=request.job_id,
                    message=str(exc),
                    stage=ProcessingStage.FAILED.value,
                )
        finally:
            if provider is not None:
                try:
                    await provider.aclose()
                except Exception as cleanup_err:
                    logger.warning(
                        "Best-effort document-source provider cleanup failed: %s",
                        cleanup_err,
                    )

    async def _sync_provider_markdown_sql_failure(
        self,
        request: ProviderMarkdownExecutionRequest,
        exc: Exception,
    ) -> None:
        if isinstance(exc, DocumentSourceIngestionError):
            return
        try:
            from src.lib.document_sources.ingestion import _sync_sql_document_status

            await _sync_sql_document_status(
                request.document_id,
                user_id=request.user_id,
                owner_user_id=request.owner_user_id,
                status="failed",
                error_message=str(exc),
            )
        except Exception as sync_err:
            logger.warning(
                "Failed to sync SQL provider Markdown failure for document=%s: %s",
                request.document_id,
                sync_err,
            )

    @staticmethod
    def _report_execution_failure(
        exc: Exception,
        *,
        task_name: str,
        request: Any,
        failure_stage: str,
    ) -> bool:
        return report_background_task_exception(
            exc,
            task_name=task_name,
            tags={
                "component": "pdf_jobs",
                "document_id": getattr(request, "document_id", None),
                "job_id": getattr(request, "job_id", None),
                "failure_stage": failure_stage,
            },
        )

    async def _sync_provider_conversion_sql_failure(
        self,
        request: ProviderConversionExecutionRequest,
        exc: Exception,
    ) -> None:
        try:
            from src.lib.document_sources.ingestion import _sync_sql_document_status

            await _sync_sql_document_status(
                request.document_id,
                user_id=request.user_id,
                owner_user_id=request.owner_user_id,
                status="failed",
                error_message=str(exc),
            )
        except Exception as sync_err:
            logger.warning(
                "Failed to sync SQL provider conversion failure for document=%s: %s",
                request.document_id,
                sync_err,
            )

    async def _sync_provider_conversion_sql_selection(
        self,
        request: ProviderConversionExecutionRequest,
        converted_artifact: SourceArtifact,
    ) -> None:
        try:
            from uuid import UUID

            from sqlalchemy import select

            from src.models.sql.database import SessionLocal
            from src.models.sql.pdf_document import PDFDocument as ViewerPDFDocument

            source_provenance = _source_provenance_with_converted_artifact(
                request.source_provenance,
                converted_artifact,
            )
            session = SessionLocal()
            try:
                document = session.execute(
                    select(ViewerPDFDocument).where(
                        ViewerPDFDocument.id == UUID(str(request.document_id)),
                        ViewerPDFDocument.user_id == request.owner_user_id,
                    )
                ).scalar_one_or_none()
                if document is None:
                    return
                document.source_provider_converted_artifact_id = source_provenance.get(
                    "converted_artifact_id"
                )
                document.source_file_class = source_provenance.get("file_class")
                document.source_file_extension = source_provenance.get("file_extension")
                document.source_artifact_status = source_provenance.get("artifact_status")
                document.source_import_status = "pending"
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception as sync_err:
            logger.warning(
                "Failed to sync SQL provider conversion selection for document=%s: %s",
                request.document_id,
                sync_err,
            )

    def _default_orchestrator_factory(
        self,
        connection: Any,
        tracker: Any,
    ) -> DocumentPipelineOrchestrator:
        return DocumentPipelineOrchestrator(
            weaviate_client=connection,
            tracker=tracker,
        )

    async def _handle_pre_start_cancellation(
        self,
        request: UploadExecutionRequest | ProviderMarkdownExecutionRequest | ProviderConversionExecutionRequest,
    ) -> None:
        cancellation_message = "Cancelled before processing started"
        pdf_job_service.mark_cancelled(
            job_id=request.job_id,
            reason=cancellation_message,
        )

        try:
            await update_document_status(request.document_id, request.user_id, ProcessingStatus.FAILED.value)
        except Exception as status_err:
            logger.warning(
                "Failed to sync document status for pre-start cancellation document=%s: %s",
                request.document_id,
                status_err,
            )

        try:
            await self.pipeline_tracker.track_pipeline_progress(
                request.document_id,
                ProcessingStage.FAILED,
                message=cancellation_message,
            )
        except Exception as tracker_err:
            logger.warning(
                "Failed to update in-memory tracker for pre-start cancellation document=%s: %s",
                request.document_id,
                tracker_err,
            )

    async def _finalize_pipeline_result(
        self,
        *,
        request: UploadExecutionRequest,
        success: bool,
        cancelled: bool,
        error_message: Optional[str],
    ) -> None:
        if cancelled:
            try:
                await self.pipeline_tracker.track_pipeline_progress(
                    request.document_id,
                    ProcessingStage.FAILED,
                    message=error_message or "Cancelled by user",
                )
            except Exception as tracker_err:
                logger.warning(
                    "Failed to sync in-memory tracker for cancelled document=%s: %s",
                    request.document_id,
                    tracker_err,
                )
            pdf_job_service.mark_cancelled(
                job_id=request.job_id,
                reason=error_message or "Cancelled by user",
            )
            return

        if success:
            try:
                await self.pipeline_tracker.track_pipeline_progress(
                    request.document_id,
                    ProcessingStage.COMPLETED,
                    progress_percentage=100,
                    message="Processing completed",
                )
            except Exception as tracker_err:
                logger.warning(
                    "Failed to sync in-memory tracker for completed document=%s: %s",
                    request.document_id,
                    tracker_err,
                )
            pdf_job_service.mark_completed(job_id=request.job_id, message="Processing completed")
            return

        try:
            await self.pipeline_tracker.track_pipeline_progress(
                request.document_id,
                ProcessingStage.FAILED,
                message=error_message or "Processing failed",
            )
        except Exception as tracker_err:
            logger.warning(
                "Failed to sync in-memory tracker for failed document=%s: %s",
                request.document_id,
                tracker_err,
            )
        pdf_job_service.mark_failed(
            job_id=request.job_id,
            message=error_message or "Processing failed",
            stage=ProcessingStage.FAILED.value,
        )


def _conversion_result_has_main_text(
    result: SourceConversionResult,
    *,
    provider_id: str,
) -> bool:
    if provider_id != "abc_literature":
        return result.status in {
            SourceConversionStatus.CONVERTED,
            SourceConversionStatus.RUNNING,
        } and bool(result.converted_classes or result.per_file_progress)

    if "converted_merged_main" in result.converted_classes:
        return True
    for progress in result.per_file_progress:
        converted = progress.get("converted")
        if not isinstance(converted, dict):
            continue
        if converted.get("file_class") == "converted_merged_main":
            return True
    for mod_status in result.per_mod_status:
        if mod_status.get("main_converted") is True:
            return True
    return False


def _select_preferred_main_markdown(
    artifacts: list[SourceArtifact],
    *,
    reference: str,
) -> tuple[SourceArtifact | None, int]:
    reference_key = str(reference or "").strip()
    candidates = [
        artifact
        for artifact in artifacts
        if artifact.role is SourceArtifactRole.CONVERTED_TEXT
        and artifact.artifact_format is SourceArtifactFormat.MARKDOWN
        and artifact.status in {SourceArtifactStatus.AVAILABLE, SourceArtifactStatus.UNKNOWN}
        and _is_canonical_main_markdown_artifact(artifact)
        and (
            not reference_key
            or artifact.reference_curie == reference_key
            or artifact.reference_id == reference_key
        )
    ]
    if not candidates:
        return None, 0
    ranked = sorted(
        ((_main_markdown_sort_key(artifact), artifact) for artifact in candidates),
        key=lambda item: (
            item[0],
            str(item[1].display_name or "").strip().lower(),
            item[1].artifact_id,
        ),
    )
    best_rank = ranked[0][0]
    best = [artifact for rank, artifact in ranked if rank == best_rank]
    if len(best) > 1:
        return None, len(best)
    return best[0], 1


def _is_canonical_main_markdown_artifact(artifact: SourceArtifact) -> bool:
    file_class = str(artifact.metadata.get("file_class") or "").strip().lower()
    if artifact.provider == "abc_literature":
        return file_class == "converted_merged_main" and not _artifact_looks_tei(artifact)
    return True


def _artifact_looks_tei(artifact: SourceArtifact) -> bool:
    file_class = str(artifact.metadata.get("file_class") or "").strip().lower()
    display_name = str(artifact.display_name or "").strip().lower()
    return "tei" in file_class or "_tei" in display_name or display_name.endswith("tei.md")


def _main_markdown_sort_key(artifact: SourceArtifact) -> tuple[int]:
    display_name = str(artifact.display_name or "").strip().lower()
    is_nxml = display_name.endswith("_nxml") or display_name.endswith("_nxml.md")
    is_tei = display_name.endswith("_tei") or display_name.endswith("_tei.md")
    return (0 if is_nxml else 2 if is_tei else 1,)


def _conversion_job_metadata(result: SourceConversionResult) -> dict[str, Any]:
    document_source: dict[str, Any] = {
        "conversion_status": result.status.value,
        "converted_classes": list(result.converted_classes),
        "per_file_progress": [_json_safe_mapping(item) for item in result.per_file_progress],
        "per_mod_status": [_json_safe_mapping(item) for item in result.per_mod_status],
    }
    if result.job_id:
        document_source["conversion_job_id"] = result.job_id
    if result.error_message:
        document_source["conversion_error_message"] = result.error_message
    return {"document_source": document_source}


def _json_safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, raw_value in value.items():
        if isinstance(raw_value, Mapping):
            safe[str(key)] = _json_safe_mapping(raw_value)
        elif isinstance(raw_value, list | tuple):
            safe[str(key)] = [
                _json_safe_mapping(item) if isinstance(item, Mapping) else item
                for item in raw_value
            ]
        else:
            safe[str(key)] = raw_value
    return safe


def _conversion_progress_percentage(result: SourceConversionResult) -> int:
    if result.status is SourceConversionStatus.RUNNING:
        if "converted_merged_main" in result.converted_classes:
            return 35
        return 20
    if result.status is SourceConversionStatus.CONVERTED:
        return 35
    if result.status in {SourceConversionStatus.FAILED, SourceConversionStatus.NO_SOURCES}:
        return 100
    return 15


def _conversion_progress_message(result: SourceConversionResult) -> str:
    if "converted_merged_main" in result.converted_classes:
        return "ABC Literature main text is ready; importing converted Markdown"
    if result.status is SourceConversionStatus.RUNNING:
        pending_count = _count_progress_status(result, "pending")
        if pending_count:
            return f"ABC Literature conversion running ({pending_count} file{'s' if pending_count != 1 else ''} pending)"
        return "ABC Literature conversion running"
    if result.status is SourceConversionStatus.CONVERTED:
        return "ABC Literature conversion completed; locating converted Markdown"
    if result.status is SourceConversionStatus.FAILED:
        return _conversion_failure_message(result)
    if result.status is SourceConversionStatus.NO_SOURCES:
        return "ABC Literature found no convertible source files"
    return "Waiting for ABC Literature conversion status"


def _count_progress_status(result: SourceConversionResult, status: str) -> int:
    return sum(
        1
        for progress in result.per_file_progress
        if str(progress.get("status") or "").strip().lower() == status
    )


def _conversion_failure_message(result: SourceConversionResult) -> str:
    if result.error_message:
        return result.error_message
    failures = []
    for progress in result.per_file_progress:
        if str(progress.get("status") or "").strip().lower() != "failed":
            continue
        source = progress.get("source")
        source_name = None
        if isinstance(source, Mapping):
            source_name = source.get("display_name")
        error = progress.get("error")
        if source_name and error:
            failures.append(f"{source_name}: {error}")
        elif error:
            failures.append(str(error))
    if failures:
        return "; ".join(failures)
    return "ABC Literature conversion failed"


def _source_provenance_with_converted_artifact(
    source_provenance: Mapping[str, Any],
    converted_artifact: SourceArtifact,
) -> dict[str, Any]:
    updated = dict(source_provenance)
    updated["converted_artifact_id"] = converted_artifact.artifact_id
    updated["file_class"] = (
        converted_artifact.metadata.get("file_class")
        or getattr(converted_artifact.role, "value", str(converted_artifact.role))
    )
    updated["file_extension"] = (
        converted_artifact.metadata.get("file_extension")
        or getattr(converted_artifact.artifact_format, "value", str(converted_artifact.artifact_format))
    )
    updated["artifact_status"] = getattr(
        converted_artifact.status,
        "value",
        str(converted_artifact.status),
    )
    return updated


def _figure_metadata_artifact_ids_from_artifacts(
    *,
    provider: DocumentSourceProvider,
    artifacts: list[SourceArtifact],
    source_artifact_id: str,
) -> tuple[str, ...]:
    from src.lib.document_sources.import_selection import (
        provider_metadata_artifacts_for_source,
    )

    source_artifact = next(
        (
            artifact
            for artifact in artifacts
            if artifact.artifact_id == source_artifact_id
        ),
        None,
    )
    if source_artifact is None:
        return ()
    return tuple(
        artifact.artifact_id
        for artifact in provider_metadata_artifacts_for_source(
            provider=provider,
            source_artifact=source_artifact,
            artifacts=artifacts,
        )
    )


async def _figure_metadata_artifact_ids_for_markdown_request(
    *,
    provider: DocumentSourceProvider,
    request: ProviderMarkdownExecutionRequest,
    request_bearer_token: str,
) -> tuple[str, ...]:
    artifact_ids = list(request.figure_metadata_artifact_ids)
    source_provenance = request.source_provenance
    source_artifact_id = _first_non_empty_string(
        source_provenance.get("pdf_artifact_id"),
        source_provenance.get("source_file_id"),
    )
    reference = _first_non_empty_string(
        source_provenance.get("reference_curie"),
        source_provenance.get("reference_id"),
    )
    if source_artifact_id is None or reference is None:
        return _dedupe_strings(artifact_ids)

    if artifact_ids:
        return _dedupe_strings(artifact_ids)

    if not callable(getattr(provider, "provider_metadata_artifacts_for_source", None)):
        return _dedupe_strings(artifact_ids)

    try:
        artifacts = await provider.list_artifacts(
            reference,
            request_bearer_token=request_bearer_token,
        )
    except Exception as exc:
        raise RuntimeError("Provider figure metadata discovery failed") from exc

    return _dedupe_strings(
        (
            *artifact_ids,
            *_figure_metadata_artifact_ids_from_artifacts(
                provider=provider,
                artifacts=artifacts,
                source_artifact_id=source_artifact_id,
            ),
        )
    )


def _figure_metadata_artifact_ids_from_conversion_result(
    result: SourceConversionResult,
) -> tuple[str, ...]:
    artifact_ids: list[str] = []
    for progress in result.per_file_progress:
        artifact_ids.extend(_metadata_referencefile_ids_from_mapping(progress))
    for status in result.per_mod_status:
        artifact_ids.extend(_metadata_referencefile_ids_from_mapping(status))
    return _dedupe_strings(artifact_ids)


def _metadata_referencefile_ids_from_mapping(payload: Mapping[str, Any]) -> list[str]:
    artifact_ids: list[str] = []
    value = payload.get("metadata_referencefile_id")
    normalized = _non_empty_string(value)
    if normalized is not None:
        artifact_ids.append(normalized)

    for nested_value in payload.values():
        if isinstance(nested_value, Mapping):
            artifact_ids.extend(_metadata_referencefile_ids_from_mapping(nested_value))
        elif isinstance(nested_value, list | tuple):
            for item in nested_value:
                if isinstance(item, Mapping):
                    artifact_ids.extend(_metadata_referencefile_ids_from_mapping(item))
    return artifact_ids


def _dedupe_strings(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _non_empty_string(value)
        if normalized is None or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return tuple(deduped)


def _non_empty_string(value: object) -> str | None:
    if isinstance(value, str | int) and str(value).strip():
        return str(value).strip()
    return None


def _first_non_empty_string(*values: object) -> str | None:
    for value in values:
        normalized = _non_empty_string(value)
        if normalized is not None:
            return normalized
    return None


async def _download_provider_figure_metadata_entries(
    *,
    provider: DocumentSourceProvider,
    artifact_ids: tuple[str, ...],
    request_bearer_token: str,
) -> tuple[Mapping[str, Any], ...]:
    if not artifact_ids:
        return ()

    from src.lib.document_sources.figure_metadata import (
        normalize_provider_figure_metadata_sidecar,
    )

    entries: list[Mapping[str, Any]] = []
    for artifact_id in artifact_ids:
        raw = await provider.download_artifact(
            artifact_id,
            request_bearer_token=request_bearer_token,
        )
        entries.append(
            normalize_provider_figure_metadata_sidecar(
                raw,
                metadata_artifact_id=artifact_id,
            )
        )
    return tuple(entries)
