"""Postgres-backed job queue built on the embedding_jobs table."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.engine import Engine

from app.models import EmbeddingJob, JobStatus, JobType


@dataclass
class JobQueueConfig:
    """Runtime configuration for the queue."""

    channel: str = "embedding_queue"
    max_retries: int = 3


class JobQueue:
    """Lightweight Postgres job queue using LISTEN/NOTIFY."""

    def __init__(
        self, engine: Engine, channel: str = "embedding_queue", *, max_retries: int = 3
    ) -> None:
        self.engine = engine
        self.config = JobQueueConfig(channel=channel, max_retries=max_retries)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def _session(self) -> Session:
        session: Session = self._session_factory()
        try:
            yield session
            session.commit()
        except:  # pragma: no cover - defensive rollback
            session.rollback()
            raise
        finally:
            session.close()

    def enqueue_job(
        self,
        *,
        pdf_id: UUID,
        job_type: JobType,
        priority: int = 5,
        config: Optional[Dict[str, Any]] = None,
        total_items: Optional[int] = None,
    ) -> EmbeddingJob:
        with self._session() as session:
            job = EmbeddingJob(
                pdf_id=pdf_id,
                job_type=job_type,
                status=JobStatus.PENDING,
                priority=priority,
                config=config or {},
                total_items=total_items,
                processed_items=0,
                progress=0,
                retry_count=0,
            )
            session.add(job)
            session.flush()
            session.refresh(job)
        self._notify(job.id)
        return job

    def dequeue_job(self, *, worker_id: str) -> Optional[EmbeddingJob]:
        with self._session() as session:
            query = (
                session.query(EmbeddingJob)
                .filter(EmbeddingJob.status == JobStatus.PENDING)
                .order_by(EmbeddingJob.priority.desc(), EmbeddingJob.created_at.asc())
                .with_for_update(skip_locked=True)
            )
            job = query.first()
            if job is None:
                return None

            job.status = JobStatus.RUNNING
            job.worker_id = worker_id
            job.started_at = datetime.now(timezone.utc)
            job.error_log = None
            session.flush()
            session.refresh(job)
            return job

    def update_progress(self, job_id: UUID, *, processed_items: int) -> EmbeddingJob:
        with self._session() as session:
            job = self._get_job_for_update(session, job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")

            job.processed_items = processed_items
            if job.total_items:
                percentage = int((processed_items / job.total_items) * 100)
                job.progress = max(0, min(percentage, 100))
            else:
                job.progress = 0
            session.flush()
            session.refresh(job)
            return job

    def mark_job_done(
        self, job_id: UUID, *, result: Optional[Dict[str, Any]] = None
    ) -> EmbeddingJob:
        with self._session() as session:
            job = self._get_job_for_update(session, job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")

            job.status = JobStatus.DONE
            job.worker_id = None
            job.progress = 100
            job.processed_items = job.total_items or job.processed_items
            job.completed_at = datetime.now(timezone.utc)
            job.result = result or {}
            job.error_log = None
            session.flush()
            session.refresh(job)
            return job

    def mark_job_failed(
        self,
        job_id: UUID,
        *,
        error_log: str,
        retry: bool,
    ) -> EmbeddingJob:
        with self._session() as session:
            job = self._get_job_for_update(session, job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")

            job.retry_count += 1
            job.error_log = error_log

            if retry and job.retry_count <= self.config.max_retries:
                job.status = JobStatus.PENDING
                job.worker_id = None
                job.started_at = None
                job.completed_at = None
                job.progress = 0
                job.processed_items = 0
                job.result = None
                session.flush()
                session.refresh(job)
            else:
                job.status = JobStatus.FAILED
                job.worker_id = None
                job.completed_at = datetime.now(timezone.utc)
                job.progress = job.progress or 0
                session.flush()
                session.refresh(job)
                return job

        # Only notify when the job is being retried
        if retry and job.retry_count <= self.config.max_retries:
            self._notify(job_id)
        return job

    def _notify(self, job_id: UUID) -> None:
        payload = str(job_id)
        with self.engine.begin() as conn:
            conn.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": self.config.channel, "payload": payload},
            )

    @staticmethod
    def _get_job_for_update(session: Session, job_id: UUID) -> Optional[EmbeddingJob]:
        stmt = select(EmbeddingJob).where(EmbeddingJob.id == job_id).with_for_update()
        result = session.execute(stmt).scalar_one_or_none()
        return result


__all__ = ["JobQueue"]
