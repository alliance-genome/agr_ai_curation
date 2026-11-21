"""Run paper text clean-up on one worker thread, off the server's event loop.

Turning converted Markdown into pipeline elements is CPU work that can take
seconds for a large paper. Running it in the event loop would freeze every
request on the backend, and an import deadline could not interrupt it.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Exactly one worker, by design. Do not raise it:
# - A clean-up cannot be stopped once it runs. If its import times out or is
#   cancelled, it finishes in the background and its result is dropped. One
#   worker bounds that abandoned work to a single thread (the backend has a pids
#   limit).
# - The default executor stays free for short blocking calls such as review
#   lease heartbeats.
# - Two CPU-bound clean-ups at once starve the event loop even off it: measured
#   loop stalls of 410 to 886 ms with two at once, against 21 to 71 ms with one
#   (review of ALL-1626, 21 Nov 2026). The clean-up is pure Python under the
#   GIL, so a second worker would not finish clean-ups sooner either.
# Thread safety does not depend on this: the shared Markdown parser builds its
# rule caches at import (``src/lib/markdown_plain_text.py``). Other imports wait
# in this worker's queue; that wait counts against each import's own deadline.
_CLEANUP_WORKERS = 1

_pool_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None
# Clean-ups submitted and not yet finished (queued or running), for the log line.
_in_flight = 0


def _executor() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=_CLEANUP_WORKERS, thread_name_prefix="text-cleanup"
            )
        return _pool


async def run_text_cleanup(
    build: Callable[..., T], /, *args: Any, log_id: str, **kwargs: Any
) -> T:
    """Run ``build(*args, **kwargs)`` on the clean-up worker and await its result.

    ``log_id`` is an internal id (document or artifact) for the timing log line.
    The caller's context variables (error reporting scope, cost attribution)
    carry into the worker. Cancelling the await drops a queued clean-up before it
    starts; a running one finishes and its result is discarded.
    """

    global _in_flight
    queued_at = time.monotonic()
    # The whole worker body, timing log line included, runs in a copy of the
    # caller's context, so the line carries the request, user and trace fields.
    context = contextvars.copy_context()

    def run(ahead: int) -> T:
        started_at = time.monotonic()
        outcome = "failed"
        try:
            result = build(*args, **kwargs)
            outcome = "completed"
            return result
        finally:
            logger.info(
                "Text clean-up %s for %s: %d ahead in queue, waited %.3f s, ran %.3f s",
                outcome,
                log_id,
                ahead,
                started_at - queued_at,
                time.monotonic() - started_at,
            )

    def left_queue(_future: Future) -> None:
        global _in_flight
        with _pool_lock:
            _in_flight -= 1

    with _pool_lock:
        ahead = _in_flight
        _in_flight += 1
    try:
        future = _executor().submit(context.run, run, ahead)
    except BaseException:
        with _pool_lock:
            _in_flight -= 1
        raise
    future.add_done_callback(left_queue)
    return await asyncio.wrap_future(future)


def shutdown_text_cleanup() -> None:
    """Stop the clean-up worker: drop queued clean-ups and wait for a running one.

    A later clean-up starts a new worker, so tests can shut it down after each run.
    """

    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=True)
