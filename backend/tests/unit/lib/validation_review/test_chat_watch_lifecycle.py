"""Review work must finish even when cancellation polling absorbs cancellation."""

from tests.cancellation_test_support import install_cancel_reader
import asyncio
from unittest.mock import Mock

import pytest

from src.lib.validation_review.chat import ChatReview
from src.lib.validation_review.admission import ReviewStopped
from src.lib.validation_review.stops import ReviewInterrupted


@pytest.mark.asyncio
@pytest.mark.parametrize("reporter_fails", [False, True])
async def test_stop_reports_simultaneous_work_failure(monkeypatch, reporter_fails, caplog):
    work = asyncio.get_running_loop().create_future()
    failure = RuntimeError("FAKE sensitive database parameters")

    async def stop(_session):
        work.set_exception(failure)
        return True

    install_cancel_reader(monkeypatch, stop)
    capture = Mock(return_value=True)
    if reporter_fails:
        capture.side_effect = RuntimeError("FAKE sensitive telemetry message")
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", capture)
    context = ChatReview("owner", "doc", "session", "turn", None, (), "review")

    with pytest.raises(ReviewInterrupted):
        await context.until_stop(work)

    assert context.interrupted
    capture.assert_called_once()
    (safe,), details = capture.call_args
    assert str(safe) == "Validation review operational failure (RuntimeError)"
    assert safe.__context__ is safe.__cause__ is None
    assert details == {"component": "validation_review", "operation": "execute_review_work"}
    assert "FAKE sensitive" not in caplog.text


@pytest.mark.asyncio
async def test_stop_discards_expected_review_stop_quietly(monkeypatch):
    work = asyncio.get_running_loop().create_future()

    async def stop(_session):
        work.set_exception(ReviewStopped("worker_lease_lost"))
        return True

    install_cancel_reader(monkeypatch, stop)
    capture = Mock()
    monkeypatch.setattr("src.lib.validation_review.audit.report_operational_failure", capture)
    context = ChatReview("owner", "doc", "session", "turn", None, (), "review")
    with pytest.raises(ReviewInterrupted):
        await context.until_stop(work)
    capture.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "caller_cancel"])
@pytest.mark.parametrize("absorb_cancellation", [False, True])
async def test_review_joins_watcher_after_work_finishes(
    monkeypatch, outcome, absorb_cancellation
):
    probe_entered = asyncio.Event()
    work_started = asyncio.Event()
    work_finished = asyncio.Event()
    absorbed_cancel = asyncio.Event()
    watcher_tasks = []

    async def check_cancel(_session):
        watcher_tasks.append(asyncio.current_task())
        probe_entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if not absorb_cancellation or absorbed_cancel.is_set():
                raise
            absorbed_cancel.set()
            return False

    install_cancel_reader(monkeypatch, check_cancel)

    async def work():
        work_started.set()
        try:
            await probe_entered.wait()
            if outcome == "caller_cancel":
                await asyncio.Event().wait()
            if outcome == "error":
                raise ValueError("review work failed")
            return "review finished"
        finally:
            work_finished.set()

    context = ChatReview("owner", "doc", "session", "turn", None, (), "review")
    operation = asyncio.create_task(context.until_stop(work()))
    await work_started.wait()
    await probe_entered.wait()
    if outcome == "caller_cancel":
        operation.cancel()
    try:
        if outcome == "error":
            with pytest.raises(ValueError, match="review work failed"):
                await asyncio.wait_for(operation, timeout=1)
        elif outcome == "caller_cancel":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, timeout=1)
        else:
            assert await asyncio.wait_for(operation, timeout=1) == "review finished"
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)

    assert absorbed_cancel.is_set() is absorb_cancellation
    assert work_finished.is_set()
    assert len(watcher_tasks) == 1
    assert watcher_tasks[0].done()
