"""Unit tests for the Weaviate storage helpers."""

import asyncio
import importlib
import logging
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

class _StoreModuleProxy:
    """Resolve pipeline.store lazily so patches hit the active module instance."""

    @staticmethod
    def _module():
        return importlib.import_module("src.lib.pipeline.store")

    def __getattr__(self, name):
        return getattr(self._module(), name)

    def __setattr__(self, name, value):
        setattr(self._module(), name, value)


store_module = _StoreModuleProxy()


def store_to_weaviate(*args, **kwargs):
    return store_module.store_to_weaviate(*args, **kwargs)


def store_chunks_to_weaviate(*args, **kwargs):
    return store_module.store_chunks_to_weaviate(*args, **kwargs)


def update_document_metadata(*args, **kwargs):
    return store_module.update_document_metadata(*args, **kwargs)


def generate_deterministic_uuid(*args, **kwargs):
    return store_module.generate_deterministic_uuid(*args, **kwargs)


@pytest.fixture(autouse=True)
def disable_store_sleep(monkeypatch):
    monkeypatch.setattr(store_module.time, "sleep", lambda _: None)


def test_generate_deterministic_uuid_is_stable():
    doc_id = "doc-1"
    content = "A long paragraph of text"
    value = generate_deterministic_uuid(doc_id, 0, content)
    assert value == generate_deterministic_uuid(doc_id, 0, content)


@pytest.mark.asyncio
async def test_store_to_weaviate_requires_chunks():
    with pytest.raises(store_module.StorageError, match="No chunks to store"):
        await store_to_weaviate([], "doc-1", None, "test_user", chunk_range=(0, 0))


@pytest.mark.asyncio
async def test_store_to_weaviate_calls_status_updates():
    chunks = [{"chunk_index": 0, "content": "hello"}]
    document_id = "doc-1"

    weaviate_client = MagicMock()

    async def fake_chunks_store(chunks_arg, doc_id, client, user_id, *, chunk_range):  # noqa: ANN001
        assert doc_id == document_id
        assert chunks_arg == chunks
        assert user_id == "test_user"
        assert chunk_range == (0, 0)
        return {"stored_count": 1, "failed_count": 0, "stored_ids": ["uuid"]}

    with patch("src.lib.pipeline.store.store_chunks_to_weaviate", new=fake_chunks_store), patch(
        "src.lib.pipeline.store.update_document_metadata", new=AsyncMock()
    ) as mock_metadata, patch("src.lib.pipeline.store.update_document_status_detailed", new=AsyncMock()) as mock_status:
        stats = await store_to_weaviate(
            chunks, document_id, weaviate_client, "test_user", chunk_range=(0, 0)
        )

    assert stats["stored_chunks"] == 1
    mock_status.assert_awaited_once()
    # The paper's summed piece count is written once the paper finishes, not per file.
    mock_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_to_weaviate_marks_failed_and_raises_when_storage_fails(caplog):
    chunks = [{"chunk_index": 0, "content": "hello"}]
    document_id = "doc-1"
    weaviate_client = MagicMock()

    async def fake_chunks_store(chunks_arg, doc_id, client, user_id, *, chunk_range):  # noqa: ANN001
        raise store_module.BatchInsertError(
            "failed", failed_objects=[{"chunk_index": 0}]
        )

    with patch("src.lib.pipeline.store.store_chunks_to_weaviate", new=fake_chunks_store), patch(
        "src.lib.pipeline.store.update_document_metadata", new=AsyncMock()
    ) as mock_metadata, patch("src.lib.pipeline.store.update_document_status_detailed", new=AsyncMock()) as mock_status:
        with pytest.raises(store_module.StorageError, match="Weaviate storage failed: failed"):
            await store_to_weaviate(chunks, document_id, weaviate_client, "test_user", chunk_range=(0, 0))

    failure_log, = [r for r in caplog.records if r.name == store_module.logger.name and r.levelno == logging.ERROR]
    assert not getattr(failure_log, "sentry_skip_event", False)
    assert mock_status.await_count == 2
    assert mock_status.await_args_list[1].kwargs["embedding_status"] == "failed"
    mock_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_chunks_to_weaviate_raises_when_collection_missing():
    weaviate_client = MagicMock()
    session_client = MagicMock()

    @contextmanager
    def fake_session():
        yield session_client

    weaviate_client.session.side_effect = fake_session
    session_client.collections.get.side_effect = Exception("missing")

    event_loop = MagicMock()

    calls = []

    def run_executor(_, func):
        calls.append("called")
        func()
        return asyncio.sleep(0)

    event_loop.run_in_executor.side_effect = run_executor

    with patch("src.lib.pipeline.store.asyncio.get_event_loop", return_value=event_loop):
        with pytest.raises(store_module.CollectionNotFoundError):
            await store_chunks_to_weaviate(
                [{"chunk_index": 0, "content": "alpha"}], "doc-1", weaviate_client, "test_user",
                chunk_range=(0, 0),
            )


