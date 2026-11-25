"""Unit tests for the Weaviate document helpers."""

import asyncio
import importlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_FILE_FIELDS = (
    "file_path", "file_hash", "file_size", "page_count", "pdfx_json_path", "processed_json_path",
    "hierarchy_metadata", "source_provider_source_file_id", "source_provider_converted_artifact_id",
    "source_provider_pdf_artifact_id", "source_md5", "source_converted_artifact_sha256",
    "source_file_class", "source_file_extension", "source_artifact_status", "source_import_status",
    "source_imported_at", "source_payload_path", "source_markdown_path", "source_access_scope",
    "source_access_group_ids",
)
_PAPER_PROVENANCE_FIELDS = (
    "source_provider", "source_provider_reference_id", "source_provider_reference_curie",
    "source_external_ids",
)


def _doc(*, display_mode="local_pdf", document_kind="library", file_status="ready", **values):
    """A paper record whose per-file values live on its main file."""
    file_values = {field: values.pop(field, None) for field in _FILE_FIELDS}
    file_values["display_mode"] = display_mode
    if display_mode == "text":
        file_values["page_count"] = None
    paper_values = {field: values.pop(field, None) for field in _PAPER_PROVENANCE_FIELDS}
    main_file = SimpleNamespace(id="main-file", role="main", status=file_status, **file_values)
    return SimpleNamespace(
        document_kind=document_kind, main_file=main_file, main_file_or_none=main_file, files=[main_file],
        **paper_values, **values,
    )


def _documents_module():
    """Resolve current documents module to avoid stale references across reloads."""
    return importlib.import_module("src.lib.weaviate_client.documents")


class DummyCollection:
    """Minimal stand-in for Weaviate collection methods used in tests."""

    def __init__(self, fetch_objects_result, aggregate_result):
        self.query = MagicMock()
        self.query.fetch_objects.return_value = fetch_objects_result
        self.aggregate = MagicMock()
        self.aggregate.over_all.return_value = aggregate_result


@pytest.mark.asyncio
@pytest.mark.parametrize("search", [None, "paper"])
async def test_library_excludes_benchmark_ids_in_both_page_and_count_queries(search):
    from src.models.api_schemas import DocumentFilter
    from weaviate.classes.query import Filter

    benchmark_id = UUID("00000000-0000-0000-0000-000000000123")
    collection = DummyCollection(SimpleNamespace(objects=[]), SimpleNamespace(total_count=5))
    connection = MagicMock()
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = SimpleNamespace(id=42)
    db.execute.return_value.scalars.return_value.all.return_value = []
    db.scalars.return_value = [benchmark_id]

    def database():
        yield db

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_client.documents.get_db", database), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(MagicMock(), collection)), \
         patch("src.lib.weaviate_helpers.get_tenant_name", return_value="synthetic-tenant"):
        result = await _documents_module().async_list_documents(
            "synthetic-curator", DocumentFilter(search_term=search),
            {"page": 2, "page_size": 3, "sort_by": "creationDate", "sort_order": "desc"},
        )
    exclusion = Filter.by_id().contains_none([str(benchmark_id)])
    expected = Filter.all_of([Filter.by_property("filename").like("*paper*"), exclusion]) if search else exclusion
    page = collection.query.fetch_objects.call_args.kwargs
    count = collection.aggregate.over_all.call_args.kwargs
    assert page["filters"] is count["filters"]
    if search:
        assert type(page["filters"]) is type(expected)
        assert page["filters"].filters == expected.filters
    else:
        assert page["filters"] == expected
    assert page["offset"] == 3 and page["limit"] == 3
    assert count["total_count"] is True
    assert result["total"] == 5
    query_parameters = db.scalars.call_args.args[0].compile().params.values()
    assert 42 in query_parameters and "library" in query_parameters
    assert "!=" in str(db.scalars.call_args.args[0])


