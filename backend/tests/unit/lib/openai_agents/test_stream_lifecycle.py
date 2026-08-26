import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from agents.result import RunResultStreaming

from src.lib.openai_agents.stream_lifecycle import await_streamed_run_completion


@pytest.mark.asyncio
async def test_await_streamed_run_completion_waits_for_sdk_run_loop() -> None:
    release = asyncio.Event()

    async def _run_loop() -> None:
        await release.wait()

    task = asyncio.create_task(_run_loop())
    result = cast(RunResultStreaming, SimpleNamespace(run_loop_task=task))
    waiter = asyncio.create_task(await_streamed_run_completion(result))

    await asyncio.sleep(0)
    assert not waiter.done()

    release.set()
    await waiter
    assert task.done()


@pytest.mark.asyncio
async def test_await_streamed_run_completion_retrieves_late_failure() -> None:
    async def _run_loop() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("late websocket failure")

    task = asyncio.create_task(_run_loop())
    result = cast(RunResultStreaming, SimpleNamespace(run_loop_task=task))

    with pytest.raises(RuntimeError, match="late websocket failure"):
        await await_streamed_run_completion(result)

    assert task.exception() is not None
