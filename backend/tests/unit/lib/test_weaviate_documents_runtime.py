"""Additional runtime branch tests for Weaviate document helpers."""

import asyncio
import importlib
import json
import threading
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import MagicMock, patch

import pytest


class _DocumentsModuleProxy:
    """Resolve documents module lazily to avoid stale imports in full-suite runs."""

    @staticmethod
    def _module():
        return importlib.import_module("src.lib.weaviate_client.documents")

    def __getattr__(self, name):
        return getattr(self._module(), name)

    def __setattr__(self, name, value):
        setattr(self._module(), name, value)


documents = _DocumentsModuleProxy()


@pytest.mark.asyncio
async def test_detailed_library_read_rejects_benchmark_before_vector_access(monkeypatch):
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = SimpleNamespace(id=42)
    session.scalar.return_value = "benchmark_frozen"

    def database():
        yield session

    connection = MagicMock()
    monkeypatch.setattr(documents._module(), "get_db", database)
    monkeypatch.setattr(documents._module(), "get_connection", connection)
    with pytest.raises(ValueError, match="curator library"):
        await documents.get_document("synthetic-curator", "00000000-0000-0000-0000-000000000123")
    connection.assert_not_called()
    params = session.scalar.call_args.args[0].compile().params.values()
    assert 42 in params


@pytest.mark.parametrize(
    ("sql_status", "weaviate_status", "expected"),
    [
        ("failed", "pending", "failed"),
        ("failed", "processing", "failed"),
        ("completed", "processing", "processing"),
        ("processing", "completed", "processing"),
        ("processing", "pending", "processing"),
        ("pending", "processing", "processing"),
    ],
)
def test_effective_list_processing_status_prefers_terminal_state(
    sql_status,
    weaviate_status,
    expected,
):
    assert documents._effective_list_processing_status(sql_status, weaviate_status) == expected


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
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())
    # The paper's readable pieces: its ready files' ranges (a leftover range is not counted).
    readable = SimpleNamespace(files=(SimpleNamespace(chunk_count=3), SimpleNamespace(chunk_count=1)))

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.document_files.readable.resolve_readable_files", return_value=readable) as resolve, \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.re_embed_document("doc-1", "user-1")

    assert result["success"] is True
    assert result["total_chunks"] == 4
    resolve.assert_called_once_with("doc-1", "user-1")
    chunk_collection.query.fetch_objects.assert_not_called()
    pdf_collection.data.update.assert_called_once()


@pytest.mark.asyncio
async def test_re_embed_document_failure_returns_error():
    chunk_collection = MagicMock()
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.document_files.readable.resolve_readable_files", side_effect=RuntimeError("query failed")), \
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
    properties = pdf_collection.data.insert.call_args.kwargs["properties"]
    assert properties["metadata"] == '{"species": "WB"}'


@pytest.mark.asyncio
async def test_create_document_includes_source_provenance_metadata():
    pdf_collection = MagicMock()
    pdf_collection.data.insert.return_value = "doc-123"
    chunk_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    document = SimpleNamespace(
        id="doc-123",
        filename="paper.md",
        file_size=1024,
        creation_date=datetime(2026, 2, 1, 0, 0, 0),
        last_accessed_date=None,
        processing_status="pending",
        embedding_status="pending",
        chunk_count=0,
        vector_count=0,
        metadata={"document_type": "provider_text"},
        source_provenance={
            "provider": "abc_literature",
            "reference_curie": "AGRKB:101",
            "converted_artifact_id": "converted-file-1",
            "access_scope": "restricted",
            "access_group_ids": ["FB", {"secret": "drop"}],
            "source_payload_path": "/internal/raw.json",
            "curator_token": "secret-token",
            "full_markdown": "# Full content",
        },
    )

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.create_document("user-1", document)

    assert result["success"] is True
    properties = pdf_collection.data.insert.call_args.kwargs["properties"]
    metadata = json.loads(properties["metadata"])
    assert metadata["document_type"] == "provider_text"
    assert metadata["source_provenance"] == {
        "provider": "abc_literature",
        "reference_curie": "AGRKB:101",
        "converted_artifact_id": "converted-file-1",
        "access_scope": "restricted",
        "access_group_ids": ["FB"],
    }
    assert "curator_token" not in metadata["source_provenance"]
    assert "source_payload_path" not in metadata["source_provenance"]
    assert "full_markdown" not in metadata["source_provenance"]


