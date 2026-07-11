"""Unit coverage for startup batch recovery dispatch."""

import asyncio
from contextlib import contextmanager
from uuid import uuid4

import pytest

from src.lib.batch import recovery


@pytest.mark.asyncio
async def test_startup_scan_dispatches_each_persisted_recoverable_batch(monkeypatch):
    batch_ids = [uuid4(), uuid4()]
    processed = []

    @contextmanager
    def session():
        yield object()

    class FakeBatchService:
        def __init__(self, _db):
            pass

        def list_recoverable_batch_ids(self):
            return batch_ids

    monkeypatch.setattr(recovery, "SessionLocal", session)
    monkeypatch.setattr(recovery, "BatchService", FakeBatchService)
    monkeypatch.setattr(recovery, "process_batch_task", processed.append)

    assert recovery.schedule_startup_batch_recovery() == 2
    tasks = list(recovery._recovery_tasks)
    await asyncio.gather(*tasks)

    assert set(processed) == set(batch_ids)
