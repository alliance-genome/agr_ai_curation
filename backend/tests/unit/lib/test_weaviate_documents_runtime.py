"""Additional runtime branch tests for Weaviate document helpers."""

import asyncio
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import MagicMock, patch

import pytest

import src.lib.weaviate_client.documents as documents


def _event_loop_with_sync_executor():
    loop = MagicMock()
    loop.run_in_executor.side_effect = lambda _executor, func: asyncio.sleep(0, result=func())
    return loop


def _connection_with_client(client):
    @contextmanager
    def _session():
        yield client

    connection = MagicMock()
    connection.session.side_effect = _session
    return connection


@pytest.mark.asyncio
async def test_update_document_status_requires_user_id():
    with pytest.raises(ValueError):
        await documents.update_document_status("doc-1", "", "processing")


@pytest.mark.asyncio
async def test_update_document_status_requires_connection(monkeypatch):
    monkeypatch.setattr(documents, "get_connection", lambda: None)
    with pytest.raises(RuntimeError):
        await documents.update_document_status("doc-1", "user-1", "processing")


@pytest.mark.asyncio
async def test_update_document_status_success():
    chunk_collection = MagicMock()
    pdf_collection = MagicMock()
    client = MagicMock()
    connection = _connection_with_client(client)

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.update_document_status("doc-1", "user-1", "processing")

    assert result["success"] is True
    pdf_collection.data.update.assert_called_once()


@pytest.mark.asyncio
async def test_update_document_status_failure_returns_error():
    chunk_collection = MagicMock()
    pdf_collection = MagicMock()
    pdf_collection.data.update.side_effect = RuntimeError("write failed")
    client = MagicMock()
    connection = _connection_with_client(client)

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.update_document_status("doc-1", "user-1", "processing")

    assert result["success"] is False
    assert "write failed" in result["message"]


@pytest.mark.asyncio
async def test_search_similar_requires_user_id():
    with pytest.raises(ValueError):
        await documents.search_similar("doc-1", "", limit=5)


@pytest.mark.asyncio
async def test_search_similar_requires_connection(monkeypatch):
    monkeypatch.setattr(documents, "get_connection", lambda: None)
    with pytest.raises(RuntimeError):
        await documents.search_similar("doc-1", "user-1", limit=5)


@pytest.mark.asyncio
async def test_search_similar_returns_empty_for_missing_vector():
    pdf_collection = MagicMock()
    pdf_collection.query.fetch_object_by_id.return_value = SimpleNamespace(vector=None)
    chunk_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.search_similar("doc-1", "user-1", limit=5)

    assert result == []


@pytest.mark.asyncio
async def test_search_similar_raises_when_source_document_missing():
    pdf_collection = MagicMock()
    pdf_collection.query.fetch_object_by_id.return_value = None
    chunk_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        with pytest.raises(ValueError):
            await documents.search_similar("doc-1", "user-1", limit=5)


@pytest.mark.asyncio
async def test_search_similar_filters_self_and_applies_limit():
    source_doc = SimpleNamespace(vector=[0.1, 0.2])
    self_obj = SimpleNamespace(
        uuid="doc-1",
        properties={"filename": "self.pdf", "fileSize": 1, "creationDate": "2026", "embeddingStatus": "done", "vectorCount": 1},
        metadata=SimpleNamespace(distance=0.01),
    )
    other_1 = SimpleNamespace(
        uuid="doc-2",
        properties={"filename": "a.pdf", "fileSize": 2, "creationDate": "2026", "embeddingStatus": "done", "vectorCount": 2},
        metadata=SimpleNamespace(distance=0.12),
    )
    other_2 = SimpleNamespace(
        uuid="doc-3",
        properties={"filename": "b.pdf", "fileSize": 3, "creationDate": "2026", "embeddingStatus": "done", "vectorCount": 3},
        metadata=SimpleNamespace(distance=0.20),
    )

    pdf_collection = MagicMock()
    pdf_collection.query.fetch_object_by_id.return_value = source_doc
    pdf_collection.query.near_vector.return_value = SimpleNamespace(objects=[self_obj, other_1, other_2])
    chunk_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.search_similar("doc-1", "user-1", limit=1)

    assert len(result) == 1
    assert result[0]["id"] == "doc-2"
    assert result[0]["_additional"]["distance"] == 0.12