@pytest.mark.asyncio
async def test_create_document_strips_raw_metadata_source_provenance():
    pdf_collection = MagicMock()
    pdf_collection.data.insert.return_value = "doc-123"
    chunk_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

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
        metadata={
            "document_type": "general",
            "source_provenance": {
                "provider": "abc_literature",
                "client_secret": "secret-client",
                "full_markdown": "# Full content",
            },
        },
        source_provenance=None,
    )

    with patch("src.lib.weaviate_client.documents.get_connection", return_value=connection), \
         patch("src.lib.weaviate_client.documents.get_user_collections", return_value=(chunk_collection, pdf_collection)), \
         patch("src.lib.weaviate_client.documents.asyncio.get_event_loop", return_value=_event_loop_with_sync_executor()):
        result = await documents.create_document("user-1", document)

    assert result["success"] is True
    properties = pdf_collection.data.insert.call_args.kwargs["properties"]
    metadata = json.loads(properties["metadata"])
    assert metadata == {"document_type": "general"}


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
@pytest.mark.parametrize(
    ("successful", "failed", "reporter_raises"),
    [
        pytest.param(2, 1, False, id="partial-wipe"),
        pytest.param(2, 0, False, id="unaccounted-match"),
        pytest.param(3, 1, False, id="reported-failure-with-equal-counts"),
        pytest.param(2, 1, True, id="reporter-failure"),
    ],
)
async def test_delete_document_preserves_owners_until_chunk_wipe_completes(
    monkeypatch, successful, failed, reporter_raises,
):
    user_id = "fake-sensitive-curator"
    document_id = "dddddddd-0000-0000-0000-000000000123"
    private_detail = "fake-sensitive-provider-detail"
    chunk_collection = MagicMock()
    chunk_collection.data.delete_many.side_effect = [
        SimpleNamespace(successful=successful, matches=3, failed=failed, objects=[private_detail]),
        SimpleNamespace(successful=1, matches=1, failed=0),
    ]
    pdf_collection = MagicMock()
    pdf_collection.data.delete_by_id.return_value = True
    client = MagicMock()
    monkeypatch.setattr(documents, "get_connection", lambda: _connection_with_client(client))
    collections = MagicMock(return_value=(chunk_collection, pdf_collection))
    monkeypatch.setattr(documents, "get_user_collections", collections)
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = SimpleNamespace(id=42)
    paper = SimpleNamespace(id=UUID(document_id), user_id=42)
    db.get.return_value = paper
    get_db = MagicMock(side_effect=lambda: iter([db]))
    monkeypatch.setattr(documents, "get_db", get_db)
    cleanup = MagicMock()
    monkeypatch.setattr(documents, "cleanup_document_curation_dependencies", cleanup)
    report = MagicMock(side_effect=RuntimeError(private_detail) if reporter_raises else None)
    monkeypatch.setattr(documents.runtime, "report_runtime_exception", report)

    result = await documents.delete_document(user_id, document_id)
    counts = {"chunks_deleted": successful, "chunks_matched": 3, "chunks_failed": failed}
    assert result == {
        "success": False,
        "message": "Document deletion reported as unsuccessful",
        "documentId": document_id,
        **counts,
        "postgres_deleted": False,
    }
    collections.assert_called_once_with(client, user_id)
    pdf_collection.data.delete_by_id.assert_not_called()
    get_db.assert_not_called()
    report.assert_called_once()
    (error,) = report.call_args.args
    assert str(error) == "Paper chunk deletion was incomplete"
    assert error.__cause__ is None and error.__context__ is None
    assert report.call_args.kwargs == {
        "component": "weaviate_client",
        "operation": "paper_delete_chunk_wipe_incomplete",
        "context": counts,
        "include_request": False,
    }
    for sensitive in (user_id, document_id, private_detail):
        assert sensitive not in repr(report.call_args)

    retry = await documents.delete_document(user_id, document_id)
    assert retry == {
        "success": True,
        "message": "Document and chunks deleted successfully",
        "documentId": document_id,
        "chunks_deleted": 1,
        "chunks_matched": 1,
        "postgres_deleted": True,
    }
    pdf_collection.data.delete_by_id.assert_called_once_with(document_id)
    cleanup.assert_called_once_with(db, paper.id)
    db.delete.assert_called_once_with(paper)
    db.commit.assert_called_once_with()
    report.assert_called_once()


@pytest.mark.asyncio
async def test_delete_document_without_chunks_keeps_success_when_sql_unavailable(monkeypatch):
    chunks, papers = MagicMock(), MagicMock()
    chunks.data.delete_many.return_value = SimpleNamespace(successful=0, matches=0, failed=0)
    papers.data.delete_by_id.return_value = True
    monkeypatch.setattr(documents, "get_connection", lambda: _connection_with_client(MagicMock()))
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))
    monkeypatch.setattr(documents, "get_db", MagicMock(side_effect=RuntimeError("db unavailable")))

    result = await documents.delete_document("user-1", "doc-1")

    assert result["success"] is True
    assert result["chunks_deleted"] == result["chunks_matched"] == 0
    assert result["postgres_deleted"] is False
    papers.data.delete_by_id.assert_called_once_with("doc-1")


