"""Real PostgreSQL evidence for shared sessions and bounded checkout recovery."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import TimeoutError

from src.models.sql import database


@pytest.fixture
def pool_database(historical_migration_database):
    # Retain the canonical engine and its database-redirect listener. Only the
    # test pool has a small capacity/timeout; production timeout is unchanged.
    engine = database.engine
    original_pool = engine.pool
    engine.dispose()
    engine.pool = original_pool.__class__(
        original_pool._creator, pool_size=1, max_overflow=1,
        timeout=0.1, pre_ping=True, dialect=engine.dialect,
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE pool_probe (value text PRIMARY KEY)"))
    counts = {"checkout": 0, "checkin": 0}

    def checkout(*_args):
        counts["checkout"] += 1

    def checkin(*_args):
        counts["checkin"] += 1

    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
    try:
        yield engine, counts
    finally:
        assert engine.pool.checkedout() == 0
        assert counts["checkout"] == counts["checkin"]
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "checkin", checkin)
        engine.dispose()
        engine.pool = original_pool.recreate()


def test_overlapping_request_transactions_are_independent(pool_database):
    engine, _ = pool_database
    app_provider = database.get_db()
    feedback_provider = database.get_feedback_db()
    app, feedback = next(app_provider), next(feedback_provider)
    try:
        assert app is not feedback
        app.execute(text("INSERT INTO pool_probe VALUES ('app')"))
        feedback.execute(text("INSERT INTO pool_probe VALUES ('feedback')"))
        assert engine.pool.checkedout() == 2
        app.commit()
        assert feedback.in_transaction()
        assert engine.pool.checkedout() == 1
        feedback.rollback()
        app.execute(text("INSERT INTO pool_probe VALUES ('discard')"))
        feedback.execute(text("INSERT INTO pool_probe VALUES ('kept')"))
        app_provider.close()
        assert feedback.in_transaction()
        feedback.commit()
        assert feedback.execute(text("SELECT value FROM pool_probe ORDER BY value")).scalars().all() == ["app", "kept"]
    finally:
        app_provider.close()
        feedback_provider.close()


def test_shared_capacity_exhaustion_and_recovery(pool_database):
    engine, counts = pool_database
    ready = Barrier(3)
    release = Event()

    def hold(provider):
        dependency = provider()
        session = next(dependency)
        try:
            session.execute(text("SELECT 1"))
            ready.wait(timeout=10)
            assert release.wait(timeout=10)
        finally:
            dependency.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(hold, provider) for provider in
                   (database.get_db, database.get_feedback_db)]
        try:
            ready.wait(timeout=10)
            assert engine.pool.checkedout() == 2
            assert counts["checkout"] == 2
            with database.SessionLocal() as third:
                with pytest.raises(TimeoutError):
                    third.execute(text("SELECT 1"))
            assert counts["checkout"] == 2
        finally:
            release.set()
        for future in futures:
            future.result(timeout=10)
    assert engine.pool.checkedout() == 0
    with database.SessionLocal() as recovered:
        assert recovered.execute(text("SELECT 1")).scalar_one() == 1
    assert counts == {"checkout": 3, "checkin": 3}
    assert engine.pool.checkedin() == 1


@pytest.mark.parametrize("fail", [False, True])
def test_background_persistence_and_exception_release(pool_database, monkeypatch, fail):
    from src.api import feedback as api
    from src.lib.feedback.models import FeedbackReport, ProcessingStatus

    engine, _ = pool_database
    report_id = str(uuid4())
    with database.SessionLocal() as session:
        session.add(FeedbackReport(
            id=report_id, session_id="pool-test", curator_id="pool-test",
            feedback_text="synthetic", trace_ids=[],
        ))
        session.commit()

    # Replace external trace/notification orchestration, but exercise the real
    # background entry point, canonical factory, ORM writes, commit and close.
    class Processor:
        def __init__(self, session):
            self.session = session

        def process_feedback_report(self, feedback_id):
            report = self.session.get(FeedbackReport, feedback_id)
            report.processing_status = ProcessingStatus.COMPLETED
            self.session.flush()
            if fail:
                raise RuntimeError("synthetic failure")
            self.session.commit()

    reported = []
    monkeypatch.setattr(api, "FeedbackService", Processor)
    monkeypatch.setattr(api, "report_background_task_exception",
                        lambda exc, **kwargs: reported.append(exc))
    worker = api.dispatch_feedback_report_processing(report_id)
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert engine.pool.checkedout() == 0
    with database.SessionLocal() as session:
        report = session.get(FeedbackReport, report_id)
        assert report.processing_status == (
            ProcessingStatus.PENDING if fail else ProcessingStatus.COMPLETED
        )
    assert len(reported) == int(fail)
