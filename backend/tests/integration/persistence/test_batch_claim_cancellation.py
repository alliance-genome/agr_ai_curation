"""Real-transaction coverage for batch cancellation and worker claiming."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event, Lock, Thread
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, update

from src.lib.batch import processor
from src.lib.batch.service import BatchService
from src.models.sql.batch import Batch, BatchDocument, BatchDocumentStatus, BatchStatus
from src.models.sql.database import SessionLocal


BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    """Apply the canonical schema before exercising real batch transactions."""
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")


def _create_batch(*, user_id: int = 701) -> tuple[UUID, UUID]:
    with SessionLocal() as db:
        batch = Batch(
            user_id=user_id,
            flow_id=uuid4(),
            status=BatchStatus.PENDING,
            total_documents=1,
            completed_documents=0,
            failed_documents=0,
        )
        db.add(batch)
        db.flush()
        batch_doc = BatchDocument(
            batch_id=batch.id,
            document_id=uuid4(),
            position=0,
            status=BatchDocumentStatus.PENDING,
        )
        db.add(batch_doc)
        db.commit()
        return batch.id, batch_doc.id


def _delete_batch(batch_id: UUID) -> None:
    with SessionLocal() as db:
        batch = db.get(Batch, batch_id)
        if batch is not None:
            db.delete(batch)
            db.commit()


def _create_batch_with_statuses(
    batch_status: BatchStatus,
    document_statuses: list[BatchDocumentStatus],
    *,
    lease_owner: UUID | None = None,
    lease_expires_at: datetime | None = None,
) -> tuple[UUID, list[UUID]]:
    with SessionLocal() as db:
        batch = Batch(
            user_id=703,
            flow_id=uuid4(),
            status=batch_status,
            total_documents=len(document_statuses),
            completed_documents=99,
            failed_documents=99,
            started_at=datetime.now(timezone.utc) if batch_status != BatchStatus.PENDING else None,
            completed_at=(
                datetime.now(timezone.utc)
                if batch_status in (BatchStatus.COMPLETED, BatchStatus.CANCELLED)
                else None
            ),
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            lease_heartbeat_at=lease_expires_at,
        )
        db.add(batch)
        db.flush()
        batch_document_ids = []
        for position, status in enumerate(document_statuses):
            document = BatchDocument(
                batch_id=batch.id,
                document_id=uuid4(),
                position=position,
                status=status,
                result_file_path=(f"/files/{position}" if status == BatchDocumentStatus.COMPLETED else None),
                review_session_ids=([f"review-{position}"] if status == BatchDocumentStatus.COMPLETED else None),
                processed_at=(
                    datetime.now(timezone.utc)
                    if status in (BatchDocumentStatus.COMPLETED, BatchDocumentStatus.FAILED)
                    else None
                ),
            )
            db.add(document)
            db.flush()
            batch_document_ids.append(document.id)
        db.commit()
        return batch.id, batch_document_ids


def test_cancelled_batch_worker_has_zero_processing_side_effects(monkeypatch):
    batch_id, batch_doc_id = _create_batch()
    try:
        with SessionLocal() as db:
            cancelled = BatchService(db).cancel_batch(batch_id, user_id=701)
            assert cancelled is not None
            assert cancelled.status == BatchStatus.CANCELLED

        @contextmanager
        def _real_worker_session():
            with SessionLocal() as db:
                yield db

        processing_calls: list[str] = []
        monkeypatch.setattr(processor, "get_db_session", _real_worker_session)
        monkeypatch.setattr(
            processor,
            "_process_single_document",
            lambda *_args, **_kwargs: processing_calls.append("document"),
        )
        monkeypatch.setattr(
            processor,
            "_execute_flow_for_document",
            lambda *_args, **_kwargs: processing_calls.append("flow"),
        )
        monkeypatch.setattr(
            processor,
            "_validate_file_ownership",
            lambda *_args, **_kwargs: processing_calls.append("file"),
        )
        monkeypatch.setattr(
            processor,
            "get_batch_broadcaster",
            lambda: processing_calls.append("event"),
        )

        processor.process_batch_task(batch_id)

        with SessionLocal() as db:
            batch = db.get(Batch, batch_id)
            batch_doc = db.get(BatchDocument, batch_doc_id)
            assert batch.status == BatchStatus.CANCELLED
            assert batch.completed_documents == 0
            assert batch.failed_documents == 0
            assert batch_doc.status == BatchDocumentStatus.PENDING
        assert processing_calls == []
    finally:
        _delete_batch(batch_id)


def test_uncommitted_cancellation_deterministically_beats_concurrent_claim():
    batch_id, _batch_doc_id = _create_batch(user_id=702)
    cancel_db = SessionLocal()
    claim_started = Event()
    claim_finished = Event()
    claim_result: list[object] = []
    try:
        cancelled_id = cancel_db.execute(
            update(Batch)
            .where(Batch.id == batch_id, Batch.status == BatchStatus.PENDING)
            .values(status=BatchStatus.CANCELLED)
            .returning(Batch.id)
        ).scalar_one()
        assert cancelled_id == batch_id

        def _claim_while_cancel_is_uncommitted() -> None:
            with SessionLocal() as claim_db:
                claim_started.set()
                claim_result.append(
                    BatchService(claim_db).claim_recoverable_batch(batch_id, uuid4(), 120)
                )
                claim_finished.set()

        claim_thread = Thread(target=_claim_while_cancel_is_uncommitted)
        claim_thread.start()
        assert claim_started.wait(timeout=5)
        assert not claim_finished.wait(timeout=0.2)

        cancel_db.commit()
        claim_thread.join(timeout=5)
        assert not claim_thread.is_alive()
        assert claim_result == [None]

        with SessionLocal() as verify_db:
            batch = verify_db.scalars(select(Batch).where(Batch.id == batch_id)).one()
            assert batch.status == BatchStatus.CANCELLED
            assert batch.started_at is None
            assert batch.completed_documents == 0
            assert batch.failed_documents == 0
    finally:
        cancel_db.rollback()
        cancel_db.close()
        _delete_batch(batch_id)


def test_two_workers_contending_for_batch_have_one_lease_owner():
    batch_id, _document_ids = _create_batch_with_statuses(
        BatchStatus.PENDING,
        [BatchDocumentStatus.PENDING],
    )
    barrier = Barrier(2)
    lock = Lock()
    results: list[tuple[UUID, bool]] = []

    def claim(owner: UUID) -> None:
        with SessionLocal() as db:
            barrier.wait()
            claimed = BatchService(db).claim_recoverable_batch(batch_id, owner, 120)
            with lock:
                results.append((owner, claimed is not None))

    owners = [uuid4(), uuid4()]
    threads = [Thread(target=claim, args=(owner,)) for owner in owners]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()

        assert sum(won for _owner, won in results) == 1
        winning_owner = next(owner for owner, won in results if won)
        with SessionLocal() as db:
            batch = db.get(Batch, batch_id)
            assert batch.lease_owner == winning_owner
            assert batch.status == BatchStatus.RUNNING
    finally:
        _delete_batch(batch_id)


def test_unexpired_lease_blocks_recovery_then_expired_lease_can_be_claimed():
    original_owner = uuid4()
    batch_id, _document_ids = _create_batch_with_statuses(
        BatchStatus.RUNNING,
        [BatchDocumentStatus.PENDING],
        lease_owner=original_owner,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    recovery_owner = uuid4()
    try:
        with SessionLocal() as db:
            assert BatchService(db).claim_recoverable_batch(batch_id, recovery_owner, 120) is None

        with SessionLocal() as db:
            batch = db.get(Batch, batch_id)
            batch.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()

        with SessionLocal() as db:
            assert not BatchService(db).heartbeat_batch_lease(
                batch_id,
                original_owner,
                120,
            )

        with SessionLocal() as db:
            claimed = BatchService(db).claim_recoverable_batch(batch_id, recovery_owner, 120)
            assert claimed is not None
            assert claimed.lease_owner == recovery_owner
    finally:
        _delete_batch(batch_id)


def test_missing_flow_stale_worker_cannot_cancel_takeover_owner():
    original_owner = uuid4()
    recovery_owner = uuid4()
    batch_id, _document_ids = _create_batch_with_statuses(
        BatchStatus.RUNNING,
        [BatchDocumentStatus.PENDING],
        lease_owner=original_owner,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    original_db = SessionLocal()
    takeover_db = SessionLocal()
    try:
        stale_batch = original_db.get(Batch, batch_id)
        assert stale_batch is not None

        takeover_db.execute(
            update(Batch)
            .where(Batch.id == batch_id, Batch.lease_owner == original_owner)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        takeover_db.commit()
        claimed = BatchService(takeover_db).claim_recoverable_batch(
            batch_id,
            recovery_owner,
            120,
        )
        assert claimed is not None

        processor._process_claimed_batch(
            original_db,
            BatchService(original_db),
            stale_batch,
            original_owner,
        )

        takeover_db.expire_all()
        current_batch = takeover_db.get(Batch, batch_id)
        assert current_batch is not None
        assert current_batch.status == BatchStatus.RUNNING
        assert current_batch.lease_owner == recovery_owner
        assert current_batch.lease_expires_at is not None
    finally:
        original_db.rollback()
        original_db.close()
        takeover_db.rollback()
        takeover_db.close()
        _delete_batch(batch_id)


def test_restart_claim_preserves_terminal_outputs_and_fails_only_interrupted_work():
    batch_id, document_ids = _create_batch_with_statuses(
        BatchStatus.RUNNING,
        [
            BatchDocumentStatus.COMPLETED,
            BatchDocumentStatus.PENDING,
            BatchDocumentStatus.PROCESSING,
            BatchDocumentStatus.FAILED,
        ],
        lease_owner=uuid4(),
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    try:
        with SessionLocal() as db:
            claimed = BatchService(db).claim_recoverable_batch(batch_id, uuid4(), 120)
            assert claimed is not None

        with SessionLocal() as db:
            batch = db.get(Batch, batch_id)
            documents = [db.get(BatchDocument, document_id) for document_id in document_ids]
            assert [document.status for document in documents] == [
                BatchDocumentStatus.COMPLETED,
                BatchDocumentStatus.PENDING,
                BatchDocumentStatus.FAILED,
                BatchDocumentStatus.FAILED,
            ]
            assert documents[0].result_file_path == "/files/0"
            assert documents[0].review_session_ids == ["review-0"]
            assert documents[2].error_message == (
                "Worker lease expired during processing; document was not re-run"
            )
            assert batch.completed_documents == 1
            assert batch.failed_documents == 2
            assert batch.completed_documents + batch.failed_documents <= batch.total_documents
    finally:
        _delete_batch(batch_id)


def test_startup_scan_includes_pending_and_stale_running_only():
    now = datetime.now(timezone.utc)
    fixtures = [
        _create_batch_with_statuses(BatchStatus.PENDING, [BatchDocumentStatus.PENDING]),
        _create_batch_with_statuses(
            BatchStatus.RUNNING,
            [BatchDocumentStatus.PROCESSING],
            lease_owner=uuid4(),
            lease_expires_at=now - timedelta(seconds=1),
        ),
        _create_batch_with_statuses(
            BatchStatus.RUNNING,
            [BatchDocumentStatus.PENDING],
            lease_owner=uuid4(),
            lease_expires_at=now + timedelta(minutes=5),
        ),
        _create_batch_with_statuses(BatchStatus.COMPLETED, [BatchDocumentStatus.COMPLETED]),
        _create_batch_with_statuses(BatchStatus.CANCELLED, [BatchDocumentStatus.PENDING]),
    ]
    batch_ids = [fixture[0] for fixture in fixtures]
    try:
        with SessionLocal() as db:
            recoverable = set(BatchService(db).list_recoverable_batch_ids())
        assert recoverable.intersection(batch_ids) == {batch_ids[0], batch_ids[1]}
    finally:
        for batch_id in batch_ids:
            _delete_batch(batch_id)
