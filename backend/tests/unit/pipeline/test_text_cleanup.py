"""The text clean-up worker: one clean-up at a time, off the event loop."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time

import pytest

from src.lib.pipeline import text_cleanup
from src.lib.pipeline.text_cleanup import run_text_cleanup, shutdown_text_cleanup
from tests.event_loop_responsiveness_support import busy_cpu


def _cleanup_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("text-cleanup")]


@pytest.mark.asyncio
async def test_clean_ups_run_one_at_a_time_on_one_thread():
    # Fails if the worker count is raised: the shared Markdown parser is not
    # safe for concurrent first use, and one worker bounds runaway clean-ups.
    spans: list[tuple[str, float, float]] = []

    def clean_up(label: str) -> str:
        start = time.monotonic()
        busy_cpu(0.2)
        spans.append((threading.current_thread().name, start, time.monotonic()))
        return label

    results = await asyncio.gather(
        *(run_text_cleanup(clean_up, f"file-{i}", log_id=f"doc-{i}") for i in range(3))
    )

    assert results == ["file-0", "file-1", "file-2"]
    assert len({name for name, _, _ in spans}) == 1
    ordered = sorted(spans, key=lambda span: span[1])
    for (_, _, previous_end), (_, next_start, _) in zip(ordered, ordered[1:]):
        assert next_start >= previous_end


@pytest.mark.asyncio
async def test_a_cancelled_queued_clean_up_never_starts():
    started: list[str] = []

    def clean_up(label: str) -> str:
        started.append(label)
        busy_cpu(0.3)
        return label

    running = asyncio.ensure_future(run_text_cleanup(clean_up, "running", log_id="doc-1"))
    await asyncio.sleep(0.05)
    queued = asyncio.ensure_future(run_text_cleanup(clean_up, "queued", log_id="doc-2"))
    await asyncio.sleep(0)
    queued.cancel()

    assert await running == "running"
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert started == ["running"]


@pytest.mark.asyncio
async def test_a_timed_out_import_returns_at_once_and_the_worker_stays_usable():
    finished = threading.Event()

    def slow_clean_up() -> str:
        busy_cpu(0.5)
        finished.set()
        return "dropped"

    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(run_text_cleanup(slow_clean_up, log_id="doc-1"), timeout=0.1)
    assert time.monotonic() - started < 0.3
    assert not finished.is_set()

    # The running clean-up cannot be stopped; it finishes and the next one runs after it.
    assert await run_text_cleanup(lambda: "next", log_id="doc-2") == "next"
    assert finished.is_set()


@pytest.mark.asyncio
async def test_the_callers_context_reaches_the_worker():
    scope = contextvars.ContextVar("scope")
    scope.set("import-42")

    assert await run_text_cleanup(scope.get, log_id="doc-1") == "import-42"


@pytest.mark.asyncio
async def test_each_clean_up_logs_queue_wait_and_run_time_without_text(caplog):
    caplog.set_level(logging.INFO, logger=text_cleanup.__name__)

    def failing(text: str) -> None:
        raise ValueError("bad input")

    assert await run_text_cleanup(str.upper, "secret paper text", log_id="doc-1") == "SECRET PAPER TEXT"
    with pytest.raises(ValueError):
        await run_text_cleanup(failing, "secret paper text", log_id="doc-2")

    messages = [r.getMessage() for r in caplog.records if r.name == text_cleanup.__name__]
    assert len(messages) == 2
    assert messages[0].startswith("Text clean-up completed for doc-1: waited ")
    assert messages[1].startswith("Text clean-up failed for doc-2: waited ")
    assert all(" s in queue, ran " in m and "secret" not in m for m in messages)


@pytest.mark.asyncio
async def test_shutdown_leaves_no_clean_up_thread_and_a_later_clean_up_still_runs():
    await run_text_cleanup(str, "x", log_id="doc-1")
    assert len(_cleanup_threads()) == 1

    shutdown_text_cleanup()
    assert _cleanup_threads() == []

    assert await run_text_cleanup(str, 7, log_id="doc-2") == "7"
    shutdown_text_cleanup()
    assert _cleanup_threads() == []
