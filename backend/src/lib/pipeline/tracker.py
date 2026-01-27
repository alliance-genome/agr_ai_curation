"""Pipeline status tracking for document processing."""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
import asyncio

from src.models.pipeline import (
    ProcessingStage,
    PipelineStatus,
    StageResult,
    ProcessingError
)

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    """Configuration for stage retry logic."""
    max_retries: int = 3
    retry_delay_seconds: int = 5
    exponential_backoff: bool = True
    retryable_stages: List[ProcessingStage] = field(default_factory=lambda: [
        ProcessingStage.PARSING,
        ProcessingStage.EMBEDDING,
        ProcessingStage.STORING
    ])


class PipelineTracker:
    """Tracks pipeline execution status and progress."""

    def __init__(self, retry_config: Optional[RetryConfig] = None):
        """Initialize pipeline tracker.

        Args:
            retry_config: Retry configuration for failed stages
        """
        self.retry_config = retry_config or RetryConfig()
        # In-memory storage for demo (would use database in production)
        self.pipeline_states: Dict[str, PipelineStatus] = {}
        self.stage_results: Dict[str, List[StageResult]] = {}
        self.processing_errors: Dict[str, List[ProcessingError]] = {}

    async def track_pipeline_progress(
        self,
        document_id: str,
        stage: ProcessingStage,
        progress_percentage: Optional[int] = None,
        message: Optional[str] = None
    ) -> PipelineStatus:
        """Track progress of pipeline execution.

        Args:
            document_id: Document UUID
            stage: Current processing stage
            progress_percentage: Optional progress percentage
            message: Optional status message

        Returns:
            Updated PipelineStatus
        """
        logger.info(f"Tracking progress for document {document_id} at stage {stage.value}")

        # Get or create pipeline status
        if document_id not in self.pipeline_states:
            self.pipeline_states[document_id] = PipelineStatus(
                document_id=document_id,
                current_stage=stage,
                started_at=datetime.now(),
                updated_at=datetime.now(),
                progress_percentage=progress_percentage or 0,
                message=message
            )
        else:
            # Update existing status
            status = self.pipeline_states[document_id]
            status.current_stage = stage
            status.updated_at = datetime.now()
            if progress_percentage is not None:
                status.progress_percentage = progress_percentage
            if message:
                status.message = message

            # Calculate automatic progress based on stage when not explicitly provided
            if progress_percentage is None:
                stage_progress = {
                    ProcessingStage.PENDING: 0,
                    ProcessingStage.UPLOAD: 10,
                    ProcessingStage.PARSING: 30,
                    ProcessingStage.CHUNKING: 50,
                    ProcessingStage.EMBEDDING: 70,
                    ProcessingStage.STORING: 85,
                    ProcessingStage.COMPLETED: 100,
                    ProcessingStage.FAILED: status.progress_percentage,
                }
                status.progress_percentage = stage_progress.get(stage, status.progress_percentage)

        return self.pipeline_states[document_id]

    async def get_pipeline_status(self, document_id: str) -> Optional[PipelineStatus]:
        """Get current pipeline status for a document.

        Args:
            document_id: Document UUID

        Returns:
            PipelineStatus or None if not found
        """
        status = self.pipeline_states.get(document_id)

        if status:
            # Calculate processing duration
            if status.completed_at:
                duration = (status.completed_at - status.started_at).total_seconds()
            else:
                duration = (datetime.now() - status.started_at).total_seconds()

            status.processing_time_seconds = duration

            # Add stage results if available
            if document_id in self.stage_results:
                status.stage_results = self.stage_results[document_id]

            # Add error count
            if document_id in self.processing_errors:
                status.error_count = len(self.processing_errors[document_id])

        return status

    async def handle_pipeline_failure(
        self,
        document_id: str,
        error: Exception,
        stage: Optional[ProcessingStage] = None
    ) -> ProcessingError:
        """Handle pipeline failure and record error details.

        Args:
            document_id: Document UUID
            error: The exception that occurred
            stage: Stage where failure occurred

        Returns:
            ProcessingError object
        """
        logger.error(f"Pipeline failure for document {document_id}: {str(error)}")

        # Determine current stage if not provided
        if stage is None and document_id in self.pipeline_states:
            stage = self.pipeline_states[document_id].current_stage
        elif stage is None:
            stage = ProcessingStage.FAILED

        # Create error record
        processing_error = ProcessingError(
            stage=stage,
            error_code=f"{stage.value.upper()}_ERROR",
            error_message=str(error),
            timestamp=datetime.now(),
            document_id=document_id,
            retry_count=0,
            is_retryable=stage in self.retry_config.retryable_stages
        )

        # Store error
        if document_id not in self.processing_errors:
            self.processing_errors[document_id] = []
        self.processing_errors[document_id].append(processing_error)

        # Update pipeline status
        if document_id in self.pipeline_states:
            status = self.pipeline_states[document_id]
            status.current_stage = ProcessingStage.FAILED
            status.updated_at = datetime.now()
            status.message = f"Failed at {stage.value}: {str(error)}"
            status.error_count = len(self.processing_errors[document_id])

        return processing_error

    async def retry_failed_stage(
        self,
        document_id: str,
        stage: ProcessingStage
    ) -> Dict[str, Any]:
        """Retry a failed processing stage.

        Args:
            document_id: Document UUID
            stage: Stage to retry

        Returns:
            Retry result dictionary
        """
        logger.info(f"Attempting retry for document {document_id}, stage {stage.value}")

        # Check if stage is retryable
        if stage not in self.retry_config.retryable_stages:
            return {
                "success": False,
                "message": f"Stage {stage.value} is not retryable"
            }

        # Get error history
        errors = self.processing_errors.get(document_id, [])
        stage_errors = [e for e in errors if e.stage == stage]

        if not stage_errors:
            return {
                "success": False,
                "message": f"No errors found for stage {stage.value}"
            }

        latest_error = stage_errors[-1]
        retry_count = latest_error.retry_count

        # Check retry limit
        if retry_count >= self.retry_config.max_retries:
            return {
                "success": False,
                "message": f"Maximum retries ({self.retry_config.max_retries}) exceeded"
            }

        # Calculate retry delay
        if self.retry_config.exponential_backoff:
            delay = self.retry_config.retry_delay_seconds * (2 ** retry_count)
        else:
            delay = self.retry_config.retry_delay_seconds

        # Wait before retry
        await asyncio.sleep(delay)

        # Update retry count
        latest_error.retry_count += 1

        # Update pipeline status
        if document_id in self.pipeline_states:
            status = self.pipeline_states[document_id]
            status.current_stage = stage
            status.updated_at = datetime.now()
            status.message = f"Retrying {stage.value} (attempt {retry_count + 1})"

        return {
            "success": True,
            "message": f"Ready to retry {stage.value}",
            "retry_attempt": retry_count + 1,
            "delay_seconds": delay
        }

    def record_stage_result(
        self,
        document_id: str,
        stage: ProcessingStage,
        success: bool,
        duration_seconds: float,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> StageResult:
        """Record the result of a processing stage.

        Args:
            document_id: Document UUID
            stage: Completed stage
            success: Whether stage succeeded
            duration_seconds: Stage execution time
            message: Optional result message
            metadata: Optional additional metadata

        Returns:
            StageResult object
        """
        result = StageResult(
            stage=stage,
            success=success,
            started_at=datetime.now() - timedelta(seconds=duration_seconds),
            completed_at=datetime.now(),
            duration_seconds=duration_seconds,
            message=message,
            metadata=metadata
        )

        # Store result
        if document_id not in self.stage_results:
            self.stage_results[document_id] = []
        self.stage_results[document_id].append(result)

        logger.info(f"Recorded {'successful' if success else 'failed'} result for stage {stage.value}")
        return result

    def get_pipeline_statistics(self) -> Dict[str, Any]:
        """Get overall pipeline statistics.

        Returns:
            Statistics dictionary
        """
        total_pipelines = len(self.pipeline_states)
        completed = sum(1 for s in self.pipeline_states.values()
                       if s.current_stage == ProcessingStage.COMPLETED)
        failed = sum(1 for s in self.pipeline_states.values()
                    if s.current_stage == ProcessingStage.FAILED)
        in_progress = total_pipelines - completed - failed

        # Calculate average processing time
        processing_times = []
        for status in self.pipeline_states.values():
            if status.completed_at:
                duration = (status.completed_at - status.started_at).total_seconds()
                processing_times.append(duration)

        avg_processing_time = (
            sum(processing_times) / len(processing_times)
            if processing_times else 0
        )

        # Count errors by stage
        errors_by_stage = {}
        for errors in self.processing_errors.values():
            for error in errors:
                stage_name = error.stage.value
                errors_by_stage[stage_name] = errors_by_stage.get(stage_name, 0) + 1

        return {
            "total_pipelines": total_pipelines,
            "completed": completed,
            "failed": failed,
            "in_progress": in_progress,
            "average_processing_seconds": avg_processing_time,
            "errors_by_stage": errors_by_stage,
            "total_errors": sum(len(e) for e in self.processing_errors.values())
        }

    def clear_completed_pipelines(self, older_than_hours: int = 24) -> int:
        """Clear completed pipeline states older than specified hours.

        Args:
            older_than_hours: Clear pipelines completed more than this many hours ago

        Returns:
            Number of cleared pipelines
        """
        cutoff_time = datetime.now() - timedelta(hours=older_than_hours)
        cleared_count = 0

        documents_to_clear = []
        for doc_id, status in self.pipeline_states.items():
            if (status.current_stage == ProcessingStage.COMPLETED and
                status.completed_at and
                status.completed_at < cutoff_time):
                documents_to_clear.append(doc_id)

        for doc_id in documents_to_clear:
            del self.pipeline_states[doc_id]
            if doc_id in self.stage_results:
                del self.stage_results[doc_id]
            if doc_id in self.processing_errors:
                del self.processing_errors[doc_id]
            cleared_count += 1

        if cleared_count > 0:
            logger.info(f"Cleared {cleared_count} completed pipelines")

        return cleared_count