@pytest.mark.asyncio
async def test_delete_document_cleans_sql_when_index_object_is_already_absent(monkeypatch):
    chunks, papers = MagicMock(), MagicMock()
    chunks.data.delete_many.return_value = SimpleNamespace(successful=0, matches=0, failed=0)
    papers.data.delete_by_id.return_value = False
    monkeypatch.setattr(documents, "get_connection", lambda: _connection_with_client(MagicMock()))
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))
    delete_sql_row = MagicMock(return_value=True)
    monkeypatch.setattr(documents, "_delete_sql_row", delete_sql_row)

    result = await documents.delete_document("user-1", "doc-1")

    assert result["success"] is False
    assert result["postgres_deleted"] is True
    delete_sql_row.assert_called_once_with("user-1", "doc-1")


@pytest.mark.asyncio
async def test_delete_paper_chunks_wipes_tenant_chunks_without_deleting_parent(monkeypatch):
    chunks, papers = MagicMock(), MagicMock()
    chunks.data.delete_many.return_value = SimpleNamespace(successful=2, matches=2, failed=0)
    monkeypatch.setattr(documents, "get_connection", lambda: _connection_with_client(MagicMock()))
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))

    result = await documents.delete_paper_chunks("user-1", "doc-1")

    assert result == {
        "success": True,
        "message": "Document chunks deleted successfully",
        "documentId": "doc-1",
        "chunks_deleted": 2,
        "chunks_matched": 2,
    }
    papers.data.delete_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_delete_paper_chunks_reports_incomplete_wipe(monkeypatch):
    chunks, papers = MagicMock(), MagicMock()
    chunks.data.delete_many.return_value = SimpleNamespace(successful=1, matches=2, failed=1)
    monkeypatch.setattr(documents, "get_connection", lambda: _connection_with_client(MagicMock()))
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))
    report = MagicMock()
    monkeypatch.setattr(documents.runtime, "report_runtime_exception", report)

    result = await documents.delete_paper_chunks("user-1", "doc-1")

    assert result["success"] is False
    assert result["chunks_failed"] == 1
    papers.data.delete_by_id.assert_not_called()
    report.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deleted", "allow_missing", "expected_success"),
    [
        (True, False, True),
        (False, False, False),
        (False, True, True),
    ],
)
async def test_delete_paper_object_respects_allow_missing(
    monkeypatch, deleted, allow_missing, expected_success,
):
    chunks, papers = MagicMock(), MagicMock()
    papers.data.delete_by_id.return_value = deleted
    monkeypatch.setattr(documents, "get_connection", lambda: _connection_with_client(MagicMock()))
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))

    result = await documents.delete_paper_object("user-1", "doc-1", allow_missing=allow_missing)

    assert result["success"] is expected_success
    assert result["deleted"] is deleted
    chunks.data.delete_many.assert_not_called()
    papers.data.delete_by_id.assert_called_once_with("doc-1")


@pytest.mark.asyncio
async def test_delete_paper_object_returns_error_on_exception(monkeypatch):
    chunks, papers = MagicMock(), MagicMock()
    papers.data.delete_by_id.side_effect = RuntimeError("weaviate unavailable")
    monkeypatch.setattr(documents, "get_connection", lambda: _connection_with_client(MagicMock()))
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))

    result = await documents.delete_paper_object("user-1", "doc-1", allow_missing=True)

    assert result["success"] is False
    assert result["deleted"] is False
    assert result["error"]["code"] == "DELETE_FAILED"


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
async def test_delete_document_cancel_during_chunk_wipe_drains_index_and_sql(monkeypatch):
    wipe_entered = threading.Event()
    resume_wipe = threading.Event()
    sql_entered = threading.Event()
    resume_sql = threading.Event()
    events: list[str] = []
    session_open = False

    @contextmanager
    def _tracked_session():
        nonlocal session_open
        session_open = True
        try:
            yield MagicMock()
        finally:
            session_open = False
            events.append("session_closed")

    connection = MagicMock()
    connection.session.side_effect = _tracked_session

    def _delete_many(**_kwargs):
        wipe_entered.set()
        assert resume_wipe.wait(timeout=5)
        assert session_open is True
        events.append("chunks")
        return SimpleNamespace(successful=2, matches=2, failed=0)

    def _delete_by_id(_document_id):
        assert session_open is True
        events.append("parent")
        return True

    def _delete_sql(_user_id, _document_id):
        sql_entered.set()
        assert resume_sql.wait(timeout=5)
        events.append("sql")
        return True

    chunks, papers = MagicMock(), MagicMock()
    chunks.data.delete_many.side_effect = _delete_many
    papers.data.delete_by_id.side_effect = _delete_by_id
    monkeypatch.setattr(documents, "get_connection", lambda: connection)
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))
    monkeypatch.setattr(documents, "_delete_sql_row", _delete_sql)

    task = asyncio.create_task(documents.delete_document("user-1", "doc-1"))
    try:
        assert await asyncio.to_thread(wipe_entered.wait, 5)
        task.cancel("first cancel")
        await asyncio.sleep(0.02)
        task.cancel("second cancel")
        await asyncio.sleep(0.02)
        assert not task.done()
        assert session_open is True

        resume_wipe.set()
        assert await asyncio.to_thread(sql_entered.wait, 5)
        task.cancel("third cancel during sql")
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        resume_wipe.set()
        resume_sql.set()

    with pytest.raises(asyncio.CancelledError, match="first cancel"):
        await asyncio.wait_for(task, timeout=5)

    assert events == ["chunks", "parent", "session_closed", "sql"]


