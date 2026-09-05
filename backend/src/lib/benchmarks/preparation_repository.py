"""Lease-fenced frozen-document preparation journal in existing job events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import time
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.benchmarks.document_preparation import PreparedBenchmarkDocument
from src.lib.benchmarks.persistence import (
    BenchmarkCancellationRequestedError,
    BenchmarkLeaseLostError,
    BenchmarkRepository,
)
from src.models.sql.benchmark import (
    BenchmarkEvent,
    BenchmarkInputSnapshot,
    BenchmarkJob,
    BenchmarkJobInputSnapshot,
    BenchmarkJobStatus,
)
from src.models.sql.database import SessionLocal


STARTED = "document_preparation.started"
COMPLETED = "document_preparation.completed"
STAGES = frozenset({"artifacts", "vector_document", "hierarchy", "chunking", "figure_locators", "vector_storage", "ready"})


class BenchmarkPreparationUncertainError(RuntimeError):
    """A previous preparation started without a durable successful receipt."""


@dataclass(frozen=True)
class PreparationClaim:
    document_id: UUID
    snapshot_digest: str
    prepared: PreparedBenchmarkDocument | None = None


class BenchmarkPreparationRepository:
    """One preparation per job/snapshot, reused by all comparison arms.

    All operations use caller-owned transactions. Commit begin() before any
    artifact or provider operation. A started-only journal never authorizes a
    replay, including after lease recovery or on another sibling cell.
    """

    def __init__(self, session: Session):
        self.session = session

    def _lock(
        self, job_id: UUID, snapshot_id: UUID, lease_owner: UUID,
    ) -> BenchmarkInputSnapshot:
        now = datetime.now(timezone.utc)
        job = self.session.scalar(
            select(BenchmarkJob).where(
                BenchmarkJob.id == job_id,
                BenchmarkJob.lease_owner == lease_owner,
                BenchmarkJob.lease_expires_at > now,
                BenchmarkJob.status.in_((BenchmarkJobStatus.RUNNING, BenchmarkJobStatus.CANCEL_REQUESTED)),
            ).with_for_update().execution_options(populate_existing=True)
        )
        if job is None or job.lease_expires_at is None or job.lease_expires_at <= datetime.now(timezone.utc):
            raise BenchmarkLeaseLostError("benchmark preparation job lease is no longer owned")
        if job.status == BenchmarkJobStatus.CANCEL_REQUESTED:
            raise BenchmarkCancellationRequestedError("benchmark preparation was cancelled")
        snapshot = self.session.scalar(
            select(BenchmarkInputSnapshot).where(
                BenchmarkInputSnapshot.id == snapshot_id,
                BenchmarkInputSnapshot.owner_subject == job.owner_subject,
                BenchmarkInputSnapshot.id.in_(
                    select(BenchmarkJobInputSnapshot.snapshot_id).where(
                        BenchmarkJobInputSnapshot.job_id == job_id,
                    )
                ),
            )
        )
        if snapshot is None:
            raise LookupError("preparation snapshot does not belong to the owned job")
        return snapshot

    def _latest(self, job_id: UUID, snapshot_id: UUID) -> BenchmarkEvent | None:
        return self.session.scalar(
            select(BenchmarkEvent).where(
                BenchmarkEvent.job_id == job_id,
                BenchmarkEvent.event_type.in_((STARTED, COMPLETED)),
                BenchmarkEvent.payload["snapshot_id"].astext == str(snapshot_id),
            ).order_by(BenchmarkEvent.sequence.desc()).limit(1)
        )

    def begin(self, *, job_id: UUID, snapshot_id: UUID, lease_owner: UUID) -> PreparationClaim:
        snapshot = self._lock(job_id, snapshot_id, lease_owner)
        existing = self._latest(job_id, snapshot_id)
        if existing is not None:
            if existing.event_type != COMPLETED:
                raise BenchmarkPreparationUncertainError("frozen document preparation cannot be replayed")
            receipt = PreparedBenchmarkDocument.model_validate_json(json.dumps(existing.payload["receipt"]))
            if receipt.snapshot_digest != snapshot.digest:
                raise ValueError("prepared document receipt does not match frozen snapshot")
            return PreparationClaim(receipt.document_id, snapshot.digest, receipt)
        document_id = uuid4()
        BenchmarkRepository(self.session).append_event(
            job_id=job_id, event_type=STARTED,
            payload={"snapshot_id": str(snapshot_id), "document_id": str(document_id)},
        )
        return PreparationClaim(document_id, snapshot.digest)

    def complete(
        self, *, job_id: UUID, snapshot_id: UUID, lease_owner: UUID,
        receipt: PreparedBenchmarkDocument,
    ) -> None:
        snapshot = self._lock(job_id, snapshot_id, lease_owner)
        existing = self._latest(job_id, snapshot_id)
        if (
            existing is None or existing.event_type != STARTED
            or existing.payload["document_id"] != str(receipt.document_id)
            or receipt.snapshot_digest != snapshot.digest
        ):
            raise ValueError("preparation completion does not match its durable start")
        BenchmarkRepository(self.session).append_event(
            job_id=job_id, event_type=COMPLETED,
            payload={"snapshot_id": str(snapshot_id), "receipt": receipt.model_dump(mode="json")},
        )

    def checkpoint(
        self, *, job_id: UUID, snapshot_id: UUID, lease_owner: UUID,
        document_id: UUID, stage: str, elapsed_ms: float,
    ) -> None:
        """Fence the next normal stage; timing is not provider usage/cost."""
        if stage not in STAGES or not math.isfinite(elapsed_ms) or elapsed_ms < 0:
            raise ValueError("invalid preparation stage checkpoint")
        self._lock(job_id, snapshot_id, lease_owner)
        existing = self._latest(job_id, snapshot_id)
        if (
            existing is None or existing.event_type != STARTED
            or existing.payload["document_id"] != str(document_id)
        ):
            raise ValueError("preparation checkpoint does not match its durable start")
        BenchmarkRepository(self.session).append_event(
            job_id=job_id, event_type="document_preparation.stage",
            payload={
                "snapshot_id": str(snapshot_id), "document_id": str(document_id),
                "stage": stage, "elapsed_ms": elapsed_ms,
                "accounting_scope": "document_preparation",
                "measurement": "elapsed_time_only",
            },
        )


class PreparationStageCheckpoint:
    """Commit the lease checkpoint before allowing the next stage to start."""

    def __init__(
        self, *, job_id: UUID, snapshot_id: UUID, document_id: UUID, lease_owner: UUID,
        session_factory: Callable[..., Any] = SessionLocal,
    ):
        self.job_id = job_id
        self.snapshot_id = snapshot_id
        self.document_id = document_id
        self.lease_owner = lease_owner
        self.session_factory = session_factory
        self.started_at = time.monotonic()

    async def __call__(self, stage: str) -> None:
        with self.session_factory() as session:
            BenchmarkPreparationRepository(session).checkpoint(
                job_id=self.job_id, snapshot_id=self.snapshot_id,
                document_id=self.document_id, lease_owner=self.lease_owner,
                stage=stage, elapsed_ms=(time.monotonic() - self.started_at) * 1000,
            )
            session.commit()
