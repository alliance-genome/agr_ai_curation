"""Controlled-tick SSE contract and worker cancellation coverage."""
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.api import pdf_jobs
from src.lib.pdf_jobs import service
from tests.unit.lib.pdf_jobs.test_pdf_job_service import _build_job, _FakeSession


@pytest.mark.asyncio
async def test_content_changes_empty_transition_suppression_and_timeout(monkeypatch):
    job = service._to_response(_build_job(status="running"))
    changed = job.model_copy(update={"message": "Embedding", "metadata": {"new": True}})
    cancelled = changed.model_copy(update={"cancel_requested": True})
    snapshots = [[], [job], [job], [changed], [cancelled], [], []]
    calls = []
    clock = [0]
    main_thread = threading.get_ident()

    def snapshot(**kwargs):
        assert threading.get_ident() != main_thread
        calls.append(kwargs)
        return snapshots.pop(0)

    async def sleep(_seconds):
        clock[0] += 5

    monkeypatch.setattr(pdf_jobs, "_resolve_db_user_id", AsyncMock(return_value=42))
    monkeypatch.setattr(service, "snapshot_jobs", snapshot)
    monkeypatch.setattr(pdf_jobs, "asyncio", SimpleNamespace(
        sleep=sleep,
        get_event_loop=lambda: SimpleNamespace(time=lambda: clock[0]),
    ))
    monkeypatch.setenv("PDF_JOBS_STREAM_TIMEOUT_SECONDS", "35")
    response = await pdf_jobs.stream_pdf_jobs(user={"sub": "owner"}, window_days=7, limit=3)
    frames = [json.loads(frame.removeprefix("data: ")) async for frame in response.body_iterator]
    assert len(frames) == 5
    assert frames[0]["jobs"][0]["message"] == job.message
    assert frames[1]["jobs"][0]["message"] == "Embedding"
    assert frames[2]["jobs"][0]["cancel_requested"] is True
    assert frames[3]["jobs"] == []
    assert frames[4]["final"] is True
    assert frames[4]["message"] == "PDF jobs stream timed out"
    assert calls == [{"user_id": 42, "window_days": 7, "limit": 3}] * 7


@pytest.mark.asyncio
async def test_cancel_inflight_snapshot_eventually_closes_session_without_next_tick(monkeypatch):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    calls = []
    capture = Mock()
    monkeypatch.setattr(service, "report_runtime_exception", capture)

    class Session(_FakeSession):
        def execute(self, statement):
            calls.append(statement)
            entered.set()
            assert release.wait(5)
            return super().execute(statement)

        def close(self):
            super().close()
            closed.set()

    session = Session([[]])
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(pdf_jobs, "_resolve_db_user_id", AsyncMock(return_value=42))
    response = await pdf_jobs.stream_pdf_jobs(user={}, window_days=7, limit=3)
    pending = asyncio.create_task(anext(response.body_iterator))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not closed.is_set()
    finally:
        release.set()
        assert await asyncio.to_thread(closed.wait, 5)
        await asyncio.gather(*service._pending_snapshots)
        await asyncio.sleep(0)
    assert len(calls) == 1
    assert session.closed
    capture.assert_not_called()
    assert not service._pending_snapshots


@pytest.mark.asyncio
async def test_disconnect_after_yield_does_not_start_another_snapshot(monkeypatch):
    snapshot = Mock(return_value=[service._to_response(_build_job(status="running"))])
    monkeypatch.setattr(pdf_jobs, "_resolve_db_user_id", AsyncMock(return_value=42))
    monkeypatch.setattr(service, "snapshot_jobs", snapshot)
    response = await pdf_jobs.stream_pdf_jobs(user={}, window_days=7, limit=3)
    await anext(response.body_iterator)
    await response.body_iterator.aclose()
    assert snapshot.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["sql", "dto"])
@pytest.mark.parametrize("reporting_fails", [False, True])
async def test_cancelled_snapshot_reports_late_failure(monkeypatch, caplog, failure, reporting_fails):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    reported = asyncio.Event()
    captures, loop_errors = [], []
    sensitive = "SELECT secret FROM documents; password=private curator-text"

    class Session(_FakeSession):
        def execute(self, statement):
            entered.set()
            assert release.wait(5)
            if failure == "sql":
                raise RuntimeError(sensitive)
            return super().execute(statement)

        def close(self):
            super().close()
            closed.set()

    def fail_conversion(_row):
        raise ValueError(sensitive)

    def capture(exc, **kwargs):
        captures.append((exc, kwargs))
        reported.set()
        if reporting_fails:
            raise RuntimeError(sensitive)

    monkeypatch.setattr(service, "report_runtime_exception", capture)
    session = Session([[_build_job(status="running")]])
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(service, "_to_response", fail_conversion)
    monkeypatch.setattr(pdf_jobs, "_resolve_db_user_id", AsyncMock(return_value=42))
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
    response = await pdf_jobs.stream_pdf_jobs(user={}, window_days=7, limit=3)
    pending = asyncio.create_task(anext(response.body_iterator))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not closed.is_set()
        release.set()
        assert await asyncio.to_thread(closed.wait, 5)
        await asyncio.wait_for(reported.wait(), timeout=2)
        await asyncio.sleep(0)
        assert len(captures) == 1
        exc, kwargs = captures[0]
        assert str(exc) == "Abandoned PDF job snapshot failed"
        assert exc.__cause__ is None and exc.__context__ is None
        assert kwargs == {"component": "pdf_jobs", "operation": "abandoned_snapshot_failed"}
        assert sensitive not in repr(captures)
        assert sensitive not in caplog.text
        if reporting_fails:
            assert any(
                record.message == "Failed to report abandoned PDF job snapshot failure"
                and record.sentry_skip_event
                for record in caplog.records
            )
        assert not loop_errors
        assert not service._pending_snapshots
    finally:
        release.set()
        await asyncio.to_thread(closed.wait, 5)
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["sql", "dto"])
async def test_live_snapshot_failure_propagates_without_explicit_capture(monkeypatch, failure):
    error = RuntimeError("private SQL or DTO content")
    session = _FakeSession([error if failure == "sql" else [_build_job(status="running")]])

    def fail_conversion(_row):
        raise error

    capture = Mock()
    monkeypatch.setattr(service, "report_runtime_exception", capture)
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(service, "_to_response", fail_conversion)
    monkeypatch.setattr(pdf_jobs, "_resolve_db_user_id", AsyncMock(return_value=42))
    response = await pdf_jobs.stream_pdf_jobs(user={}, window_days=7, limit=3)
    with pytest.raises(RuntimeError) as raised:
        await anext(response.body_iterator)
    assert raised.value is error
    assert session.closed
    capture.assert_not_called()
    assert not service._pending_snapshots