@pytest.mark.asyncio
async def test_delete_document_cancel_during_incomplete_chunk_wipe_retains_owners(monkeypatch):
    wipe_entered = threading.Event()
    resume_wipe = threading.Event()

    def _delete_many(**_kwargs):
        wipe_entered.set()
        assert resume_wipe.wait(timeout=5)
        return SimpleNamespace(successful=1, matches=2, failed=1)

    chunks, papers = MagicMock(), MagicMock()
    chunks.data.delete_many.side_effect = _delete_many
    monkeypatch.setattr(documents, "get_connection", lambda: _connection_with_client(MagicMock()))
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))
    delete_sql_row = MagicMock()
    monkeypatch.setattr(documents, "_delete_sql_row", delete_sql_row)
    report = MagicMock()
    monkeypatch.setattr(documents.runtime, "report_runtime_exception", report)

    task = asyncio.create_task(documents.delete_document("user-1", "doc-1"))
    try:
        assert await asyncio.to_thread(wipe_entered.wait, 5)
        task.cancel("cancel during incomplete wipe")
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        resume_wipe.set()

    with pytest.raises(asyncio.CancelledError, match="cancel during incomplete wipe"):
        await asyncio.wait_for(task, timeout=5)

    report.assert_called_once()
    papers.data.delete_by_id.assert_not_called()
    delete_sql_row.assert_not_called()


@pytest.mark.asyncio
async def test_delete_paper_chunks_cancel_drains_worker_before_closing_session(monkeypatch):
    wipe_entered = threading.Event()
    resume_wipe = threading.Event()
    session_open = False
    wipe_finished_while_open = False

    @contextmanager
    def _tracked_session():
        nonlocal session_open
        session_open = True
        try:
            yield MagicMock()
        finally:
            session_open = False

    connection = MagicMock()
    connection.session.side_effect = _tracked_session

    def _delete_many(**_kwargs):
        nonlocal wipe_finished_while_open
        wipe_entered.set()
        assert resume_wipe.wait(timeout=5)
        wipe_finished_while_open = session_open
        return SimpleNamespace(successful=2, matches=2, failed=0)

    chunks, papers = MagicMock(), MagicMock()
    chunks.data.delete_many.side_effect = _delete_many
    monkeypatch.setattr(documents, "get_connection", lambda: connection)
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))

    task = asyncio.create_task(documents.delete_paper_chunks("user-1", "doc-1"))
    try:
        assert await asyncio.to_thread(wipe_entered.wait, 5)
        task.cancel("stop wipe")
        await asyncio.sleep(0.02)
        task.cancel("stop wipe again")
        await asyncio.sleep(0.02)
        assert not task.done()
        assert session_open is True
    finally:
        resume_wipe.set()

    with pytest.raises(asyncio.CancelledError, match="stop wipe"):
        await asyncio.wait_for(task, timeout=5)

    assert wipe_finished_while_open is True
    assert session_open is False
    papers.data.delete_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_delete_paper_search_entries_cancel_during_chunk_wipe_deletes_parent(monkeypatch):
    wipe_entered = threading.Event()
    resume_wipe = threading.Event()
    session_open = False
    events: list[str] = []

    @contextmanager
    def _tracked_session():
        nonlocal session_open
        session_open = True
        try:
            yield MagicMock()
        finally:
            session_open = False
            events.append("session_closed")

    connection = MagicMock()
    connection.session.side_effect = _tracked_session

    def _delete_many(**_kwargs):
        wipe_entered.set()
        assert resume_wipe.wait(timeout=5)
        assert session_open is True
        events.append("chunks")
        return SimpleNamespace(successful=2, matches=2, failed=0)

    def _delete_by_id(_document_id):
        assert session_open is True
        events.append("parent")
        return True

    chunks, papers = MagicMock(), MagicMock()
    chunks.data.delete_many.side_effect = _delete_many
    papers.data.delete_by_id.side_effect = _delete_by_id
    monkeypatch.setattr(documents, "get_connection", lambda: connection)
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))

    task = asyncio.create_task(documents.delete_paper_search_entries("user-1", "doc-1"))
    try:
        assert await asyncio.to_thread(wipe_entered.wait, 5)
        task.cancel("stop search delete")
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        resume_wipe.set()

    with pytest.raises(asyncio.CancelledError, match="stop search delete"):
        await asyncio.wait_for(task, timeout=5)

    assert events == ["chunks", "parent", "session_closed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("helper_name", ["delete_document", "delete_paper_chunks", "delete_paper_search_entries"])
async def test_delete_helpers_cancelled_before_submission_do_not_start_destructive_work(
    monkeypatch, helper_name,
):
    chunks, papers = MagicMock(), MagicMock()
    monkeypatch.setattr(documents, "get_connection", lambda: _connection_with_client(MagicMock()))
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))
    delete_sql_row = MagicMock()
    monkeypatch.setattr(documents, "_delete_sql_row", delete_sql_row)

    helper = getattr(documents, helper_name)
    task = asyncio.create_task(helper("user-1", "doc-1"))
    task.cancel("cancelled before submission")
    with pytest.raises(asyncio.CancelledError, match="cancelled before submission"):
        await task

    chunks.data.delete_many.assert_not_called()
    papers.data.delete_by_id.assert_not_called()
    delete_sql_row.assert_not_called()


