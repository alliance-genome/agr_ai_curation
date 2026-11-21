"""Measure whether the event loop keeps running while other work is in progress."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable

# A CPU-bound clean-up long enough that a blocked loop is unmistakable, and a
# bound on the longest loop stall that a responsive loop meets with room to spare.
SLOW_CLEANUP_SECONDS = 1.5
MAX_LOOP_STALL_SECONDS = 0.3


def busy_cpu(seconds: float = SLOW_CLEANUP_SECONDS) -> None:
    """Hold the interpreter in pure-Python work, as a slow text clean-up does."""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        pass


async def run_and_measure_longest_loop_stall(work: Awaitable[Any]) -> tuple[Any, float]:
    """Run ``work`` while a ticker records the longest gap between its wake-ups."""
    task = asyncio.ensure_future(work)
    longest = 0.0
    last = time.perf_counter()
    while not task.done():
        await asyncio.sleep(0.01)
        now = time.perf_counter()
        longest = max(longest, now - last)
        last = now
    return await task, longest
