"""PDF processing jobs helpers."""

from .service import (
    create_job,
    get_job,
    get_latest_job_for_document,
    is_cancel_requested,
    list_jobs,
    mark_cancelled,
    mark_completed,
    mark_failed,
    request_cancel,
    set_process_id,
    update_progress,
)
from .upload_execution_service import (
    UploadExecutionRequest,
    UploadExecutionService,
    normalize_pipeline_result,
)

__all__ = [
    "create_job",
    "get_job",
    "get_latest_job_for_document",
    "is_cancel_requested",
    "list_jobs",
    "mark_cancelled",
    "mark_completed",
    "mark_failed",
    "request_cancel",
    "set_process_id",
    "update_progress",
    "UploadExecutionRequest",
    "UploadExecutionService",
    "normalize_pipeline_result",
]
