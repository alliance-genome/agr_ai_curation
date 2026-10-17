"""Controlled-tick SSE contract and worker cancellation coverage."""
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
        to_thread=asyncio.to_thread, sleep=sleep,
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
    assert len(calls) == 1
    assert session.closed


@pytest.mark.asyncio
async def test_disconnect_after_yield_does_not_start_another_snapshot(monkeypatch):
    snapshot = AsyncMock(return_value=[service._to_response(_build_job(status="running"))])
    monkeypatch.setattr(pdf_jobs, "_resolve_db_user_id", AsyncMock(return_value=42))
    monkeypatch.setattr(pdf_jobs, "asyncio", SimpleNamespace(
        to_thread=snapshot, get_event_loop=asyncio.get_event_loop, sleep=asyncio.sleep,
    ))
    response = await pdf_jobs.stream_pdf_jobs(user={}, window_days=7, limit=3)
    await anext(response.body_iterator)
    await response.body_iterator.aclose()
    assert snapshot.await_count == 1