@pytest.mark.asyncio
async def test_update_document_filename_updates_tenant_index():
    collection = MagicMock()
    mock_client = MagicMock()

    @contextmanager
    def fake_session():
        yield mock_client

    connection = MagicMock()
    connection.session.side_effect = fake_session
    event_loop = MagicMock()
    event_loop.run_in_executor.side_effect = lambda _, func: asyncio.sleep(0, result=func())

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch(
             "src.lib.weaviate_client.documents.get_user_collections",
             return_value=(MagicMock(), collection),
         ), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=event_loop):
        await _documents_module().update_document_filename(
            "auth-sub-1",
            "00000000-0000-0000-0000-000000000111",
            "renamed.pdf",
        )

    collection.data.update.assert_called_once_with(
        uuid="00000000-0000-0000-0000-000000000111",
        properties={"filename": "renamed.pdf"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sql_status,indexed_status,expected_status", [
    ("completed", "completed", "COMPLETED"),
    ("failed", "embedding", "FAILED"),
    ("completed", "embedding", "EMBEDDING"),
    ("processing", "completed", "PROCESSING"),
    ("completed", "pending", "COMPLETED"),
    ("pending", "failed", "FAILED"),
    (None, None, "PENDING"),
])
async def test_async_list_documents_normalises_results(sql_status, indexed_status, expected_status):
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
                    "processingStatus": indexed_status,
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

    mock_db_doc = _doc(
        id=mock_uuid,
        filename="paper.pdf",
        title=None,
        status=sql_status,
        error_message=None,
        upload_timestamp=datetime(2024, 1, 1),
        processing_started_at=None,
        processing_completed_at=None,
        file_size=2048,
        source_provider="abc_literature",
        source_provider_reference_id="101",
        source_provider_reference_curie="AGRKB:101",
        source_provider_source_file_id="source-file-1",
        source_provider_pdf_artifact_id="pdf-file-1",
        source_provider_converted_artifact_id="converted-file-1",
        source_external_ids={"pmid": "12345"},
        source_md5="abc123",
        source_file_class="converted_merged_main",
        source_file_extension="md",
        source_artifact_status="available",
        source_import_status="completed",
        source_imported_at=datetime(2024, 1, 2),
        source_access_scope="restricted",
        source_access_group_ids=["FB"],
        display_mode="text",
    )

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

        result = await _documents_module().async_list_documents("test_user_user_id", filter_model, pagination)

    assert result["documents"][0]["status"] == expected_status
    assert result["total"] == 1
    assert result["limit"] == 10
    assert result["offset"] == 0
    assert result["documents"][0]["document_id"] == str(mock_uuid)
    assert result["documents"][0]["filename"] == "paper.pdf"
    assert result["documents"][0]["user_id"] == "test_user_user_id"  # user_id is the auth_sub string, not db id
    assert result["documents"][0]["vector_count"] == 10
    assert result["documents"][0]["weaviate_tenant"] == "test_tenant"
    assert result["documents"][0]["processing_started_at"] is None
    assert result["documents"][0]["processing_completed_at"] is None
    assert result["documents"][0]["source_provenance"] == {
        "provider": "abc_literature",
        "provider_metadata": {
            "display_label": "ABC Literature",
            "short_label": "ABC",
            "identifier_help_label": (
                "PMID, PubMed ID, AGRKB, or ABC identifiers; comma or newline separated."
            ),
            "identifier_examples": [
                "PMID:23970418",
                "PubMed ID 23970418",
                "AGRKB:101000000055784",
            ],
            "reference_label_priority": [
                "external_ids.fbrf",
                "reference_curie",
                "reference_id",
                "external_ids.pmid",
                "external_ids.pmcid",
                "external_ids.doi",
                "source_md5",
            ],
        },
        "reference_id": "101",
        "reference_curie": "AGRKB:101",
        "source_file_id": "source-file-1",
        "pdf_artifact_id": "pdf-file-1",
        "converted_artifact_id": "converted-file-1",
        "external_ids": {"pmid": "12345"},
        "source_md5": "abc123",
        "file_class": "converted_merged_main",
        "file_extension": "md",
        "artifact_status": "available",
        "import_status": "completed",
        "imported_at": "2024-01-02T00:00:00",
        "access_scope": "restricted",
        "access_group_ids": ["FB"],
        "viewer_mode": "text",
    }


