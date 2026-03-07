"""Runtime service for upload execution orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import BackgroundTasks

from src.lib.exceptions import PDFCancellationError
from src.lib.pipeline.orchestrator import DocumentPipelineOrchestrator
from src.lib.pipeline.tracker import PipelineTracker
from src.lib.weaviate_helpers import get_connection
from src.lib.weaviate_client.documents import update_document_status
from src.models.document import ProcessingStatus
from src.models.pipeline import ProcessingStage
from src.models.sql.pdf_processing_job import PdfJobStatus

from . import service as pdf_job_service

logger = logging.getLogger(__name__)


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


class UploadExecutionService:
    """Dispatch and execute the upload processing pipeline lifecycle."""

    def __init__(
        self,
        *,
        pipeline_tracker: PipelineTracker,
        orchestrator_factory: Optional[Callable[[Any, PipelineTracker], DocumentPipelineOrchestrator]] = None,
    ) -> None:
        self.pipeline_tracker = pipeline_tracker
        self._orchestrator_factory = orchestrator_factory or self._default_orchestrator_factory

    async def dispatch_upload_execution(
        self,
        *,
        background_tasks: BackgroundTasks,
        request: UploadExecutionRequest,
    ) -> None:
        """Prime tracking and queue upload execution on FastAPI background tasks."""
        await self.pipeline_tracker.track_pipeline_progress(request.document_id, ProcessingStage.UPLOAD)
        background_tasks.add_task(self.execute_upload, request)

    async def execute_upload(self, request: UploadExecutionRequest) -> None:
        """Run upload orchestration and persist durable job transitions."""
        if pdf_job_service.is_cancel_requested(job_id=request.job_id):
            await self._handle_pre_start_cancellation(request)
            return

        existing_job = pdf_job_service.get_job_by_id(job_id=request.job_id, reconcile_stale=False)
        if existing_job and existing_job.status in {
            PdfJobStatus.RUNNING.value,
            PdfJobStatus.CANCEL_REQUESTED.value,
            PdfJobStatus.COMPLETED.value,
            PdfJobStatus.FAILED.value,
            PdfJobStatus.CANCELLED.value,
        }:
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

    def _default_orchestrator_factory(
        self,
        connection: Any,
        tracker: PipelineTracker,
    ) -> DocumentPipelineOrchestrator:
        return DocumentPipelineOrchestrator(
            weaviate_client=connection,
            tracker=tracker,
        )

    async def _handle_pre_start_cancellation(self, request: UploadExecutionRequest) -> None:
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
