import hashlib
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.lib.benchmarks import document_preparation as preparation
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.models.sql.user import User


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["digest", "owner", "disabled", "content"])
async def test_invalid_input_never_writes_artifacts_or_starts_model_storage_work(monkeypatch, invalid):
    content = b'{"user_id":"content-cannot-authorize"}' if invalid == "content" else b"Frozen text"
    context = BenchmarkCuratorContext(
        subject="synthetic-curator", auth_provider="oidc", db_user_id=42, active_groups=(),
    )
    factory = MagicMock()
    factory.return_value.__enter__.return_value.get.return_value = User(
        id=42, auth_sub="different" if invalid == "owner" else context.subject,
        is_active=invalid != "disabled",
    )
    monkeypatch.setattr(preparation, "SessionLocal", factory)
    artifacts = MagicMock()
    create = AsyncMock()
    index = AsyncMock()
    monkeypatch.setattr(preparation, "_write_artifacts", artifacts)
    monkeypatch.setattr(preparation, "create_document", create)
    monkeypatch.setattr(preparation, "index_owned_document_elements", index)
    with pytest.raises((PermissionError, ValueError)):
        await preparation.prepare_frozen_document(
            document_id=uuid4(), content=content,
            content_type="application/json" if invalid == "content" else "text/plain",
            snapshot_digest="bad" if invalid == "digest" else f"sha256:{hashlib.sha256(content).hexdigest()}",
            curator=context, weaviate_client=object(),
            stage_checkpoint=AsyncMock(),
        )
    artifacts.assert_not_called()
    create.assert_not_called()
    index.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["context", "document_owner", "checkpoint"])
async def test_blocked_sql_can_be_cancelled_without_crossing_session_threads(monkeypatch, operation):
    import asyncio
    import threading
    from types import SimpleNamespace

    from src.lib.benchmarks import preparation_service
    from src.lib.benchmarks.preparation_repository import PreparationStageCheckpoint

    loop_thread = threading.get_ident()
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    context = BenchmarkCuratorContext(
        subject="synthetic-curator", auth_provider="oidc", db_user_id=42, active_groups=(),
    )

    class BlockedSession:
        def __init__(self):
            self.thread = threading.get_ident()
            assert self.thread != loop_thread

        def __enter__(self):
            assert threading.get_ident() == self.thread
            return self

        def get(self, *args):
            assert threading.get_ident() == self.thread
            entered.set()
            assert release.wait(5), "test failed to release SQL"
            return SimpleNamespace(
                curator_context=context.model_dump(mode="json"), owner_subject="owner",
                is_active=True, auth_sub=context.subject,
            )

        def scalar(self, *args):
            self.get()
            # A cancelled checkpoint must not proceed to another stage even if
            # the database operation eventually fails after its caller exits.
            raise RuntimeError("synthetic late SQL failure")

        def __exit__(self, *args):
            assert threading.get_ident() == self.thread
            closed.set()

    downstream = AsyncMock()
    if operation == "context":
        monkeypatch.setattr(preparation_service, "authorize_benchmark_curator", downstream)
        pending = preparation_service.prepare_job_document(
            job_id=uuid4(), snapshot_id=uuid4(), lease_owner=uuid4(),
            session_factory=BlockedSession,
        )
    elif operation == "document_owner":
        monkeypatch.setattr(preparation, "SessionLocal", BlockedSession)
        content = b"Frozen text"
        pending = preparation.prepare_frozen_document(
            document_id=uuid4(), content=content, content_type="text/plain",
            snapshot_digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
            curator=context, weaviate_client=object(), stage_checkpoint=downstream,
        )
    else:
        checkpoint = PreparationStageCheckpoint(
            job_id=uuid4(), snapshot_id=uuid4(), document_id=uuid4(), lease_owner=uuid4(),
            session_factory=BlockedSession,
        )
        pending = checkpoint("artifacts")

    task = asyncio.create_task(pending)
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task, timeout=0.02)
        assert not closed.is_set(), "timeout waited for SQL to finish"
        downstream.assert_not_awaited()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(closed.wait, 5)
    downstream.assert_not_awaited()
