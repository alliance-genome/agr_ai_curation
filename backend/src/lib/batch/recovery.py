"""Startup dispatch for durable batch crash recovery."""

import asyncio
import logging

from src.models.sql.database import SessionLocal

from .processor import process_batch_task
from .service import BatchService


logger = logging.getLogger(__name__)
_recovery_tasks: set[asyncio.Task[None]] = set()


def _finish_recovery_task(task: asyncio.Task[None]) -> None:
    """Retain no completed task while reporting unexpected worker failures."""
    _recovery_tasks.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.error(
            "Recovered batch worker failed",
            exc_info=(type(error), error, error.__traceback__),
        )


def schedule_startup_batch_recovery() -> int:
    """Scan persisted work and dispatch lease-contending recovery workers."""
    with SessionLocal() as db:
        batch_ids = BatchService(db).list_recoverable_batch_ids()

    for batch_id in batch_ids:
        task = asyncio.create_task(
            asyncio.to_thread(process_batch_task, batch_id),
            name=f"recover-batch-{batch_id}",
        )
        _recovery_tasks.add(task)
        task.add_done_callback(_finish_recovery_task)

    if batch_ids:
        logger.info("Scheduled %d persisted batch(es) for recovery", len(batch_ids))
    return len(batch_ids)