@pytest.mark.asyncio
async def test_store_chunks_to_weaviate_success_path():
    chunks = [
        {
            "chunk_index": 0,
            "content": "alpha",
            "metadata": {
                "figure_locator_resolution": {
                    "schema_version": 1,
                    "prompt_version": "figure-locator-v1",
                    "model": "gpt-6.1-sol",
                    "reasoning": "low",
                    "status": "resolved",
                    "annotations": [
                        {
                            "text": "Figure 1A",
                            "char_start": 0,
                            "char_end": 9,
                            "cardinality": "single",
                            "kind": "figure",
                            "number": "1",
                            "panels": ["A"],
                            "canonical_reference": "Figure 1A",
                        }
                    ],
                },
                "doc_items": [
                    {
                        "element_id": "el-1",
                        "page": 1,
                        "bbox": {"left": 0.1, "top": 0.9, "right": 0.5, "bottom": 0.8, "coord_origin": "BOTTOMLEFT"},
                    }
                ]
            },
            "doc_items": [
                {
                    "element_id": "el-1",
                    "page": 1,
                    "bbox": {"left": 0.1, "top": 0.9, "right": 0.5, "bottom": 0.8, "coord_origin": "BOTTOMLEFT"},
                }
            ],
        },
        {"chunk_index": 1, "content": "beta", "metadata": {}},
    ]

    weaviate_client = MagicMock()
    chunk_collection = MagicMock()
    pdf_collection = MagicMock()
    batch_ctx = MagicMock()

    @contextmanager
    def fake_session():
        yield MagicMock()

    weaviate_client.session.side_effect = fake_session
    chunk_collection.batch.rate_limit.return_value.__enter__.return_value = batch_ctx
    batch_ctx.number_errors = 0
    chunk_collection.aggregate.over_all.return_value = MagicMock(total_count=2)  # For verification
    batch_ctx.failed_objects = []

    event_loop = MagicMock()

    def run_executor(_, func):
        func()
        return asyncio.sleep(0)

    event_loop.run_in_executor.side_effect = run_executor

    # Mock get_user_collections to return tenant-scoped collections
    with patch("src.lib.pipeline.store.asyncio.get_event_loop", return_value=event_loop), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        result = await store_chunks_to_weaviate(
            chunks, "doc-1", weaviate_client, "test_user", chunk_range=(0, 1)
        )

    assert result["stored_count"] == 2
    assert batch_ctx.add_object.call_count == 2

    # Provenance is serialized to JSON string for TEXT field storage
    provenance_properties = batch_ctx.add_object.call_args_list[0].kwargs["properties"].get("docItemProvenance")
    import json
    assert json.loads(provenance_properties) == chunks[0]["doc_items"]
    stored_metadata = json.loads(
        batch_ctx.add_object.call_args_list[0].kwargs["properties"]["metadata"]
    )
    assert (
        stored_metadata["figure_locator_resolution"]["annotations"][0][
            "canonical_reference"
        ]
        == "Figure 1A"
    )


@pytest.mark.asyncio
async def test_store_chunks_to_weaviate_raises_when_batch_reports_failed_objects():
    chunks = [
        {"chunk_index": 0, "content": "alpha", "metadata": {}},
        {"chunk_index": 1, "content": "beta", "metadata": {}},
    ]

    weaviate_client = MagicMock()
    chunk_collection = MagicMock()
    pdf_collection = MagicMock()
    batch_ctx = MagicMock()
    chunk_collection.batch.rate_limit.return_value.__enter__.return_value = batch_ctx
    batch_ctx.number_errors = 0
    batch_ctx.failed_objects = [{"message": "openai token overflow"}]

    @contextmanager
    def fake_session():
        yield MagicMock()

    weaviate_client.session.side_effect = fake_session

    event_loop = MagicMock()

    def run_executor(_, func):
        func()
        return asyncio.sleep(0)

    event_loop.run_in_executor.side_effect = run_executor

    with patch("src.lib.pipeline.store.asyncio.get_event_loop", return_value=event_loop), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        with pytest.raises(store_module.BatchInsertError):
            await store_chunks_to_weaviate(chunks, "doc-1", weaviate_client, "test_user", chunk_range=(0, 1))

    assert batch_ctx.add_object.call_count == 2