@pytest.mark.asyncio
async def test_drain_shielded_preserves_cancellation_when_on_cancelled_error_fails():
    from src.lib.document_cleanup import drain_shielded

    entered = asyncio.Event()
    resume = asyncio.Event()
    callback_called = False

    async def _failing_cleanup():
        entered.set()
        await resume.wait()
        raise RuntimeError("cleanup boom")

    def _failing_observer(_exc: Exception) -> None:
        nonlocal callback_called
        callback_called = True
        raise RuntimeError("observer boom")

    task = asyncio.create_task(
        drain_shielded(_failing_cleanup(), on_cancelled_error=_failing_observer)
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel("original cancel")
    await asyncio.sleep(0)
    assert not task.done()

    resume.set()
    with pytest.raises(asyncio.CancelledError, match="original cancel"):
        await asyncio.wait_for(task, timeout=2)

    assert callback_called is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "helper_name",
    ["delete_paper_chunks", "delete_paper_object", "delete_paper_search_entries", "delete_document"],
)
async def test_delete_helpers_cancel_before_submission_when_caller_already_cancelling(
    monkeypatch, helper_name,
):
    chunks, papers = MagicMock(), MagicMock()
    connection = _connection_with_client(MagicMock())
    get_conn = MagicMock(return_value=connection)
    monkeypatch.setattr(documents, "get_connection", get_conn)
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))
    delete_sql_row = MagicMock()
    monkeypatch.setattr(documents, "_delete_sql_row", delete_sql_row)

    helper = getattr(documents, helper_name)

    async def _run_with_pending_cancel():
        current = asyncio.current_task()
        assert current is not None
        current.cancel("before helper")
        return await helper("user-1", "doc-1")

    with pytest.raises(asyncio.CancelledError, match="before helper"):
        await asyncio.create_task(_run_with_pending_cancel())

    get_conn.assert_not_called()
    connection.session.assert_not_called()
    chunks.data.delete_many.assert_not_called()
    papers.data.delete_by_id.assert_not_called()
    delete_sql_row.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_callback", ["on_cancel", "on_cancelled_error"])
