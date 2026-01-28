"""Unit tests for the Weaviate document helpers."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.lib.weaviate_client.documents import list_documents, async_list_documents, update_document_status_detailed


class DummyCollection:
    """Minimal stand-in for Weaviate collection methods used in tests."""

    def __init__(self, fetch_objects_result, aggregate_result):
        self.query = MagicMock()
        self.query.fetch_objects.return_value = fetch_objects_result
        self.aggregate = MagicMock()
        self.aggregate.over_all.return_value = aggregate_result


@pytest.mark.asyncio
async def test_async_list_documents_normalises_results():
    mock_uuid = UUID("00000000-0000-0000-0000-000000000001")
    fetch_result = SimpleNamespace(
        objects=[
            SimpleNamespace(
                uuid=mock_uuid,
                properties={
                    "filename": "paper.pdf",
                    "fileSize": 2048,
                    "creationDate": "2024-01-01T00:00:00",
                    "lastAccessedDate": "2024-01-02T00:00:00",
                    "processingStatus": "completed",
                    "embeddingStatus": "processing",
                    "chunkCount": 10,
                    "vectorCount": 10,
                    "metadata": '{"section":"methods"}'
                }
            )
        ]
    )
    aggregate_result = SimpleNamespace(total_count=1)

    mock_collection = DummyCollection(fetch_result, aggregate_result)
    mock_client = MagicMock()
    mock_client.collections.get.return_value = mock_collection

    @contextmanager
    def fake_session():
        yield mock_client

    mock_connection = MagicMock()
    mock_connection.session.side_effect = fake_session

    # Mock PostgreSQL database access (T030 requirement)
    from datetime import datetime
    mock_db_user = MagicMock()
    mock_db_user.user_id = 123
    mock_db_user.user_id = "test_user_user_id"

    mock_db_doc = MagicMock()
    mock_db_doc.id = mock_uuid
    mock_db_doc.upload_timestamp = datetime(2024, 1, 1)
    mock_db_doc.file_size = 2048

    mock_db_session = MagicMock()
    mock_db_execute_result = MagicMock()
    mock_db_execute_result.scalar_one_or_none.return_value = mock_db_user
    mock_db_execute_result.scalars.return_value.all.return_value = [mock_db_doc]
    mock_db_session.execute.return_value = mock_db_execute_result
    mock_db_session.close = MagicMock()

    def mock_get_db():
        yield mock_db_session

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=mock_connection), \
         patch("src.lib.weaviate_client.documents.get_db", mock_get_db), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(mock_collection, mock_collection)), \
         patch("src.lib.weaviate_helpers.get_tenant_name", return_value="test_tenant"):
        from src.models.api_schemas import DocumentFilter, PaginationParams

        filter_model = DocumentFilter()
        pagination = PaginationParams(page=1, page_size=10).model_dump()

        result = await async_list_documents("test_user_user_id", filter_model, pagination)

    assert result["total"] == 1
    assert result["limit"] == 10
    assert result["offset"] == 0
    assert result["documents"][0]["document_id"] == str(mock_uuid)
    assert result["documents"][0]["user_id"] == 123
    assert result["documents"][0]["weaviate_tenant"] == "test_tenant"


@patch("src.lib.weaviate_client.documents.async_list_documents", new_callable=AsyncMock)
def test_list_documents_wrapper_converts_arguments(async_mock):
    async_mock.return_value = {"documents": [], "total": 0, "pagination": {"currentPage": 1, "totalPages": 0, "totalItems": 0, "pageSize": 20}}

    result = list_documents("test_user_user_id", page=2, page_size=15, embedding_status=["completed"], sort_by="vectorCount", sort_order="asc")

    assert result["documents"] == []
    async_mock.assert_awaited_once()

    user_id_arg, filter_arg, pagination_arg = async_mock.await_args.args
    assert user_id_arg == "test_user_user_id"
    assert filter_arg.embedding_status[0].value == "completed"
    assert pagination_arg["page"] == 2
    assert pagination_arg["sort_by"] == "vectorCount"
    assert pagination_arg["sort_order"] == "asc"


@pytest.mark.asyncio
async def test_update_document_status_detailed_invalid_status():
    with patch("src.lib.weaviate_client.documents.get_connection", return_value=MagicMock()):
        outcome = await update_document_status_detailed("doc-1", "test_user_user_id", embedding_status="unknown")
    assert outcome["success"] is False
    assert outcome["error"]["code"] == "INVALID_STATUS"


@pytest.mark.asyncio
async def test_update_document_status_detailed_success():
    mock_collection = MagicMock()

    def fake_update(uuid, properties):  # noqa: ANN001
        assert uuid == "doc-1"
        assert properties == {"embeddingStatus": "completed"}

    mock_collection.data.update.side_effect = fake_update

    mock_client = MagicMock()
    # Mock the tenant-scoped collection pattern
    mock_base_collection = MagicMock()
    mock_base_collection.with_tenant.return_value = mock_collection
    mock_client.collections.get.return_value = mock_base_collection

    @contextmanager
    def fake_session():
        yield mock_client

    mock_connection = MagicMock()
    mock_connection.session.side_effect = fake_session

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=mock_connection):
        result = await update_document_status_detailed("doc-1", "test_user_user_id", embedding_status="completed")

    assert result["success"] is True
    assert result["updates"]["embeddingStatus"] == "completed"
