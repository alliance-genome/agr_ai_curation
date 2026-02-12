"""Unit tests for the Weaviate storage helpers."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.lib.pipeline.store as store_module
from src.lib.pipeline.store import (
    store_to_weaviate,
    store_chunks_to_weaviate,
    update_document_metadata,
    generate_deterministic_uuid,
    StorageError,
)
from src.lib.exceptions import CollectionNotFoundError, BatchInsertError


def test_generate_deterministic_uuid_is_stable():
    doc_id = "doc-1"
    content = "A long paragraph of text"
    value = generate_deterministic_uuid(doc_id, 0, content)
    assert value == generate_deterministic_uuid(doc_id, 0, content)


@pytest.mark.asyncio
async def test_store_to_weaviate_requires_chunks():
    with pytest.raises(StorageError, match="No chunks to store"):
        await store_to_weaviate([], "doc-1", user_id="test_user")


@pytest.mark.asyncio
async def test_store_to_weaviate_calls_status_updates():
    chunks = [{"chunk_index": 0, "content": "hello"}]
    document_id = "doc-1"

    weaviate_client = MagicMock()

    async def fake_chunks_store(chunks_arg, doc_id, client, user_id):  # noqa: ANN001
        assert doc_id == document_id
        assert chunks_arg == chunks
        assert user_id == "test_user"
        return {"stored_count": 1, "failed_count": 0, "stored_ids": ["uuid"]}

    with patch("src.lib.pipeline.store.store_chunks_to_weaviate", new=fake_chunks_store), patch(
        "src.lib.pipeline.store.update_document_metadata", new=AsyncMock()
    ) as mock_metadata, patch("src.lib.pipeline.store.update_document_status_detailed", new=AsyncMock()) as mock_status:
        stats = await store_to_weaviate(chunks, document_id, weaviate_client, user_id="test_user")

    assert stats["stored_chunks"] == 1
    mock_status.assert_awaited_once()
    mock_metadata.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_to_weaviate_marks_failed_and_raises_when_storage_fails():
    chunks = [{"chunk_index": 0, "content": "hello"}]
    document_id = "doc-1"
    weaviate_client = MagicMock()

    async def fake_chunks_store(chunks_arg, doc_id, client, user_id):  # noqa: ANN001
        raise BatchInsertError("failed", failed_objects=[{"chunk_index": 0}])

    with patch("src.lib.pipeline.store.store_chunks_to_weaviate", new=fake_chunks_store), patch(
        "src.lib.pipeline.store.update_document_metadata", new=AsyncMock()
    ) as mock_metadata, patch("src.lib.pipeline.store.update_document_status_detailed", new=AsyncMock()) as mock_status:
        with pytest.raises(StorageError):
            await store_to_weaviate(chunks, document_id, weaviate_client, user_id="test_user")

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
        with pytest.raises(CollectionNotFoundError):
            await store_chunks_to_weaviate([], "doc-1", weaviate_client, "test_user")


@pytest.mark.asyncio
async def test_store_chunks_to_weaviate_success_path():
    chunks = [
        {
            "chunk_index": 0,
            "content": "alpha",
            "metadata": {
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
    chunk_collection.batch.dynamic.return_value.__enter__.return_value = batch_ctx
    chunk_collection.query.fetch_objects.return_value = MagicMock(objects=[MagicMock(), MagicMock()])  # For verification
    batch_ctx.failed_objects = []

    event_loop = MagicMock()

    def run_executor(_, func):
        func()
        return asyncio.sleep(0)

    event_loop.run_in_executor.side_effect = run_executor

    # Mock get_user_collections to return tenant-scoped collections
    with patch("src.lib.pipeline.store.asyncio.get_event_loop", return_value=event_loop), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        result = await store_chunks_to_weaviate(chunks, "doc-1", weaviate_client, "test_user")

    assert result["stored_count"] == 2
    assert batch_ctx.add_object.call_count == 2

    # Provenance is serialized to JSON string for TEXT field storage
    provenance_properties = batch_ctx.add_object.call_args_list[0].kwargs["properties"].get("docItemProvenance")
    import json
    assert json.loads(provenance_properties) == chunks[0]["doc_items"]


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
    chunk_collection.batch.dynamic.return_value.__enter__.return_value = batch_ctx
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
        with pytest.raises(BatchInsertError):
            await store_chunks_to_weaviate(chunks, "doc-1", weaviate_client, "test_user")

    assert batch_ctx.add_object.call_count == 2


@pytest.mark.asyncio
async def test_store_chunks_to_weaviate_raises_when_batch_add_object_fails():
    chunks = [{"chunk_index": 0, "content": "alpha", "metadata": {}}]

    weaviate_client = MagicMock()
    chunk_collection = MagicMock()
    pdf_collection = MagicMock()
    batch_ctx = MagicMock()
    chunk_collection.batch.dynamic.return_value.__enter__.return_value = batch_ctx
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
        with pytest.raises(BatchInsertError):
            await store_chunks_to_weaviate(chunks, "doc-1", weaviate_client, "test_user")


@pytest.mark.asyncio
async def test_store_chunks_to_weaviate_raises_when_token_preflight_exceeds_limit(monkeypatch):
    chunks = [{"chunk_index": 0, "content": "alpha", "metadata": {}}]
    weaviate_client = MagicMock()
    chunk_collection = MagicMock()
    pdf_collection = MagicMock()
    batch_ctx = MagicMock()
    chunk_collection.batch.dynamic.return_value.__enter__.return_value = batch_ctx
    batch_ctx.failed_objects = []
    chunk_collection.query.fetch_objects.return_value = MagicMock(objects=[MagicMock()])

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
        with pytest.raises(BatchInsertError):
            await store_chunks_to_weaviate(chunks, "doc-1", weaviate_client, "test_user")

    assert batch_ctx.add_object.call_count == 0


@pytest.mark.asyncio
async def test_update_document_metadata_best_effort():
    stats = {
        "stored_chunks": 2,
        "storage_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    chunk_collection = MagicMock()
    pdf_collection = MagicMock()
    pdf_collection.data.update.side_effect = Exception("missing doc")

    @contextmanager
    def fake_session():
        yield MagicMock()

    weaviate_client = MagicMock()
    weaviate_client.session.side_effect = fake_session

    # Mock get_user_collections to return tenant-scoped collections
    with patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        await update_document_metadata("doc-1", stats, weaviate_client, "test_user")

    pdf_collection.data.update.assert_called_once()