@pytest.mark.asyncio
async def test_store_chunks_to_weaviate_raises_when_batch_add_object_fails():
    chunks = [{"chunk_index": 0, "content": "alpha", "metadata": {}}]

    weaviate_client = MagicMock()
    chunk_collection = MagicMock()
    pdf_collection = MagicMock()
    batch_ctx = MagicMock()
    chunk_collection.batch.rate_limit.return_value.__enter__.return_value = batch_ctx
    batch_ctx.number_errors = 0
    batch_ctx.add_object.side_effect = Exception("single add_object failed")
    batch_ctx.failed_objects = []

    @contextmanager
    def fake_session():
        yield MagicMock()

    weaviate_client.session.side_effect = fake_session

    event_loop = MagicMock()
    event_loop.run_in_executor.side_effect = lambda _, func: asyncio.sleep(0, result=func())

    with patch("src.lib.pipeline.store.asyncio.get_event_loop", return_value=event_loop), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        with pytest.raises(store_module.BatchInsertError):
            await store_chunks_to_weaviate(chunks, "doc-1", weaviate_client, "test_user", chunk_range=(0, 0))


@pytest.mark.asyncio
async def test_store_chunks_to_weaviate_raises_when_token_preflight_exceeds_limit(monkeypatch):
    chunks = [{"chunk_index": 0, "content": "alpha", "metadata": {}}]
    weaviate_client = MagicMock()
    chunk_collection = MagicMock()
    pdf_collection = MagicMock()
    batch_ctx = MagicMock()
    chunk_collection.batch.rate_limit.return_value.__enter__.return_value = batch_ctx
    batch_ctx.number_errors = 0
    batch_ctx.failed_objects = []
    chunk_collection.aggregate.over_all.return_value = MagicMock(total_count=1)

    class FakeEncoder:
        def encode(self, value):  # noqa: ANN001
            return list(range(store_module.TOKEN_HARD_LIMIT + 10))

    monkeypatch.setattr(store_module, "_tiktoken_encoder", FakeEncoder())

    @contextmanager
    def fake_session():
        yield MagicMock()

    weaviate_client.session.side_effect = fake_session

    event_loop = MagicMock()
    event_loop.run_in_executor.side_effect = lambda _, func: asyncio.sleep(0, result=func())

    with patch("src.lib.pipeline.store.asyncio.get_event_loop", return_value=event_loop), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        with pytest.raises(store_module.BatchInsertError):
            await store_chunks_to_weaviate(chunks, "doc-1", weaviate_client, "test_user", chunk_range=(0, 0))

    assert batch_ctx.add_object.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_at", "reporter_fails"),
    [("update", False), ("session", False), ("update", True)],
)
async def test_update_document_metadata_best_effort(caplog, failure_at, reporter_fails):
    weaviate_client = MagicMock()
    pdf_collection = MagicMock()
    failure = OSError("fake-private-index-detail")
    if failure_at == "session":
        weaviate_client.session.side_effect = failure
    else:
        pdf_collection.data.update.side_effect = failure

    with (
        patch("src.lib.weaviate_helpers.get_user_collections", return_value=(MagicMock(), pdf_collection)),
        patch("src.lib.pipeline.store.report_runtime_exception") as report,
    ):
        if reporter_fails:
            report.side_effect = RuntimeError("fake-private-capture-detail")
        await update_document_metadata("doc-1", {"stored_chunks": 2}, weaviate_client, "test_user")

    if failure_at == "update":
        pdf_collection.data.update.assert_called_once()
    report.assert_called_once()
    error, = report.call_args.args
    assert str(error) == "Final index metadata write failed"
    assert error.__cause__ is None and error.__context__ is None
    assert report.call_args.kwargs == {
        "component": "document_pipeline",
        "operation": "pipeline_final_index_metadata_write_failed",
        "context": {"exception_type": "OSError"},
    }
    assert "fake-private-index-detail" not in caplog.text
    assert "fake-private-capture-detail" not in caplog.text
    warnings = [
        record for record in caplog.records
        if record.name == store_module.logger.name and record.levelno >= logging.WARNING
    ]
    assert len(warnings) == (2 if reporter_fails else 1)
    assert all(record.sentry_skip_event for record in warnings)


