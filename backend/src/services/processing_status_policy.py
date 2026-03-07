"""Shared processing status policy used across backend APIs."""

from typing import Any

from ..models.document import ProcessingStatus
from ..models.pipeline import ProcessingStage
from ..models.sql.pdf_processing_job import PdfJobStatus


PROCESSING_STATUS_VALUES = frozenset(status.value for status in ProcessingStatus)
PIPELINE_STAGE_TO_PROCESSING_STATUS = {
    ProcessingStage.PENDING.value: ProcessingStatus.PENDING.value,
    ProcessingStage.UPLOAD.value: ProcessingStatus.PROCESSING.value,
    ProcessingStage.PARSING.value: ProcessingStatus.PARSING.value,
    ProcessingStage.CHUNKING.value: ProcessingStatus.CHUNKING.value,
    ProcessingStage.EMBEDDING.value: ProcessingStatus.EMBEDDING.value,
    ProcessingStage.STORING.value: ProcessingStatus.STORING.value,
    ProcessingStage.COMPLETED.value: ProcessingStatus.COMPLETED.value,
    ProcessingStage.FAILED.value: ProcessingStatus.FAILED.value,
}
PDF_JOB_STATUS_TO_PROCESSING_STATUS = {
    PdfJobStatus.PENDING.value: ProcessingStatus.PENDING.value,
    PdfJobStatus.RUNNING.value: ProcessingStatus.PROCESSING.value,
    PdfJobStatus.CANCEL_REQUESTED.value: ProcessingStatus.PROCESSING.value,
    PdfJobStatus.COMPLETED.value: ProcessingStatus.COMPLETED.value,
    PdfJobStatus.CANCELLED.value: ProcessingStatus.FAILED.value,
    PdfJobStatus.FAILED.value: ProcessingStatus.FAILED.value,
}
ACTIVE_PROCESSING_STATUSES = frozenset(
    {
        ProcessingStatus.PROCESSING.value,
        ProcessingStatus.PARSING.value,
        ProcessingStatus.CHUNKING.value,
        ProcessingStatus.EMBEDDING.value,
        ProcessingStatus.STORING.value,
    }
)
TERMINAL_PROCESSING_STATUSES = frozenset(
    {
        ProcessingStatus.COMPLETED.value,
        ProcessingStatus.FAILED.value,
    }
)
ACTIVE_PDF_JOB_STATUSES = frozenset(
    {
        PdfJobStatus.PENDING.value,
        PdfJobStatus.RUNNING.value,
        PdfJobStatus.CANCEL_REQUESTED.value,
    }
)
TERMINAL_PDF_JOB_STATUSES = frozenset(
    {
        PdfJobStatus.COMPLETED.value,
        PdfJobStatus.FAILED.value,
        PdfJobStatus.CANCELLED.value,
    }
)


def normalize_processing_status(value: Any) -> str:
    """Normalize raw status values to the API enum surface."""
    status = str(value or "").strip().lower()
    if status in PROCESSING_STATUS_VALUES:
        return status
    return ProcessingStatus.PENDING.value


def stage_value(stage: Any, *, default: str = "") -> str:
    """Return a normalized stage label for enum-or-string values."""
    value = getattr(stage, "value", stage)
    normalized = str(value or "").strip().lower()
    return normalized or default


def pipeline_stage_value(pipeline_status: Any) -> str:
    """Return a normalized current-stage value from a pipeline status payload."""
    if not pipeline_status:
        return ProcessingStage.PENDING.value
    return stage_value(getattr(pipeline_status, "current_stage", None), default=ProcessingStage.PENDING.value)


def processing_status_for_pipeline_stage(stage: Any) -> str:
    """Map a pipeline stage to the public processing status surface."""
    stage_str = stage_value(stage, default=ProcessingStage.PENDING.value)
    return PIPELINE_STAGE_TO_PROCESSING_STATUS.get(stage_str, normalize_processing_status(stage_str))


def is_pipeline_status_active(pipeline_status: Any) -> bool:
    """Return whether the pipeline tracker still indicates active work."""
    if not pipeline_status:
        return False
    return processing_status_for_pipeline_stage(
        getattr(pipeline_status, "current_stage", None)
    ) in ACTIVE_PROCESSING_STATUSES


def is_pipeline_status_terminal(pipeline_status: Any) -> bool:
    """Return whether the pipeline tracker is at a terminal stage."""
    if not pipeline_status:
        return False
    return processing_status_for_pipeline_stage(
        getattr(pipeline_status, "current_stage", None)
    ) in TERMINAL_PROCESSING_STATUSES
