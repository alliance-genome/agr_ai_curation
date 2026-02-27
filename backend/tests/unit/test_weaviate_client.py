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
    mock_db_user.id = 123  # User's DB id
    mock_db_user.user_id = "test_user_user_id"  # User's string identifier

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

    event_loop = MagicMock()
    event_loop.run_in_executor.side_effect = lambda _, func: asyncio.sleep(0, result=func())

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=mock_connection), \
         patch("src.lib.weaviate_client.documents.get_db", mock_get_db), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(mock_collection, mock_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=event_loop), \
         patch("src.lib.weaviate_helpers.get_tenant_name", return_value="test_tenant"):
        from src.models.api_schemas import DocumentFilter, PaginationParams

        filter_model = DocumentFilter()
        pagination = PaginationParams(page=1, page_size=10).model_dump()

        result = await async_list_documents("test_user_user_id", filter_model, pagination)

    assert result["total"] == 1
    assert result["limit"] == 10
    assert result["offset"] == 0
    assert result["documents"][0]["document_id"] == str(mock_uuid)
    assert result["documents"][0]["user_id"] == "test_user_user_id"  # user_id is the auth_sub string, not db id
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

    event_loop = MagicMock()
    event_loop.run_in_executor.side_effect = lambda _, func: asyncio.sleep(0, result=func())

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=mock_connection), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=event_loop):
        result = await update_document_status_detailed("doc-1", "test_user_user_id", embedding_status="completed")

    assert result["success"] is True
    assert result["updates"]["embeddingStatus"] == "completed"


@pytest.mark.asyncio
async def test_async_list_documents_returns_empty_for_unprovisioned_user():
    mock_connection = MagicMock()

    mock_db_session = MagicMock()
    user_lookup_result = MagicMock()
    user_lookup_result.scalar_one_or_none.return_value = None
    mock_db_session.execute.return_value = user_lookup_result
    mock_db_session.close = MagicMock()

    def mock_get_db():
        yield mock_db_session

    filter_obj = SimpleNamespace(
        search_term=None,
        embedding_status=None,
        min_vector_count=None,
        max_vector_count=None,
    )
    pagination = {"page": 3, "page_size": 7}

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=mock_connection), \
         patch("src.lib.weaviate_client.documents.get_db", mock_get_db):
        result = await async_list_documents("missing-user", filter_obj, pagination)

    assert result == {"documents": [], "total": 0, "limit": 7, "offset": 14}
    mock_connection.session.assert_not_called()


@pytest.mark.asyncio
async def test_async_list_documents_filters_to_owned_docs_and_applies_defaults():
    owned_uuid = UUID("00000000-0000-0000-0000-000000000111")
    other_uuid = UUID("00000000-0000-0000-0000-000000000222")
    fetch_result = SimpleNamespace(
        objects=[
            SimpleNamespace(
                uuid=owned_uuid,
                properties={
                    "filename": "owned-paper.pdf",
                    "creationDate": "2026-02-10T00:00:00",
                    "chunkCount": 4,
                },
            ),
            SimpleNamespace(
                uuid=other_uuid,
                properties={
                    "filename": "other-paper.pdf",
                    "processingStatus": "completed",
                    "embeddingStatus": "completed",
                    "creationDate": "2026-02-11T00:00:00",
                    "chunkCount": 9,
                },
            ),
        ]
    )
    aggregate_result = SimpleNamespace(total_count=2)
    pdf_collection = DummyCollection(fetch_result, aggregate_result)
    chunk_collection = MagicMock()
    mock_client = MagicMock()

    @contextmanager
    def fake_session():
        yield mock_client

    mock_connection = MagicMock()
    mock_connection.session.side_effect = fake_session

    mock_db_user = MagicMock()
    mock_db_user.id = 42

    mock_pg_doc = MagicMock()
    mock_pg_doc.id = owned_uuid
    mock_pg_doc.upload_timestamp = None
    mock_pg_doc.file_size = 5120

    user_lookup_result = MagicMock()
    user_lookup_result.scalar_one_or_none.return_value = mock_db_user
    ownership_lookup_result = MagicMock()
    ownership_lookup_result.scalars.return_value.all.return_value = [mock_pg_doc]

    mock_db_session = MagicMock()
    mock_db_session.execute.side_effect = [user_lookup_result, ownership_lookup_result]
    mock_db_session.close = MagicMock()

    def mock_get_db():
        yield mock_db_session

    event_loop = MagicMock()
    event_loop.run_in_executor.side_effect = lambda _, func: asyncio.sleep(0, result=func())

    filter_obj = SimpleNamespace(
        search_term="paper",
        embedding_status=["pending", "completed"],
        min_vector_count=1,
        max_vector_count=10,
    )
    pagination = {"page": 3, "page_size": 5, "sort_by": "creationDate", "sort_order": "desc"}

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=mock_connection), \
         patch("src.lib.weaviate_client.documents.get_db", mock_get_db), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=event_loop), \
         patch("src.lib.weaviate_helpers.get_tenant_name", return_value="tenant-owned"):
        result = await async_list_documents("auth-sub-1", filter_obj, pagination)

    assert result["total"] == 2
    assert result["limit"] == 5
    assert result["offset"] == 10
    assert len(result["documents"]) == 1
    owned = result["documents"][0]
    assert owned["document_id"] == str(owned_uuid)
    assert owned["user_id"] == "auth-sub-1"
    assert owned["weaviate_tenant"] == "tenant-owned"
    assert owned["status"] == "PENDING"
    assert owned["embedding_status"] == "pending"
    assert owned["upload_timestamp"] == "2026-02-10T00:00:00"

    fetch_call = pdf_collection.query.fetch_objects.call_args.kwargs
    assert fetch_call["limit"] == 5
    assert fetch_call["offset"] == 10
    assert fetch_call["filters"] is not None

    count_call = pdf_collection.aggregate.over_all.call_args.kwargs
    assert count_call["filters"] is fetch_call["filters"]
    assert count_call["total_count"] is True
