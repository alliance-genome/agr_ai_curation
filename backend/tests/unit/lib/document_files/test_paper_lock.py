"""Paper lock cleanup after the request is cancelled during acquisition."""

import asyncio
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.lib.document_files import paper_lock


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "capture_fails"),
    [("acquire", False), ("acquire", True), ("release", False), ("release", True), (None, False)],
)
async def test_cancel_during_acquire(monkeypatch, caplog, failure, capture_fails):
    document_id = uuid4()
    db_error = RuntimeError("private SQL parameter")
    capture_error = ValueError("private capture details")
    connection = Mock()
    taken = Mock()
    taken.scalar_one.return_value = True
    if failure == "acquire":
        connection.execute.side_effect = db_error
    else:
        connection.execute.side_effect = [taken, db_error if failure == "release" else None]
    monkeypatch.setattr("src.models.sql.database.engine.connect", lambda: connection)
    report = Mock(side_effect=capture_error if capture_fails else None)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    # Control worker completion on the test loop while exercising the real lock SQL cleanup.
    resume_acquire = asyncio.Event()
    workers = asyncio.Queue()

    async def run_worker(func, *args):
        workers.put_nowait(asyncio.current_task())
        if func is paper_lock._acquire:
            await resume_acquire.wait()
        return func(*args)

    monkeypatch.setattr(paper_lock.asyncio, "to_thread", run_worker)

    async def request():
        async with paper_lock.paper_operation_lock(document_id):
            pytest.fail("Cancelled request entered the lock")

    request_task = asyncio.create_task(request())
    acquiring = await asyncio.wait_for(workers.get(), timeout=2)
    request_task.cancel("request cancelled")
    with pytest.raises(asyncio.CancelledError, match="request cancelled"):
        await request_task

    resume_acquire.set()
    # Wait for each task and its previously registered callbacks without retrieving its exception.
    await asyncio.wait({acquiring})
    if failure != "acquire":
        releasing = await asyncio.wait_for(workers.get(), timeout=2)
        await asyncio.wait({releasing})

    connection.close.assert_called_once_with()
    if failure is None:
        report.assert_not_called()
        connection.invalidate.assert_not_called()
    else:
        connection.invalidate.assert_called_once_with()
        report.assert_called_once()
        (error,) = report.call_args.args
        operation = "paper_lock_acquire_failed_after_cancel" if failure == "acquire" else "paper_lock_release_failed"
        assert report.call_args.kwargs == {
            "component": "document_files", "operation": operation, "level": "warning", "fingerprint": [operation],
        }
        assert type(db_error).__name__ in str(error)
        assert error.__traceback__ is not None
        assert error.__cause__ is error.__context__ is None
        warnings = [record for record in caplog.records if record.name == paper_lock.__name__]
        assert any(type(db_error).__name__ in record.getMessage() for record in warnings)
        assert all(getattr(record, "sentry_skip_event", False) and record.exc_info is None for record in warnings)

    for sensitive in (str(db_error), str(capture_error), str(document_id), str(paper_lock.paper_lock_key(document_id))):
        assert sensitive not in repr(report.call_args)
        assert sensitive not in caplog.text