async def test_drain_shielded_preserves_cancellation_when_callbacks_raise_cancelled_error(
    failing_callback,
):
    from src.lib.document_cleanup import drain_shielded

    entered = asyncio.Event()
    resume = asyncio.Event()
    cleanup_finished = False

    async def _cleanup():
        nonlocal cleanup_finished
        entered.set()
        await resume.wait()
        cleanup_finished = True
        if failing_callback == "on_cancelled_error":
            raise RuntimeError("cleanup boom")

    def _bad_on_cancel() -> None:
        raise asyncio.CancelledError("bogus on_cancel")

    def _bad_on_error(_exc: Exception) -> None:
        raise asyncio.CancelledError("bogus on_cancelled_error")

    task = asyncio.create_task(
        drain_shielded(
            _cleanup(),
            on_cancel=_bad_on_cancel if failing_callback == "on_cancel" else None,
            on_cancelled_error=_bad_on_error if failing_callback == "on_cancelled_error" else None,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel("original cancel")
    await asyncio.sleep(0)
    # Even when on_cancel raises CancelledError, drain_shielded keeps draining the worker.
    assert not task.done()
    assert cleanup_finished is False

    resume.set()
    with pytest.raises(asyncio.CancelledError, match="original cancel"):
        await asyncio.wait_for(task, timeout=2)

    assert cleanup_finished is True


@pytest.mark.asyncio
async def test_drain_shielded_closes_coro_when_child_cancelled_before_start(monkeypatch):
    import gc
    import warnings
    from src.lib import document_cleanup

    coro_started = False

    async def _never_started():
        nonlocal coro_started
        coro_started = True
        return "done"

    real_create_task = document_cleanup.asyncio.create_task
    task_holder: list[asyncio.Task] = []

    def _cancel_immediately_after_create(coro, *args, **kwargs):
        child = real_create_task(coro, *args, **kwargs)
        if getattr(getattr(coro, "cr_code", None), "co_name", None) == "_guarded":
            child.cancel("child cancelled before first step")
            if task_holder:
                task_holder[0].cancel("owner cancelled after create_task")
        return child

    monkeypatch.setattr(document_cleanup.asyncio, "create_task", _cancel_immediately_after_create)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        task = real_create_task(
            document_cleanup.drain_shielded(_never_started(), started=lambda: False)
        )
        task_holder.append(task)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        gc.collect()

    assert coro_started is False
    unawaited = [
        w for w in caught
        if issubclass(w.category, RuntimeWarning) and "was never awaited" in str(w.message)
    ]
    assert unawaited == []


@pytest.mark.asyncio
async def test_delete_document_sanitizes_postgres_cleanup_warning(monkeypatch, caplog):
    import logging

    chunks, papers = MagicMock(), MagicMock()
    chunks.data.delete_many.return_value = SimpleNamespace(successful=0, matches=0, failed=0)
    papers.data.delete_by_id.return_value = True
    monkeypatch.setattr(documents, "get_connection", lambda: _connection_with_client(MagicMock()))
    monkeypatch.setattr(documents, "get_user_collections", lambda *_: (chunks, papers))
    secret_sql_detail = "private-sql-param-value-for-curator"
    monkeypatch.setattr(
        documents,
        "_delete_sql_row",
        MagicMock(side_effect=RuntimeError(secret_sql_detail)),
    )

    with caplog.at_level(logging.WARNING, logger="src.lib.weaviate_client.documents"):
        result = await documents.delete_document("user-1", "doc-1")

    assert result["success"] is True
    assert result["postgres_deleted"] is False
    assert secret_sql_detail not in caplog.text
    pg_warnings = [
        record for record in caplog.records
        if "Failed to delete document from PostgreSQL" in record.getMessage()
    ]
    assert len(pg_warnings) == 1
    assert "RuntimeError" in pg_warnings[0].getMessage()
    assert getattr(pg_warnings[0], "sentry_skip_event", False) is True


@pytest.mark.asyncio
async def test_cli_delete_enforces_lock_active_job_and_files_before_owner(monkeypatch, tmp_path):
    from uuid import uuid4
    from src.lib.weaviate_client import cli as weaviate_cli

    doc_id = str(uuid4())
    user_id = "user-cli-1"
    pdf_dir = tmp_path / user_id / doc_id
    pdf_dir.mkdir(parents=True)
    pdf_file = pdf_dir / "paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.7\n")

    held: set[int] = set()

    class _FakeLockConn:
        def execute(self, statement, params):
            sql = str(statement)
            key = params["key"]
            if "pg_try_advisory_lock" in sql:
                taken = key not in held
                held.add(key)
                return SimpleNamespace(scalar_one=lambda: taken)
            if "pg_advisory_unlock" in sql:
                held.discard(key)
                return SimpleNamespace(scalar_one=lambda: True)
            raise AssertionError(f"unexpected sql {sql}")

        def commit(self):
            pass

        def close(self):
            pass

        def invalidate(self):
            pass

    monkeypatch.setattr(
        "src.models.sql.database.engine",
        SimpleNamespace(connect=lambda: _FakeLockConn()),
        raising=False,
    )
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))

    main_file = SimpleNamespace(
        role="main",
        display_mode="local_pdf",
        file_path=f"{user_id}/{doc_id}/paper.pdf",
        pdfx_json_path=None,
        processed_json_path=None,
        source_markdown_path=None,
    )
    pdf_doc = SimpleNamespace(id=UUID(doc_id), user_id=42, files=[main_file])
    db_user = SimpleNamespace(id=42, auth_sub=user_id)

    class _FakeDb:
        def get(self, _model, _pk):
            return pdf_doc

        def execute(self, statement):
            sql = str(statement)
            if "pdf_documents" in sql:
                return SimpleNamespace(scalar_one_or_none=lambda: pdf_doc)
            return SimpleNamespace(scalar_one_or_none=lambda: db_user)

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("src.models.sql.database.get_db", lambda: iter([_FakeDb()]))

    events: list[str] = []

    async def _fake_chunks(_uid, _did):
        assert held, "CLI delete must hold paper_operation_lock during chunk wipe"
        events.append("chunks")
        return {"success": True, "chunks_deleted": 2, "chunks_matched": 2}

    async def _fake_parent(_uid, _did, *, allow_missing=False):
        assert allow_missing is True
        assert not pdf_file.exists(), "Stored PDF must be removed before deleting Weaviate parent"
        events.append("parent")
        return {"success": True, "deleted": True}

    def _fake_sql(_uid, _did):
        assert not pdf_file.exists(), "Stored PDF must be removed before deleting SQL owner"
        events.append("sql")
        return True

    monkeypatch.setattr(weaviate_cli, "delete_paper_chunks", _fake_chunks)
    monkeypatch.setattr(weaviate_cli, "delete_paper_object", _fake_parent)
    monkeypatch.setattr(weaviate_cli, "_delete_sql_row", _fake_sql)

    # 1. Active job refuses CLI delete before touching Weaviate, files, or SQL.
    monkeypatch.setattr(
        "src.lib.pdf_jobs.service.get_active_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="running"),
    )
    active_result = await weaviate_cli._delete_document_for_cli(user_id, doc_id)
    assert active_result["success"] is False
    assert "active" in active_result["message"].lower()
    assert pdf_file.exists()
    assert events == []
    assert held == set()

    # 2. Persistent unlink failure retains Weaviate parent and SQL owner for retry.
    monkeypatch.setattr(
        "src.lib.pdf_jobs.service.get_active_job_for_document",
        lambda **_kwargs: None,
    )
    from src.lib.document_files import service as doc_files_service
    orig_rmtree = doc_files_service.shutil.rmtree
    monkeypatch.setattr(
        doc_files_service.shutil,
        "rmtree",
        lambda *_a, **_kw: (_ for _ in ()).throw(PermissionError("locked")),
    )
    unlink_fail_result = await weaviate_cli._delete_document_for_cli(user_id, doc_id)
    assert unlink_fail_result["success"] is False
    assert unlink_fail_result["postgres_deleted"] is False
    assert pdf_file.exists()
    assert events == ["chunks"]
    assert held == set()

    # 3. Normal CLI delete completes chunks -> files -> parent -> SQL and releases lock.
    monkeypatch.setattr(doc_files_service.shutil, "rmtree", orig_rmtree)
    events.clear()
    ok_result = await weaviate_cli._delete_document_for_cli(user_id, doc_id)
    assert ok_result["success"] is True
    assert ok_result["postgres_deleted"] is True
    assert events == ["chunks", "parent", "sql"]
    assert not pdf_file.exists()
    assert held == set()