@patch("src.lib.weaviate_client.documents.async_list_documents", new_callable=AsyncMock)
def test_list_documents_wrapper_converts_arguments(async_mock):
    async_mock.return_value = {"documents": [], "total": 0, "pagination": {"currentPage": 1, "totalPages": 0, "totalItems": 0, "pageSize": 20}}

    result = _documents_module().list_documents(
        "test_user_user_id",
        page=2,
        page_size=15,
        embedding_status=["completed"],
        sort_by="vectorCount",
        sort_order="asc",
    )

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
        outcome = await _documents_module().update_document_status_detailed(
            "doc-1",
            "test_user_user_id",
            embedding_status="unknown",
        )
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

    mock_connection = MagicMock()
    mock_connection.session.return_value.__enter__.return_value = mock_client

    event_loop = MagicMock()
    event_loop.run_in_executor.side_effect = lambda _, func: asyncio.sleep(0, result=func())

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=mock_connection), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=event_loop):
        result = await _documents_module().update_document_status_detailed(
            "doc-1",
            "test_user_user_id",
            embedding_status="completed",
        )

    assert result["success"] is True
    assert result["updates"]["embeddingStatus"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_reports_failure", [False, True])
async def test_update_document_status_detailed_rejected_write(caller_reports_failure, monkeypatch, caplog):
    documents = _documents_module()
    connection = MagicMock()
    collection = MagicMock()
    collection.data.update.side_effect = OSError("PRIVATE_INDEX_ERROR")
    get_connection = MagicMock(return_value=connection)
    monkeypatch.setattr(documents, "get_connection", get_connection)
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", lambda *_: (None, collection))

    result = await documents.update_document_status_detailed(
        "PRIVATE_DOCUMENT_ID", "PRIVATE_USER_ID", embedding_status="failed",
        caller_reports_failure=caller_reports_failure,
    )

    get_connection.assert_called_once_with(caller_reports_failure=caller_reports_failure)
    connection.session.assert_called_once_with(caller_reports_failure=caller_reports_failure)
    assert result == {
        "success": False,
        "message": "Failed to update document status: PRIVATE_INDEX_ERROR",
        "documentId": "PRIVATE_DOCUMENT_ID",
        "error": {"code": "STATUS_UPDATE_FAILED", "details": "PRIVATE_INDEX_ERROR"},
    }
    if caller_reports_failure:
        assert caplog.record_tuples == []
    else:
        assert caplog.messages == ["Failed to update document status for PRIVATE_DOCUMENT_ID: PRIVATE_INDEX_ERROR"]


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
        result = await _documents_module().async_list_documents("missing-user", filter_obj, pagination)

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

    mock_pg_doc = _doc(
        id=owned_uuid,
        filename="owned-paper.pdf",
        title=None,
        status="pending",
        file_status="pending",  # a paper still processing: its main file is not ready yet
        error_message=None,
        upload_timestamp=None,
        processing_started_at=datetime(2026, 2, 10, 0, 1),
        processing_completed_at=datetime(2026, 2, 10, 0, 2),
        file_size=5120,
        source_provider=None,
        source_provider_reference_id=None,
        source_provider_reference_curie=None,
        source_provider_source_file_id=None,
        source_provider_converted_artifact_id=None,
        source_provider_pdf_artifact_id=None,
        source_md5=None,
        source_access_scope=None,
        
    )

    user_lookup_result = MagicMock()
    user_lookup_result.scalar_one_or_none.return_value = mock_db_user
    ownership_lookup_result = MagicMock()
    ownership_lookup_result.scalars.return_value.all.return_value = [mock_pg_doc]

    mock_db_session = MagicMock()
    mock_db_session.execute.side_effect = [user_lookup_result, ownership_lookup_result]
    mock_db_session.close = MagicMock()

    def mock_get_db():
        yield mock_db_session

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
         patch("src.lib.weaviate_helpers.get_tenant_name", return_value="tenant-owned"):
        result = await _documents_module().async_list_documents("auth-sub-1", filter_obj, pagination)

    assert result["total"] == 2
    assert result["limit"] == 5
    assert result["offset"] == 10
    assert len(result["documents"]) == 1
    owned = result["documents"][0]
    assert owned["document_id"] == str(owned_uuid)
    assert owned["user_id"] == "auth-sub-1"
    assert owned["weaviate_tenant"] == "tenant-owned"
    assert owned["status"] == "PENDING"
    assert owned["ready_file_count"] == 0
    assert owned["embedding_status"] == "pending"
    assert owned["vector_count"] is None
    assert owned["upload_timestamp"] == "2026-02-10T00:00:00"
    assert owned["processing_started_at"] == "2026-02-10T00:01:00"
    assert owned["processing_completed_at"] == "2026-02-10T00:02:00"
    assert owned["source_provenance"] is None
    assert json.loads(json.dumps(result))["documents"][0] == owned

    fetch_call = pdf_collection.query.fetch_objects.call_args.kwargs
    assert fetch_call["limit"] == 5
    assert fetch_call["offset"] == 10
    assert fetch_call["filters"] is not None

    count_call = pdf_collection.aggregate.over_all.call_args.kwargs
    assert count_call["filters"] is fetch_call["filters"]
    assert count_call["total_count"] is True


@pytest.mark.asyncio
async def test_async_list_documents_applies_date_filters():
    mock_uuid = UUID("00000000-0000-0000-0000-000000000333")
    fetch_result = SimpleNamespace(
        objects=[SimpleNamespace(uuid=mock_uuid, properties={"filename": "dated-paper.pdf"})]
    )
    pdf_collection = DummyCollection(fetch_result, SimpleNamespace(total_count=1))
    mock_client = MagicMock()

    @contextmanager
    def fake_session():
        yield mock_client

    mock_connection = MagicMock()
    mock_connection.session.side_effect = fake_session
    mock_db_user = SimpleNamespace(id=42)
    mock_pg_doc = _doc(
        id=mock_uuid,
        filename="dated-paper.pdf",
        title=None,
        status="pending",
        error_message=None,
        upload_timestamp=None,
        processing_started_at=None,
        processing_completed_at=None,
        file_size=5120,
        source_provider=None,
        source_provider_reference_id=None,
        source_provider_reference_curie=None,
        source_provider_source_file_id=None,
        source_provider_converted_artifact_id=None,
        source_provider_pdf_artifact_id=None,
        source_md5=None,
        source_access_scope=None,
        
    )
    user_lookup_result = MagicMock()
    user_lookup_result.scalar_one_or_none.return_value = mock_db_user
    ownership_lookup_result = MagicMock()
    ownership_lookup_result.scalars.return_value.all.return_value = [mock_pg_doc]
    mock_db_session = MagicMock()
    mock_db_session.execute.side_effect = [user_lookup_result, ownership_lookup_result]

    def mock_get_db():
        yield mock_db_session

    from src.models.api_schemas import DocumentFilter

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=mock_connection), \
         patch("src.lib.weaviate_client.documents.get_db", mock_get_db), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(MagicMock(), pdf_collection)), \
         patch("src.lib.weaviate_helpers.get_tenant_name", return_value="tenant-owned"):
        await _documents_module().async_list_documents(
            "auth-sub-1",
            DocumentFilter(
                date_from=datetime(2026, 3, 1, tzinfo=timezone.utc),
                date_to=datetime(2026, 3, 31, tzinfo=timezone.utc),
            ),
            {"page": 1, "page_size": 20, "sort_by": "creationDate", "sort_order": "desc"},
        )

    assert pdf_collection.query.fetch_objects.call_args.kwargs["filters"] is not None

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "user", "page", "metadata", "count", "unprovisioned"])
async def test_listing_io_and_session_lifetime_belong_to_worker(monkeypatch, failure):
    import threading
    module = _documents_module()
    loop_thread = threading.get_ident()
    events = []
    active_sessions = set()

    def record(name):
        assert threading.get_ident() != loop_thread
        events.append((name, threading.get_ident()))

    def get_db():
        index = sum(name == "sql_open" for name, _ in events)
        record("sql_open")
        active_sessions.add(index)
        db = MagicMock()

        def execute(_statement):
            record("sql_query")
            if failure == ("user" if index == 0 else "metadata"):
                raise RuntimeError("synthetic SQL failure")
            result = MagicMock()
            result.scalar_one_or_none.return_value = None if failure == "unprovisioned" else SimpleNamespace(id=42)
            result.scalars.return_value.all.return_value = []
            return result

        db.execute.side_effect = execute
        db.scalars.side_effect = lambda _: (record("benchmark_query") or [])
        try:
            yield db
        finally:
            record("sql_close")
            active_sessions.remove(index)

    collection = MagicMock()

    def remote(name, result):
        record(name)
        assert not active_sessions
        if failure == name:
            raise RuntimeError("synthetic remote failure")
        return result

    collection.query.fetch_objects.side_effect = lambda **_: remote("page", SimpleNamespace(objects=[]))
    collection.aggregate.over_all.side_effect = lambda **_: remote("count", SimpleNamespace(total_count=0))

    @contextmanager
    def session():
        record("connection_enter")
        assert not active_sessions
        try:
            yield MagicMock()
        finally:
            record("connection_exit")

    connection = MagicMock()
    connection.session.side_effect = session
    monkeypatch.setattr(module, "get_connection", lambda: (record("connection_acquire") or connection))
    monkeypatch.setattr(module, "get_user_collections", lambda *_: (record("tenant") or (collection, collection)))
    monkeypatch.setattr(module, "get_db", get_db)
    if failure in {"user", "page", "metadata", "count"}:
        with pytest.raises(RuntimeError):
            await module.async_list_documents("curator", None, {})
    else:
        assert await module.async_list_documents("curator", None, {}) == {
            "documents": [], "total": 0, "limit": 20, "offset": 0,
        }
    assert not active_sessions
    assert len({thread for _, thread in events}) == 1
    names = [name for name, _ in events]
    assert names.count("sql_open") == names.count("sql_close")
    assert names.count("connection_enter") == names.count("connection_exit")

