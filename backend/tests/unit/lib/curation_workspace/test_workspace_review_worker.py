"""Lost ownership stops execution; read-only reopening never owns a worker."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.lib.curation_workspace import review_operations as operations
from src.lib.curation_workspace import review_observability as observability
from src.lib.context import get_current_trace_id, set_current_trace_id
from src.lib.openai_agents.extraction_trace_events import (
    get_current_extraction_trace_run,
)


@pytest.mark.asyncio
async def test_lost_operation_lease_cancels_only_its_running_task(monkeypatch):
    stopped = asyncio.Event()

    async def execute(*args):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(operations, "execute", execute)
    monkeypatch.setattr(operations, "heartbeat", lambda *args: False)
    monkeypatch.setattr(
        operations.ReviewLimits,
        "configured",
        lambda: SimpleNamespace(lease_seconds=0.03),
    )
    await operations.run_claimed(("operation", "lease"))
    assert stopped.is_set()
    # The worker loop itself remains usable for the next independent operation.
    next_run = AsyncMock()
    monkeypatch.setattr(operations, "execute", next_run)
    await operations.run_claimed(("next", "next-lease"))
    next_run.assert_awaited_once_with("next", "next-lease")


def test_workspace_trace_has_durable_identity_without_a_langfuse_connection(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    monkeypatch.setattr(observability, "get_langfuse", lambda: None)
    set_current_trace_id(None)
    with observability.workspace_review_trace(
        operation_id="round", session_id="workspace", owner_id="private-curator"
    ) as trace_id:
        assert get_current_trace_id() == trace_id
        assert get_current_extraction_trace_run().session_id == "workspace"
        saved = (tmp_path / (trace_id + ".jsonl")).read_text()
        assert "validation_review.workspace_operation" in saved
        assert "private-curator" not in saved
    assert get_current_trace_id() is None
    assert get_current_extraction_trace_run() is None