@pytest.mark.asyncio
async def test_cli_delete_ownership_preflight_runs_before_active_job_reconciliation(monkeypatch, tmp_path):
    from uuid import uuid4
    from src.lib.weaviate_client import cli as weaviate_cli

    doc_id = str(uuid4())
    held: set[int] = set()

    class _FakeLockConn:
        def execute(self, statement, params):
            sql = str(statement)
            key = params["key"]
            if "pg_try_advisory_lock" in sql:
                taken = key not in held
                held.add(key)
                return SimpleNamespace(scalar_one=lambda: taken)
            if "pg_advisory_unlock" in sql:
                held.discard(key)
                return SimpleNamespace(scalar_one=lambda: True)
            raise AssertionError(sql)

        def commit(self):
            pass

        def close(self):
            pass

        def invalidate(self):
            pass

    monkeypatch.setattr(
        "src.models.sql.database.engine",
        SimpleNamespace(connect=lambda: _FakeLockConn()),
        raising=False,
    )
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))

    # Document belongs to user_id=99, caller is wrong user (user_id=42).
    pdf_doc = SimpleNamespace(id=UUID(doc_id), user_id=99, files=[])
    wrong_user = SimpleNamespace(id=42, auth_sub="wrong-user")

    class _FakeDb:
        def get(self, _model, _pk):
            return pdf_doc

        def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: wrong_user)

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("src.models.sql.database.get_db", lambda: iter([_FakeDb()]))
    active_job_spy = MagicMock(side_effect=AssertionError("must not query/reconcile another owner's jobs"))
    monkeypatch.setattr("src.lib.pdf_jobs.service.get_active_job_for_document", active_job_spy)
    chunks_spy = MagicMock()
    monkeypatch.setattr(weaviate_cli, "delete_paper_chunks", chunks_spy)

    result = await weaviate_cli._delete_document_for_cli("wrong-user", doc_id)
    assert result["success"] is False
    assert "ownership mismatch" in result["message"].lower()
    active_job_spy.assert_not_called()
    chunks_spy.assert_not_called()
    assert held == set()


