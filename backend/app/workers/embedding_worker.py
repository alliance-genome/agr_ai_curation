"""Worker pool responsible for executing embedding jobs with rate limiting."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.models import EmbeddingJob, JobType
from lib.job_queue import JobQueue

LOGGER = logging.getLogger(__name__)


class RateLimitError(Exception):
    """Raised when an external embedding service is rate limited."""


SleepFn = Callable[[float], None]


@dataclass
class EmbeddingWorkerPool:
    """Simple worker that pulls jobs from the queue and executes embedding tasks."""

    job_queue: JobQueue
    embedding_service: Any
    worker_id: str
    rate_limiter: Any
    poll_interval: float = 1.0
    backoff_seconds: float = 5.0
    sleep_fn: SleepFn = time.sleep

    def run_forever(self) -> None:
        """Continuously process jobs until interrupted."""
        while True:
            processed = self.process_once()
            if not processed and self.poll_interval > 0:
                self.sleep_fn(self.poll_interval)

    def process_once(self) -> bool:
        """Attempt to process a single job from the queue."""
        job = self.job_queue.dequeue_job(worker_id=self.worker_id)
        if job is None:
            return False

        try:
            if self.rate_limiter is not None:
                self.rate_limiter.acquire()

            result = self._execute_job(job)
        except RateLimitError as exc:  # External service asked us to slow down
            error_message = f"Rate limit encountered for job {job.id}: {exc}"
            LOGGER.info(error_message)
            self.job_queue.mark_job_failed(job.id, error_log=error_message, retry=True)
            if self.backoff_seconds > 0:
                self.sleep_fn(self.backoff_seconds)
            return True
        except Exception as exc:
            error_message = f"Job {job.id} failed: {exc}"
            LOGGER.exception("Unexpected error while processing job %s", job.id)
            self.job_queue.mark_job_failed(job.id, error_log=error_message, retry=False)
            return True

        result_payload = result if result is not None else {}
        self.job_queue.mark_job_done(job.id, result=result_payload)
        return True

    def _execute_job(self, job: EmbeddingJob) -> Optional[dict]:
        """Dispatch job execution based on job type."""
        config = job.config or {}

        if job.job_type in (JobType.EMBED_PDF, JobType.REEMBED_PDF):
            return self.embedding_service.embed_pdf(pdf_id=job.pdf_id, config=config)

        if job.job_type == JobType.EXTRACT_TABLES and hasattr(
            self.embedding_service, "extract_tables"
        ):
            return self.embedding_service.extract_tables(
                pdf_id=job.pdf_id, config=config
            )

        raise ValueError(f"Unsupported job type: {job.job_type}")


__all__ = ["EmbeddingWorkerPool", "RateLimitError"]