@pytest.mark.asyncio
async def test_re_embed_document_success():
    chunk_collection = MagicMock()
    chunk_collection.query.fetch_objects.return_value = SimpleNamespace(objects=[1, 2, 3, 4])
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.re_embed_document("doc-1", "user-1")

    assert result["success"] is True
    assert result["total_chunks"] == 4
    pdf_collection.data.update.assert_called_once()


@pytest.mark.asyncio
async def test_re_embed_document_failure_returns_error():
    chunk_collection = MagicMock()
    chunk_collection.query.fetch_objects.side_effect = RuntimeError("query failed")
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.re_embed_document("doc-1", "user-1")

    assert result["success"] is False
    assert result["error"]["code"] == "REEMBED_FAILED"


@pytest.mark.asyncio
async def test_create_document_success():
    pdf_collection = MagicMock()
    pdf_collection.data.insert.return_value = "doc-123"
    chunk_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    metadata = SimpleNamespace(model_dump_json=lambda: '{"species":"WB"}')
    document = SimpleNamespace(
        id="doc-123",
        filename="paper.pdf",
        file_size=1024,
        creation_date=datetime(2026, 2, 1, 0, 0, 0),
        last_accessed_date=None,
        processing_status="pending",
        embedding_status="pending",
        chunk_count=0,
        vector_count=0,
        metadata=metadata,
    )

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.create_document("user-1", document)

    assert result["success"] is True
    assert result["document_id"] == "doc-123"
    pdf_collection.data.insert.assert_called_once()


@pytest.mark.asyncio
async def test_create_document_raises_on_insert_error():
    pdf_collection = MagicMock()
    pdf_collection.data.insert.side_effect = RuntimeError("insert failed")
    chunk_collection = MagicMock()
    connection = _connection_with_client(MagicMock())
    document = SimpleNamespace(
        id="doc-123",
        filename="paper.pdf",
        file_size=1024,
        creation_date=None,
        last_accessed_date=None,
        processing_status="pending",
        embedding_status="pending",
        chunk_count=0,
        vector_count=0,
        metadata={},
    )

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        with pytest.raises(RuntimeError):
            await documents.create_document("user-1", document)


@pytest.mark.asyncio
async def test_delete_document_success_with_chunk_count(monkeypatch):
    delete_result = SimpleNamespace(successful="2", matches=3)
    chunk_collection = MagicMock()
    chunk_collection.data.delete_many.return_value = delete_result
    pdf_collection = MagicMock()
    pdf_collection.data.delete_by_id.return_value = True
    connection = _connection_with_client(MagicMock())

    # PostgreSQL delete path should not fail the overall delete if unavailable.
    monkeypatch.setattr(documents, "get_db", lambda: (_ for _ in ()).throw(RuntimeError("db unavailable")))

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.delete_document("user-1", "doc-1")

    assert result["success"] is True
    assert result["chunks_deleted"] == 2
    assert result["chunks_matched"] == 3
    assert result["postgres_deleted"] is False


@pytest.mark.asyncio
async def test_delete_document_failure_returns_error_payload():
    chunk_collection = MagicMock()
    chunk_collection.data.delete_many.side_effect = RuntimeError("delete failed")
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.delete_document("user-1", "doc-1")

    assert result["success"] is False
    assert result["error"]["code"] == "DELETE_FAILED"


