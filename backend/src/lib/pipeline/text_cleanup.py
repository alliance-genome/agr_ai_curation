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
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Exactly one worker, by design. Do not raise it:
# - Correctness: the clean-up uses one shared Markdown parser whose rule caches
#   are built lazily and are not safe for concurrent first use. The caches are
#   built at import (``src/lib/markdown_plain_text.py``), and serial clean-ups
#   keep any state the parser does not build up front from being shared at once.
# - A clean-up cannot be stopped once it runs. If its import times out or is
#   cancelled, it finishes in the background and its result is dropped. One
#   worker bounds that to a single thread (the backend has a pids limit).
# - The default executor stays free for short blocking calls such as lease
#   heartbeats.
# The clean-up is pure Python under the GIL, so more workers would not finish
# it sooner. Other imports wait in this worker's queue; that wait counts
# against each import's own deadline.
_CLEANUP_WORKERS = 1

_pool_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None


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

    queued_at = time.monotonic()
    context = contextvars.copy_context()

    def run() -> T:
        started_at = time.monotonic()
        outcome = "failed"
        try:
            result = context.run(build, *args, **kwargs)
            outcome = "completed"
            return result
        finally:
            logger.info(
                "Text clean-up %s for %s: waited %.3f s in queue, ran %.3f s",
                outcome,
                log_id,
                started_at - queued_at,
                time.monotonic() - started_at,
            )

    return await asyncio.get_running_loop().run_in_executor(_executor(), run)


def shutdown_text_cleanup() -> None:
    """Stop the clean-up worker: drop queued clean-ups and wait for a running one.

    A later clean-up starts a new worker, so tests can shut it down after each run.
    """

    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=True)
