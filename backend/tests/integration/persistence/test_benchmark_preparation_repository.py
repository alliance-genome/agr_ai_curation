"""Durable preparation identity, reuse and fencing against real PostgreSQL."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError

from src.lib.benchmarks.document_preparation import PreparedBenchmarkDocument
from src.lib.benchmarks.persistence import BenchmarkCancellationRequestedError, BenchmarkLeaseLostError, BenchmarkRepository
from src.lib.benchmarks.preparation_repository import (
    BenchmarkPreparationRepository, BenchmarkPreparationUncertainError,
)
from src.models.sql.benchmark import BenchmarkCell, BenchmarkEvent, BenchmarkInputSnapshot, BenchmarkJob, BenchmarkJobStatus
from src.models.sql.database import SessionLocal
from tests.integration.persistence.test_benchmark_repository import _create_job


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


def running_job(session):
    job = _create_job(session, owner=f"preparation-journal-{uuid4()}", cells=1)
    claimed = BenchmarkRepository(session).claim_next_job(
        lease_owner=uuid4(), lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    assert claimed is not None and claimed.id == job.id
    cell = session.scalar(select(BenchmarkCell).where(BenchmarkCell.job_id == job.id))
    assert cell is not None
    return job, cell.input_snapshot_id


def receipt_for(claim):
    return PreparedBenchmarkDocument(
        document_id=claim.document_id, snapshot_digest=claim.snapshot_digest,
        source_path=f"benchmark_documents/{claim.document_id}/source",
        processed_json_path=f"benchmark_documents/{claim.document_id}/elements.json",
        processed_json_digest="sha256:" + "a" * 64,
        chunk_count=2,
    )


def test_started_preparation_is_never_replayed_and_completed_identity_is_reused():
    with SessionLocal() as session:
        job, snapshot_id = running_job(session)
        repository = BenchmarkPreparationRepository(session)
        arguments = dict(job_id=job.id, snapshot_id=snapshot_id, lease_owner=job.lease_owner)
        claim = repository.begin(**arguments)
        assert claim.prepared is None
        repository.checkpoint(**arguments, document_id=claim.document_id, stage="hierarchy", elapsed_ms=1.5)
        with pytest.raises(BenchmarkPreparationUncertainError):
            repository.begin(**arguments)
        receipt = receipt_for(claim)
        with pytest.raises(ValueError, match="durable start"):
            repository.complete(**arguments, receipt=receipt.model_copy(update={"document_id": uuid4()}))
        repository.complete(**arguments, receipt=receipt)
        reused = repository.begin(**arguments)
        assert reused.document_id == claim.document_id
        assert reused.prepared == receipt
        with pytest.raises(ValueError, match="durable start"):
            repository.complete(**arguments, receipt=receipt)
        events = list(session.scalars(select(BenchmarkEvent).where(BenchmarkEvent.job_id == job.id)))
        assert len(events) == 3
        stage_event = next(event for event in events if event.event_type == "document_preparation.stage")
        assert stage_event.payload["measurement"] == "elapsed_time_only"
        assert "input_tokens" not in stage_event.payload
        with pytest.raises(ValueError, match="durable start"):
            repository.checkpoint(**arguments, document_id=claim.document_id, stage="ready", elapsed_ms=2)
        session.rollback()


@pytest.mark.parametrize("fence", ["wrong_lease", "expired", "cancelled", "wrong_snapshot"])
def test_preparation_begin_and_completion_respect_job_fences(fence):
    with SessionLocal() as session:
        job, snapshot_id = running_job(session)
        repository = BenchmarkPreparationRepository(session)
        arguments = dict(job_id=job.id, snapshot_id=snapshot_id, lease_owner=job.lease_owner)
        claim = repository.begin(**arguments)
        if fence == "wrong_lease":
            arguments["lease_owner"] = uuid4()
        elif fence == "expired":
            job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif fence == "cancelled":
            job.status = BenchmarkJobStatus.CANCEL_REQUESTED
            job.cancel_requested_at = datetime.now(timezone.utc)
        else:
            arguments["snapshot_id"] = uuid4()
        session.flush()
        error = {
            "wrong_lease": BenchmarkLeaseLostError, "expired": BenchmarkLeaseLostError,
            "cancelled": BenchmarkCancellationRequestedError, "wrong_snapshot": LookupError,
        }[fence]
        with pytest.raises(error):
            repository.begin(**arguments)
        with pytest.raises(error):
            repository.complete(**arguments, receipt=receipt_for(claim))
        with pytest.raises(error):
            repository.checkpoint(**arguments, document_id=claim.document_id, stage="vector_storage", elapsed_ms=2)
        session.rollback()


@pytest.mark.parametrize("completed", [False, True])
def test_terminal_deletion_retains_preparation_recovery_identity(completed):
    with SessionLocal() as session:
        job, snapshot_id = running_job(session)
        preparation = BenchmarkPreparationRepository(session)
        arguments = dict(job_id=job.id, snapshot_id=snapshot_id, lease_owner=job.lease_owner)
        claim = preparation.begin(**arguments)
        if completed:
            preparation.complete(**arguments, receipt=receipt_for(claim))
        repository = BenchmarkRepository(session)
        now = datetime.now(timezone.utc)
        repository.request_cancellation(job_id=job.id, owner_subject=job.owner_subject, requested_at=now)
        repository.cancel_queued_cells(job_id=job.id, lease_owner=job.lease_owner, cancelled_at=now)
        repository.complete_job(job_id=job.id, lease_owner=job.lease_owner, completed_at=now)
        assert repository.delete_terminal_job(job_id=job.id, owner_subject="different-owner") is False
        with pytest.raises(ValueError, match="coordinated document cleanup"):
            repository.delete_terminal_job(job_id=job.id, owner_subject=job.owner_subject)
        assert session.get(BenchmarkJob, job.id) is not None
        started = session.scalar(select(BenchmarkEvent).where(
            BenchmarkEvent.job_id == job.id,
            BenchmarkEvent.event_type == "document_preparation.started",
        ))
        assert started.payload["document_id"] == str(claim.document_id)
        session.rollback()


def test_committed_start_serializes_competing_claims_and_survives_new_session():
    with SessionLocal() as setup:
        job, snapshot_id = running_job(setup)
        job_id, owner, lease_owner = job.id, job.owner_subject, job.lease_owner
        setup.commit()
    arguments = dict(job_id=job_id, snapshot_id=snapshot_id, lease_owner=lease_owner)
    try:
        with SessionLocal() as first, SessionLocal() as second:
            repository = BenchmarkPreparationRepository(first)
            claim = repository.begin(**arguments)
            second.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError, match="lock timeout"):
                BenchmarkPreparationRepository(second).begin(**arguments)
            second.rollback()
            first.commit()
            with pytest.raises(BenchmarkPreparationUncertainError):
                BenchmarkPreparationRepository(second).begin(**arguments)
            second.rollback()
            repository.complete(**arguments, receipt=receipt_for(claim))
            first.commit()
        with SessionLocal() as fresh:
            reused = BenchmarkPreparationRepository(fresh).begin(**arguments)
            assert reused.prepared == receipt_for(claim)
    finally:
        with SessionLocal() as cleanup:
            repository = BenchmarkRepository(cleanup)
            now = datetime.now(timezone.utc)
            repository.request_cancellation(job_id=job_id, owner_subject=owner, requested_at=now)
            repository.cancel_queued_cells(job_id=job_id, lease_owner=lease_owner, cancelled_at=now)
            repository.complete_job(job_id=job_id, lease_owner=lease_owner, completed_at=now)
            # Test-owned journal only: this test never creates external copies.
            cleanup.execute(delete(BenchmarkJob).where(BenchmarkJob.id == job_id))
            cleanup.execute(delete(BenchmarkInputSnapshot).where(BenchmarkInputSnapshot.id == snapshot_id))
            cleanup.commit()