@pytest.mark.asyncio
async def test_cli_delete_refuses_orphan_weaviate_delete_when_tenant_dir_has_artifacts(monkeypatch, tmp_path):
    from uuid import uuid4
    from src.lib.weaviate_client import cli as weaviate_cli

    doc_id = str(uuid4())
    user_id = "user-orphan-1"
    pdf_dir = tmp_path / user_id / doc_id
    pdf_dir.mkdir(parents=True)
    pdf_file = pdf_dir / "stranded.pdf"
    pdf_file.write_bytes(b"%PDF-1.7\n")

    held: set[int] = set()

    class _FakeLockConn:
        def execute(self, statement, params):
            sql = str(statement)
            key = params["key"]
            if "pg_try_advisory_lock" in sql:
                taken = key not in held
                held.add(key)
                return SimpleNamespace(scalar_one=lambda: taken)
            if "pg_advisory_unlock" in sql:
                held.discard(key)
                return SimpleNamespace(scalar_one=lambda: True)
            raise AssertionError(sql)

        def commit(self):
            pass

        def close(self):
            pass

        def invalidate(self):
            pass

    monkeypatch.setattr(
        "src.models.sql.database.engine",
        SimpleNamespace(connect=lambda: _FakeLockConn()),
        raising=False,
    )
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))

    # No SQL row exists (e.g., after a pre-commit unlink failure retained Weaviate + PDF).
    class _FakeDb:
        def get(self, _model, _pk):
            return None

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("src.models.sql.database.get_db", lambda: iter([_FakeDb()]))
    chunks_spy = MagicMock()
    parent_spy = MagicMock()
    monkeypatch.setattr(weaviate_cli, "delete_paper_chunks", chunks_spy)
    monkeypatch.setattr(weaviate_cli, "delete_paper_object", parent_spy)

    result = await weaviate_cli._delete_document_for_cli(user_id, doc_id)
    assert result["success"] is False
    assert "stored files" in result["message"].lower()
    assert pdf_file.exists()
    chunks_spy.assert_not_called()
    parent_spy.assert_not_called()
    assert held == set()


def test_cli_delete_cmd_exits_nonzero_when_sql_owner_delete_fails(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from uuid import uuid4
    from src.lib.weaviate_client import cli as weaviate_cli

    doc_id = str(uuid4())
    user_id = "user-sqlfail-1"
    held: set[int] = set()

    class _FakeLockConn:
        def execute(self, statement, params):
            sql = str(statement)
            key = params["key"]
            if "pg_try_advisory_lock" in sql:
                taken = key not in held
                held.add(key)
                return SimpleNamespace(scalar_one=lambda: taken)
            if "pg_advisory_unlock" in sql:
                held.discard(key)
                return SimpleNamespace(scalar_one=lambda: True)
            raise AssertionError(sql)

        def commit(self):
            pass

        def close(self):
            pass

        def invalidate(self):
            pass

    monkeypatch.setattr(
        "src.models.sql.database.engine",
        SimpleNamespace(connect=lambda: _FakeLockConn()),
        raising=False,
    )
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))

    pdf_doc = SimpleNamespace(id=UUID(doc_id), user_id=42, files=[])
    db_user = SimpleNamespace(id=42, auth_sub=user_id)

    class _FakeDb:
        def get(self, _model, _pk):
            return pdf_doc

        def execute(self, statement):
            if "pdf_documents" in str(statement):
                return SimpleNamespace(scalar_one_or_none=lambda: pdf_doc)
            return SimpleNamespace(scalar_one_or_none=lambda: db_user)

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("src.models.sql.database.get_db", lambda: iter([_FakeDb()]))
    monkeypatch.setattr("src.lib.pdf_jobs.service.get_active_job_for_document", lambda **_kw: None)

    async def _ok_chunks(_uid, _did):
        return {"success": True, "chunks_deleted": 0, "chunks_matched": 0}

    async def _ok_parent(_uid, _did, *, allow_missing=False):
        return {"success": True, "deleted": True}

    monkeypatch.setattr(weaviate_cli, "delete_paper_chunks", _ok_chunks)
    monkeypatch.setattr(weaviate_cli, "delete_paper_object", _ok_parent)
    monkeypatch.setattr(
        weaviate_cli,
        "_delete_sql_row",
        MagicMock(side_effect=RuntimeError("postgres delete failed")),
    )

    runner = CliRunner()
    cli_result = runner.invoke(
        weaviate_cli.delete_document_cmd,
        [doc_id, "--user-id", user_id, "--yes"],
    )
    assert cli_result.exit_code == 1
    assert "postgresql" in cli_result.output.lower()
    assert held == set()


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
    db_session.scalar.return_value = "library"
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
    assert result["schema_version"] == "1.2.3"
    assert set(result) == {"document", "chunks", "total_chunks", "schema_version"}


@pytest.mark.asyncio
async def test_get_document_parses_chunk_metadata_without_retired_detail_fields():
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
    db_session.scalar.return_value = "library"
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
    assert result["schema_version"] == "2.4.0"
    assert set(result) == {"document", "chunks", "total_chunks", "schema_version"}