@pytest.mark.asyncio
async def test_get_document_handles_no_chunks_after_uuid_filter_failure():
    doc_uuid = "00000000-0000-0000-0000-000000000999"
    doc_response = SimpleNamespace(
        uuid=doc_uuid,
        properties={
            "filename": "paper.pdf",
            "chunkCount": 0,
            "vectorCount": 0,
            "metadata": {},
            "schemaVersion": "1.2.3",
        },
    )

    chunk_collection = MagicMock()
    chunk_collection.query.fetch_objects.side_effect = [
        RuntimeError("primary chunk query failed"),
        SimpleNamespace(objects=[]),
    ]
    pdf_collection = MagicMock()
    pdf_collection.query.fetch_object_by_id.return_value = doc_response
    connection = _connection_with_client(MagicMock())

    db_user = MagicMock()
    db_user.id = 101
    db_lookup = MagicMock()
    db_lookup.scalar_one_or_none.return_value = db_user
    db_session = MagicMock()
    db_session.execute.return_value = db_lookup
    db_session.close = MagicMock()

    def _mock_get_db():
        yield db_session

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_client.documents.get_db", _mock_get_db), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()), \
         patch("src.lib.weaviate_helpers.get_tenant_name", return_value="tenant-1"):
        result = await documents.get_document("user-1", doc_uuid)

    assert result["document"]["id"] == doc_uuid
    assert result["chunks"] == []
    assert result["total_chunks"] == 0
    assert result["embedding_summary"]["coverage_percentage"] is None
    assert result["schema_version"] == "1.2.3"


@pytest.mark.asyncio
async def test_get_document_parses_metadata_and_embedding_summary_fields():
    doc_uuid = "00000000-0000-0000-0000-000000000998"
    chunk_uuid_1 = "00000000-0000-0000-0000-000000000111"
    chunk_uuid_2 = "00000000-0000-0000-0000-000000000222"

    doc_response = SimpleNamespace(
        uuid=doc_uuid,
        properties={
            "filename": "paper.pdf",
            "chunkCount": 4,
            "vectorCount": 2,
            "metadata": '{"schema_version":"2.4.0","species":"WB"}',
            "schemaVersion": "1.0.0",
        },
    )
    chunks_response = SimpleNamespace(
        objects=[
            SimpleNamespace(
                uuid=chunk_uuid_1,
                properties={
                    "documentId": UUID(doc_uuid),
                    "chunkIndex": 0,
                    "content": "intro",
                    "elementType": "paragraph",
                    "pageNumber": 1,
                    "sectionTitle": "Intro",
                    "metadata": '{"section":"intro"}',
                    "embeddingModel": "text-embedding-3-large",
                    "embeddingTimestamp": "2026-02-01T00:00:00Z",
                },
            ),
            SimpleNamespace(
                uuid=chunk_uuid_2,
                properties={
                    "documentId": doc_uuid,
                    "chunkIndex": 1,
                    "content": "methods",
                    "elementType": "paragraph",
                    "pageNumber": 2,
                    "sectionTitle": "Methods",
                    "metadata": "not-json",
                    "embeddingModel": "text-embedding-3-small",
                    "embeddingTimestamp": "bad-timestamp",
                },
            ),
        ]
    )

    chunk_collection = MagicMock()
    chunk_collection.query.fetch_objects.return_value = chunks_response
    pdf_collection = MagicMock()
    pdf_collection.query.fetch_object_by_id.return_value = doc_response
    connection = _connection_with_client(MagicMock())

    db_user = MagicMock()
    db_user.id = 202
    db_lookup = MagicMock()
    db_lookup.scalar_one_or_none.return_value = db_user
    db_session = MagicMock()
    db_session.execute.return_value = db_lookup
    db_session.close = MagicMock()

    def _mock_get_db():
        yield db_session

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_client.documents.get_db", _mock_get_db), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()), \
         patch("src.lib.weaviate_helpers.get_tenant_name", return_value="tenant-2"):
        result = await documents.get_document("user-2", doc_uuid)

    assert result["document"]["metadata"]["schema_version"] == "2.4.0"
    assert result["chunks"][0]["document_id"] == doc_uuid
    assert result["chunks"][0]["metadata"] == {"section": "intro"}
    assert result["chunks"][1]["metadata"] is None
    assert result["embedding_summary"]["primary_model"] == "text-embedding-3-large"
    assert result["embedding_summary"]["last_embedded_at"] == "2026-02-01T00:00:00+00:00"
    assert result["embedding_summary"]["coverage_percentage"] == 50.0
    assert result["schema_version"] == "2.4.0"