@pytest.mark.asyncio
async def test_cancelled_listing_worker_closes_its_sql_session(monkeypatch):
    import threading
    module = _documents_module()
    loop = asyncio.get_running_loop()
    entered = loop.create_future()
    closed = loop.create_future()
    release = threading.Event()
    worker_threads = []

    def database():
        worker_threads.append(threading.get_ident())
        db = MagicMock()

        def execute(_):
            loop.call_soon_threadsafe(entered.set_result, None)
            assert release.wait(5), "test did not release SQL worker"
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        db.execute.side_effect = execute
        try:
            yield db
        finally:
            worker_threads.append(threading.get_ident())
            loop.call_soon_threadsafe(closed.set_result, None)

    monkeypatch.setattr(module, "get_connection", MagicMock())
    monkeypatch.setattr(module, "get_db", database)
    task = asyncio.create_task(module.async_list_documents("curator", None, {}))
    try:
        await asyncio.wait_for(entered, timeout=5)
        # This coroutine runs while the worker is blocked in SQL, proving scheduling.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    await asyncio.wait_for(closed, timeout=5)
    assert worker_threads[0] == worker_threads[1] != threading.get_ident()


@pytest.mark.asyncio
async def test_a_paper_without_a_main_file_is_listed_with_its_paper_provenance_only():
    from datetime import datetime

    from tests.pdf_document_test_support import new_supplements_only_paper

    paper_id = UUID("00000000-0000-0000-0000-000000000002")
    fetch_result = SimpleNamespace(
        objects=[
            SimpleNamespace(
                uuid=paper_id,
                properties={
                    "filename": "supplements-only-paper",
                    "creationDate": "2024-01-01T00:00:00",
                    "processingStatus": "completed",
                    "embeddingStatus": "completed",
                    "chunkCount": 3,
                    "vectorCount": 3,
                },
            )
        ]
    )
    mock_collection = DummyCollection(fetch_result, SimpleNamespace(total_count=1))
    mock_client = MagicMock()
    mock_client.collections.get.return_value = mock_collection

    @contextmanager
    def fake_session():
        yield mock_client

    mock_connection = MagicMock()
    mock_connection.session.side_effect = fake_session
    mock_db_user = MagicMock()
    mock_db_user.id = 123
    paper = new_supplements_only_paper(
        id=paper_id,
        user_id=123,
        filename="supplements-only-paper",
        status="completed",
        upload_timestamp=datetime(2024, 1, 1),
        document_kind="library",
        source_provider="abc_literature",
        source_provider_reference_curie="AGRKB:101",
    )
    mock_db_session = MagicMock()
    mock_db_execute_result = MagicMock()
    mock_db_execute_result.scalar_one_or_none.return_value = mock_db_user
    mock_db_execute_result.scalars.return_value.all.return_value = [paper]
    mock_db_session.execute.return_value = mock_db_execute_result

    def mock_get_db():
        yield mock_db_session

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=mock_connection), \
         patch("src.lib.weaviate_client.documents.get_db", mock_get_db), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(mock_collection, mock_collection)), \
         patch("src.lib.weaviate_helpers.get_tenant_name", return_value="test_tenant"):
        from src.models.api_schemas import DocumentFilter, PaginationParams

        result = await _documents_module().async_list_documents(
            "test_user_user_id", DocumentFilter(), PaginationParams(page=1, page_size=10).model_dump()
        )

    (document,) = result["documents"]
    assert document["filename"] == "supplements-only-paper"
    assert document["file_size_bytes"] is None
    provenance = document["source_provenance"]
    assert provenance["reference_curie"] == "AGRKB:101"
    assert "viewer_mode" not in provenance and "source_file_id" not in provenance