def test_metadata_connection_failure_emits_one_sanitized_event():
    # Isolate the real Sentry logging integration and its process-wide hooks.
    script = textwrap.dedent("""
        import asyncio
        import logging
        from unittest.mock import patch

        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        from src.lib.observability.sentry import before_send
        from src.lib.pipeline.store import update_document_metadata
        from src.lib.weaviate_client.connection import WeaviateConnection

        events = []
        logging.basicConfig(level=logging.INFO)
        sentry_sdk.init(
            dsn="http://public@example.invalid/1",
            transport=events.append,
            before_send=before_send,
            include_local_variables=False,
            default_integrations=False,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
        )
        failure = OSError("fake-private-sdk-detail")
        with patch("weaviate.connect_to_local", side_effect=failure) as connect:
            asyncio.run(update_document_metadata(
                "private-document", {"stored_chunks": 2}, WeaviateConnection(), "private-user"
            ))
            connect.assert_called_once()

        event, = events
        assert event["contexts"]["runtime_exception"] == {
            "component": "document_pipeline",
            "operation": "pipeline_final_index_metadata_write_failed",
            "exception_type": "OSError",
        }
        error, = event["exception"]["values"]
        assert error["value"] == "Final index metadata write failed"
        assert all(secret not in repr(event) for secret in (str(failure), "private-document", "private-user"))
    """)
    result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "fake-private-sdk-detail" not in result.stdout + result.stderr


@pytest.mark.asyncio
async def test_update_document_metadata_success():
    weaviate_client = MagicMock()
    pdf_collection = MagicMock()
    with (
        patch("src.lib.weaviate_helpers.get_user_collections", return_value=(MagicMock(), pdf_collection)) as collections,
        patch("src.lib.pipeline.store.report_runtime_exception") as report,
    ):
        await update_document_metadata("doc-1", {"stored_chunks": 2}, weaviate_client, "test_user")

    collections.assert_called_once_with(weaviate_client.session.return_value.__enter__.return_value, "test_user")
    pdf_collection.data.update.assert_called_once_with(
        uuid="doc-1",
        properties={"chunkCount": 2, "vectorCount": 2, "processingStatus": "completed", "embeddingStatus": "completed"},
    )
    report.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("write_fails", "reporter_fails"),
    [(False, False), (True, False), (True, True)],
    ids=["successful-write", "failed-write", "failed-write-and-reporter"],
)
async def test_cancelled_storage_drains_worker_before_failure_and_retry(
    monkeypatch, caplog, write_fails, reporter_fails,
):
    """A deadline must not let a late executor write resurrect terminal state."""
    import threading
    from types import SimpleNamespace

    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    persisted = {}
    properties = {"chunkCount": 0, "vectorCount": 0}
    write_detail = "fake-private-chunk-write"
    report = MagicMock(side_effect=RuntimeError("fake-private-capture-detail") if reporter_fails else None)
    monkeypatch.setattr(store_module, "report_runtime_exception", report)
    batch = MagicMock(number_errors=0, failed_objects=[])

    def insert(*, uuid, properties):
        if not release.is_set():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "test did not release storage worker"
            if write_fails:
                raise OSError(write_detail)
        persisted[uuid] = properties

    batch.add_object.side_effect = insert
    chunks_collection = MagicMock()
    chunks_collection.batch.rate_limit.return_value.__enter__.return_value = batch
    chunks_collection.aggregate.over_all.side_effect = lambda **_: SimpleNamespace(total_count=len(persisted))
    pdf_collection = MagicMock()

    connection = MagicMock()
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", lambda *_: (chunks_collection, pdf_collection))

    async def status(_document, _user, *, embedding_status=None, processing_status=None):
        if embedding_status:
            properties["embeddingStatus"] = embedding_status
        if processing_status:
            properties["processingStatus"] = processing_status

    monkeypatch.setattr(store_module, "update_document_status_detailed", status)
    chunks = [{"chunk_index": 0, "content": "alpha"}, {"chunk_index": 1, "content": "beta"}]
    task = asyncio.create_task(store_to_weaviate(chunks, "doc-1", connection, "test_user", chunk_range=(0, 1)))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel("original cancellation")
        # Synchronize with the cancellation handler without a wall-clock sleep.
        for _ in range(5):
            await asyncio.sleep(0)
        assert not task.done(), "failure was exposed while storage could still write"
        task.cancel()  # Repeated shutdown cancellation must not detach the writer.
        await asyncio.sleep(0)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError, match="^original cancellation$"):
            await task
    # A cancelled import is marked failed; its summed count is never written.
    assert properties == {
        "chunkCount": 0, "vectorCount": 0,
        "processingStatus": "failed", "embeddingStatus": "failed",
    }
    assert len(persisted) == (0 if write_fails else 2)
    pdf_collection.data.update.assert_not_called()

    if write_fails:
        report.assert_called_once()
        error, = report.call_args.args
        assert isinstance(error, RuntimeError)
        assert str(error) == "Chunk storage failed during cancellation"
        assert error.__cause__ is None and error.__context__ is None
        assert report.call_args.kwargs == {
            "component": "document_pipeline",
            "operation": "pipeline_cancelled_storage_write_failed",
            "context": {"exception_type": "StorageError"},
        }
        assert write_detail not in repr(report.call_args)
        warnings = [
            r for r in caplog.records if r.name == store_module.logger.name and r.levelno >= logging.WARNING
        ]
        assert all(getattr(r, "sentry_skip_event", False) for r in warnings)
        drain_logs = [r for r in warnings if r.funcName == "finish_cancelled_storage"]
        expected_messages = ["Chunk storage failed during cancellation"]
        if reporter_fails:
            expected_messages.append("Chunk storage failure reporting unavailable during cancellation")
        assert [r.getMessage() for r in drain_logs] == expected_messages
        assert all(r.exc_info is None for r in drain_logs)
        assert write_detail not in caplog.text
        assert "fake-private-capture-detail" not in caplog.text
    else:
        report.assert_not_called()

    # The same deterministic chunk IDs are reused on a retry of the same range.
    result = await store_to_weaviate(chunks, "doc-1", connection, "test_user", chunk_range=(0, 1))
    assert result["stored_chunks"] == len(persisted) == 2
    assert properties["embeddingStatus"] == "processing"


