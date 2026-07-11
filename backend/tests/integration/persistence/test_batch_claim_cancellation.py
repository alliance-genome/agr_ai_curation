"""Real-transaction coverage for batch cancellation and worker claiming."""

from contextlib import contextmanager
from threading import Event, Thread
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from src.lib.batch import processor
from src.lib.batch.service import BatchService
from src.models.sql.batch import Batch, BatchDocument, BatchDocumentStatus, BatchStatus
from src.models.sql.database import Base, SessionLocal, engine


@pytest.fixture(scope="module", autouse=True)
def _batch_tables():
    """Create only the SQL tables exercised by this isolated persistence suite."""
    tables = [Batch.__table__, BatchDocument.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    yield


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
                claim_result.append(BatchService(claim_db).claim_pending_batch(batch_id))
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
