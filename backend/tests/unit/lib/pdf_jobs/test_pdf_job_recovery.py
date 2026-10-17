"""Recovery lifecycle, shutdown ownership and sanitized reporting."""
import asyncio
import threading
from unittest.mock import Mock

import pytest

from src.lib.pdf_jobs import recovery


@pytest.mark.asyncio
async def test_start_once_and_shutdown_waits_for_worker(monkeypatch):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def sweep(**kwargs):
        calls.append(kwargs)
        entered.set()
        try:
            assert release.wait(5)
        finally:
            closed.set()

    monkeypatch.setattr(recovery, "reconcile_stale_jobs_once", sweep)
    task = recovery.schedule_pdf_job_recovery()
    assert recovery.schedule_pdf_job_recovery() is task
    assert await asyncio.to_thread(entered.wait, 5)
    stop = asyncio.create_task(recovery.stop_pdf_job_recovery())
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    await stop
    assert closed.is_set()
    assert len(calls) == 1
    assert recovery._recovery_task is None
    await recovery.stop_pdf_job_recovery()


@pytest.mark.asyncio
async def test_failure_is_sanitized_and_next_tick_retries(monkeypatch, caplog):
    calls = []
    captured = Mock()
    tick = asyncio.Event()

    loop = asyncio.get_running_loop()
    def sweep_with_loop(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("secret-token curator-content SQL params")
        loop.call_soon_threadsafe(tick.set)

    monkeypatch.setattr(recovery, "reconcile_stale_jobs_once", sweep_with_loop)
    monkeypatch.setattr(recovery, "report_runtime_exception", captured)
    monkeypatch.setattr(recovery, "get_pdf_job_recovery_interval_seconds", lambda: 0)
    recovery.schedule_pdf_job_recovery()
    await asyncio.wait_for(tick.wait(), 5)
    await recovery.stop_pdf_job_recovery()
    error = captured.call_args.args[0]
    assert error.__traceback__
    assert error.__context__ is None and error.__cause__ is None
    assert "secret-token" not in str(error) + caplog.text
    assert "curator-content" not in str(error) + caplog.text
    assert captured.call_count == 1
    assert len(calls) >= 2


@pytest.mark.parametrize("failure", ["query", "commit", "competing_owner"])
def test_pass_session_cleanup_and_rollback(monkeypatch, failure):
    from tests.unit.lib.pdf_jobs.test_pdf_job_service import _FakeSession

    responses = [None, True, []]
    if failure == "query":
        responses = [RuntimeError("database unavailable")]
    elif failure == "competing_owner":
        responses = [None, False]
    session = _FakeSession(
        responses,
        commit_error=RuntimeError("commit failed") if failure == "commit" else None,
    )
    monkeypatch.setattr(recovery, "SessionLocal", lambda: session)
    if failure == "competing_owner":
        assert recovery.reconcile_stale_jobs_once() is None
        assert session.commit_calls == 0
    else:
        with pytest.raises(RuntimeError):
            recovery.reconcile_stale_jobs_once()
        assert session.rollback_calls == 1
    assert session.closed
