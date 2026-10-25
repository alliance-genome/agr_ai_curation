"""Lease-backed workspace queue; a process restart never repeats an unknown call."""

import asyncio
from src.lib.curation_workspace import review_operations
from src.lib.validation_review.audit import report_operational_failure

_tasks: list[asyncio.Task] = []


async def _worker():
    while True:
        try:
            operation = await asyncio.to_thread(review_operations.claim)
            if operation is not None:
                await review_operations.run_claimed(operation)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            report_operational_failure(error)
        await asyncio.sleep(1)  # Internal queue poll; not a workload allowance.


def start():
    from src.lib.openai_agents.config import get_workspace_review_concurrency

    if _tasks:
        raise RuntimeError("Workspace review worker is already running")
    _tasks.extend(
        asyncio.create_task(_worker())
        for _ in range(get_workspace_review_concurrency())
    )


async def stop():
    for task in _tasks:
        task.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks.clear()