def _storage_fixture(total_count):
    weaviate_client = MagicMock()
    chunk_collection = MagicMock()
    batch_ctx = MagicMock(number_errors=0, failed_objects=[])
    chunk_collection.batch.rate_limit.return_value.__enter__.return_value = batch_ctx
    chunk_collection.aggregate.over_all.return_value = MagicMock(total_count=total_count)

    @contextmanager
    def fake_session():
        yield MagicMock()

    weaviate_client.session.side_effect = fake_session
    event_loop = MagicMock()
    event_loop.run_in_executor.side_effect = lambda _, func: asyncio.sleep(0, result=func())
    return weaviate_client, chunk_collection, batch_ctx, event_loop


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("indexes", "chunk_range"),
    [([7, 9], (7, 8)), ([7, 8], (7, 9)), ([0, 1], (7, 8))],
)
async def test_store_chunks_outside_or_mismatching_the_range_write_nothing(indexes, chunk_range):
    chunks = [{"chunk_index": index, "content": f"piece {index}"} for index in indexes]
    weaviate_client, chunk_collection, batch_ctx, event_loop = _storage_fixture(len(chunks))

    with patch("src.lib.pipeline.store.asyncio.get_event_loop", return_value=event_loop), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())):
        with pytest.raises(store_module.StorageError):
            await store_chunks_to_weaviate(chunks, "doc-1", weaviate_client, "test_user", chunk_range=chunk_range)

    batch_ctx.add_object.assert_not_called()
    weaviate_client.session.assert_not_called()


@pytest.mark.asyncio
async def test_store_verification_counts_only_the_files_range():
    chunks = [{"chunk_index": 7, "content": "seven"}, {"chunk_index": 8, "content": "eight"}]
    weaviate_client, chunk_collection, batch_ctx, event_loop = _storage_fixture(2)

    with patch("src.lib.pipeline.store.asyncio.get_event_loop", return_value=event_loop), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())):
        result = await store_chunks_to_weaviate(chunks, "doc-1", weaviate_client, "test_user", chunk_range=(7, 8))

    assert result["stored_count"] == 2
    kwargs = chunk_collection.aggregate.over_all.call_args.kwargs
    assert kwargs["total_count"] is True
    def leaves(node):
        children = getattr(node, "filters", None)
        if isinstance(children, list):
            return [leaf for child in children for leaf in leaves(child)]
        return [(node.target, node.operator.name, node.value)]

    assert sorted(leaves(kwargs["filters"]), key=repr) == sorted([
        ("documentId", "EQUAL", "doc-1"),
        ("chunkIndex", "GREATER_THAN_EQUAL", 7),
        ("chunkIndex", "LESS_THAN_EQUAL", 8),
    ], key=repr)
    chunk_collection.query.fetch_objects.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("total_count", [1, None])
async def test_store_verification_mismatch_or_missing_count_raises(total_count):
    chunks = [{"chunk_index": 7, "content": "seven"}, {"chunk_index": 8, "content": "eight"}]
    weaviate_client, chunk_collection, batch_ctx, event_loop = _storage_fixture(total_count)

    with patch("src.lib.pipeline.store.asyncio.get_event_loop", return_value=event_loop), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())):
        with pytest.raises(store_module.BatchInsertError):
            await store_chunks_to_weaviate(chunks, "doc-1", weaviate_client, "test_user", chunk_range=(7, 8))