@pytest.mark.asyncio
async def test_get_document_waits_for_its_database_session_off_the_event_loop():
    """A slow pool (the session's first query takes a second) must not stall other work on the loop."""

    import asyncio
    import time

    from src.lib.weaviate_client import documents as documents_module

    class _SlowSession:
        def execute(self, *_args, **_kwargs):
            time.sleep(1.0)  # waiting for a pooled connection
            raise ValueError("made-up: user not found")

        def close(self):
            pass

    def slow_get_db():
        yield _SlowSession()

    gaps = []

    async def ticker(stop):
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    stop = asyncio.Event()
    with patch.object(documents_module, "get_db", slow_get_db):
        tick = asyncio.create_task(ticker(stop))
        with pytest.raises(ValueError, match="made-up"):
            await documents_module.get_document("made-up-subject", "00000000-0000-4000-8000-000000000001")
        stop.set()
        await tick
    assert gaps and max(gaps) < 0.5


@pytest.mark.asyncio
async def test_async_list_documents_reports_ready_file_count_for_single_and_multi_file_papers():
    zero_uuid = UUID("00000000-0000-0000-0000-000000000401")
    single_uuid = UUID("00000000-0000-0000-0000-000000000402")
    multi_uuid = UUID("00000000-0000-0000-0000-000000000403")
    empty_files_uuid = UUID("00000000-0000-0000-0000-000000000404")

    fetch_result = SimpleNamespace(
        objects=[
            SimpleNamespace(uuid=zero_uuid, properties={"filename": "pending.pdf", "creationDate": "2026-02-10T00:00:00"}),
            SimpleNamespace(uuid=single_uuid, properties={"filename": "single.pdf", "creationDate": "2026-02-11T00:00:00"}),
            SimpleNamespace(uuid=multi_uuid, properties={"filename": "multi.pdf", "creationDate": "2026-02-12T00:00:00"}),
            SimpleNamespace(uuid=empty_files_uuid, properties={"filename": "empty.pdf", "creationDate": "2026-02-13T00:00:00"}),
        ]
    )
    pdf_collection = DummyCollection(fetch_result, SimpleNamespace(total_count=4))
    chunk_collection = MagicMock()
    mock_client = MagicMock()

    @contextmanager
    def fake_session():
        yield mock_client

    mock_connection = MagicMock()
    mock_connection.session.side_effect = fake_session

    zero_doc = _doc(
        id=zero_uuid, filename="pending.pdf", title=None, status="pending", file_status="pending",
        error_message=None, upload_timestamp=None, processing_started_at=None, processing_completed_at=None,
        file_size=100,
    )
    single_doc = _doc(
        id=single_uuid, filename="single.pdf", title=None, status="completed", file_status="ready",
        error_message=None, upload_timestamp=None, processing_started_at=None, processing_completed_at=None,
        file_size=200,
    )
    multi_doc = _doc(
        id=multi_uuid, filename="multi.pdf", title=None, status="completed", file_status="ready",
        error_message=None, upload_timestamp=None, processing_started_at=None, processing_completed_at=None,
        file_size=300,
    )
    multi_doc.files = [
        multi_doc.main_file,
        SimpleNamespace(id="supp-ready-1", role="supplement", status="ready"),
        SimpleNamespace(id="supp-ready-2", role="supplement", status="ready"),
        SimpleNamespace(id="supp-failed", role="supplement", status="failed"),
        SimpleNamespace(id="supp-pending", role="supplement", status="pending"),
        SimpleNamespace(id="supp-removed", role="supplement", status="removed"),
    ]
    empty_files_doc = _doc(
        id=empty_files_uuid, filename="empty.pdf", title=None, status="completed", file_status="ready",
        error_message=None, upload_timestamp=None, processing_started_at=None, processing_completed_at=None,
        file_size=400,
    )
    empty_files_doc.main_file_or_none = None
    empty_files_doc.files = []

    user_lookup_result = MagicMock()
    user_lookup_result.scalar_one_or_none.return_value = SimpleNamespace(id=42)
    ownership_lookup_result = MagicMock()
    ownership_lookup_result.scalars.return_value.all.return_value = [
        zero_doc, single_doc, multi_doc, empty_files_doc,
    ]
    mock_db_session = MagicMock()
    mock_db_session.execute.side_effect = [user_lookup_result, ownership_lookup_result]
    mock_db_session.scalars.return_value = []
    mock_db_session.close = MagicMock()

    def mock_get_db():
        yield mock_db_session

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=mock_connection), \
         patch("src.lib.weaviate_client.documents.get_db", mock_get_db), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_helpers.get_tenant_name", return_value="tenant-owned"):
        result = await _documents_module().async_list_documents(
            "auth-sub-1",
            SimpleNamespace(),
            {"page": 1, "page_size": 20, "sort_by": "creationDate", "sort_order": "desc"},
        )

    counts = {row["document_id"]: row["ready_file_count"] for row in result["documents"]}
    assert counts == {
        str(zero_uuid): 0,
        str(single_uuid): 1,
        str(multi_uuid): 3,
        str(empty_files_uuid): 0,
    }
    # User lookup + one batched ownership/files fetch (never N+1 per row).
    assert mock_db_session.execute.call_count == 2
