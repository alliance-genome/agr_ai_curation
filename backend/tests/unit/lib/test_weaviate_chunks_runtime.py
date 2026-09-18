"""Runtime branch tests for Weaviate chunk helpers."""

import asyncio
from contextlib import contextmanager
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import grpc
import pytest
from weaviate.exceptions import WeaviateQueryError

import src.lib.weaviate_client.chunks as chunks


def _connection_with_client(client):
    @contextmanager
    def _session():
        yield client

    connection = MagicMock()
    connection.session.side_effect = _session
    return connection


def _sync_to_thread(monkeypatch):
    async def _immediate(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _immediate)


def test_store_chunks_requires_user_id():
    with pytest.raises(ValueError):
        chunks.store_chunks("doc-1", [], "")


def test_store_chunks_requires_connection(monkeypatch):
    monkeypatch.setattr(chunks, "get_connection", lambda: None)
    with pytest.raises(RuntimeError):
        chunks.store_chunks("doc-1", [], "user-1")


def test_store_chunks_success_with_preview_and_doc_items():
    chunk_collection = MagicMock()
    chunk_collection.data.insert_many.return_value = SimpleNamespace(errors=None)
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    payload = [
        {
            "content": "This is a long paragraph " * 120,
            "element_type": "NarrativeText",
            "page_number": 2,
            "section_title": "Results",
            "metadata": {"section_title": "Results"},
            "doc_items": [{"id": "bbox-1"}],
        },
        {
            "content": "Short second chunk",
            "has_table": True,
            "has_image": False,
        },
    ]

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        result = chunks.store_chunks("doc-1", payload, "user-1")

    assert result["success"] is True
    assert result["chunkCount"] == 2
    assert len(result["chunkIds"]) == 2
    chunk_collection.data.insert_many.assert_called_once()
    inserted_objects = chunk_collection.data.insert_many.call_args.args[0]
    assert len(inserted_objects) == 2
    assert inserted_objects[0].properties["contentPreview"].endswith("...")
    assert "docItemProvenance" in inserted_objects[0].properties
    assert inserted_objects[1].properties["elementType"] == "NarrativeText"
    assert inserted_objects[1].properties["pageNumber"] is None
    pdf_collection.data.update.assert_called_once()


def test_store_chunks_returns_error_payload_on_insert_failure():
    chunk_collection = MagicMock()
    chunk_collection.data.insert_many.side_effect = RuntimeError("insert failed")
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        result = chunks.store_chunks("doc-1", [{"content": "abc"}], "user-1")

    assert result["success"] is False
    assert result["error"]["code"] == "CHUNK_STORE_FAILED"
    assert "insert failed" in result["message"]


def test_delete_chunks_requires_user_id():
    with pytest.raises(ValueError):
        chunks.delete_chunks("doc-1", "")


def test_delete_chunks_success():
    chunk_collection = MagicMock()
    chunk_collection.data.delete_many.return_value = {"results": {"successful": 3}}
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        result = chunks.delete_chunks("doc-1", "user-1")

    assert result["success"] is True
    assert result["deletedCount"] == 3
    pdf_collection.data.update.assert_called_once()
    chunk_collection.data.delete_many.assert_called_once()


def test_delete_chunks_failure_returns_error_payload():
    chunk_collection = MagicMock()
    chunk_collection.data.delete_many.side_effect = RuntimeError("delete failed")
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        result = chunks.delete_chunks("doc-1", "user-1")

    assert result["success"] is False
    assert result["error"]["code"] == "CHUNK_DELETE_FAILED"


def test_update_chunk_embeddings_requires_user_id():
    with pytest.raises(ValueError):
        chunks.update_chunk_embeddings("chunk-1", [0.1], "")


def test_update_chunk_embeddings_success():
    chunk_collection = MagicMock()
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        result = chunks.update_chunk_embeddings("chunk-1", [0.1, 0.2], "user-1")

    assert result["success"] is True
    assert result["chunkId"] == "chunk-1"
    chunk_collection.data.update.assert_called_once_with(
        uuid="chunk-1",
        properties={},
        vector=[0.1, 0.2],
    )


def test_update_chunk_embeddings_failure_returns_error_payload():
    chunk_collection = MagicMock()
    chunk_collection.data.update.side_effect = RuntimeError("vector write failed")
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        result = chunks.update_chunk_embeddings("chunk-1", [0.1], "user-1")

    assert result["success"] is False
    assert result["error"]["code"] == "EMBEDDING_UPDATE_FAILED"


@pytest.mark.asyncio
async def test_get_chunks_requires_user_id():
    with pytest.raises(ValueError):
        await chunks.get_chunks("doc-1", {"page": 1, "page_size": 10}, "")


@pytest.mark.asyncio
async def test_get_chunks_success_parses_metadata_and_doc_items(monkeypatch):
    _sync_to_thread(monkeypatch)

    page_response = SimpleNamespace(
        objects=[
            SimpleNamespace(
                uuid="chunk-uuid-1",
                properties={
                    "chunkIndex": 0,
                    "content": "Alpha beta",
                    "contentPreview": "Alpha",
                    "elementType": "NarrativeText",
                    "pageNumber": 1,
                    "sectionTitle": "Intro",
                    "metadata": '{"section_title":"Intro","word_count":2}',
                    "docItemProvenance": '[{"id":"bbox-1"}]',
                },
            ),
            SimpleNamespace(
                uuid="chunk-uuid-2",
                properties={
                    "chunkIndex": 1,
                    "content": "Second chunk content",
                    "contentPreview": "Second",
                    "elementType": "NarrativeText",
                    "pageNumber": 2,
                    "sectionTitle": "Methods",
                    "metadata": "{bad-json",
                    "docItemProvenance": "not-json",
                },
            ),
        ]
    )
    total_response = SimpleNamespace(objects=[SimpleNamespace(), SimpleNamespace()])

    chunk_collection = MagicMock()
    chunk_collection.query.fetch_objects.side_effect = [page_response, total_response]
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        result = await chunks.get_chunks(
            "doc-1",
            {"page": 1, "page_size": 10, "include_metadata": True},
            "user-1",
        )

    assert result["total"] == 2
    assert len(result["chunks"]) == 2
    first = result["chunks"][0]
    second = result["chunks"][1]
    assert first["metadata"]["section_title"] == "Intro"
    assert first["doc_items"] == [{"id": "bbox-1"}]
    assert second["doc_items"] == []
    assert second["metadata"]["character_count"] == len("Second chunk content")
    assert second["metadata"]["word_count"] == 3


@pytest.mark.asyncio
async def test_get_chunk_by_id_falls_back_when_thread_creation_is_unavailable(monkeypatch):
    obj = SimpleNamespace(
        uuid="chunk-uuid-1",
        properties={
            "documentId": "doc-1",
            "content": "Alpha beta",
            "contentPreview": "Alpha beta",
            "chunkIndex": 0,
            "sectionTitle": "Intro",
            "parentSection": "Intro",
            "subsection": "Overview",
            "pageNumber": 1,
            "metadata": '{"kind":"narrative"}',
            "docItemProvenance": '[{"id":"bbox-1"}]',
        },
    )
    chunk_collection = MagicMock()
    chunk_collection.query.fetch_object_by_id.return_value = obj
    connection = _connection_with_client(MagicMock())

    async def _broken_to_thread(_func, *args, **kwargs):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(asyncio, "to_thread", _broken_to_thread)

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())):
        result = await chunks.get_chunk_by_id("chunk-uuid-1", "user-1", document_id="doc-1")

    assert result == {
        "id": "chunk-uuid-1",
        "document_id": "doc-1",
        "text": "Alpha beta",
        "content_preview": "Alpha beta",
        "chunk_index": 0,
        "section_title": "Intro",
        "parent_section": "Intro",
        "subsection": "Overview",
        "page_number": 1,
        "metadata": {"kind": "narrative"},
        "doc_items": [{"id": "bbox-1"}],
    }


@pytest.mark.asyncio
async def test_get_chunks_raises_when_query_fails(monkeypatch):
    _sync_to_thread(monkeypatch)

    chunk_collection = MagicMock()
    chunk_collection.query.fetch_objects.side_effect = RuntimeError("fetch failed")
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        with pytest.raises(RuntimeError, match="fetch failed"):
            await chunks.get_chunks("doc-1", {"page": 1, "page_size": 5}, "user-1")


@pytest.mark.asyncio
async def test_get_chunks_include_metadata_false_returns_minimal_required_metadata(monkeypatch):
    _sync_to_thread(monkeypatch)

    page_response = SimpleNamespace(
        objects=[
            SimpleNamespace(
                uuid="chunk-uuid-1",
                properties={
                    "chunkIndex": 0,
                    "content": "Alpha beta gamma",
                    "contentPreview": "Alpha beta gamma",
                    "elementType": "NarrativeText",
                    "pageNumber": 1,
                    "sectionTitle": "Intro",
                    "metadata": '{"section_title":"Intro","character_count":16,"word_count":3,"content_type":"text"}',
                    "docItemProvenance": "[]",
                },
            ),
        ]
    )
    total_response = SimpleNamespace(objects=[SimpleNamespace()])

    chunk_collection = MagicMock()
    chunk_collection.query.fetch_objects.side_effect = [page_response, total_response]
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())):
        result = await chunks.get_chunks(
            "doc-1",
            {"page": 1, "page_size": 10, "include_metadata": False},
            "user-1",
        )

    assert result["total"] == 1
    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["metadata"] == {
        "character_count": 16,
        "word_count": 3,
    }


@pytest.mark.asyncio
async def test_get_chunks_include_metadata_false_repairs_null_required_metadata(monkeypatch):
    _sync_to_thread(monkeypatch)

    page_response = SimpleNamespace(
        objects=[
            SimpleNamespace(
                uuid="chunk-uuid-1",
                properties={
                    "chunkIndex": 0,
                    "content": "Alpha beta gamma",
                    "contentPreview": "Alpha beta gamma",
                    "elementType": "NarrativeText",
                    "pageNumber": 1,
                    "sectionTitle": "Intro",
                    "metadata": '{"character_count": null, "word_count": null}',
                    "docItemProvenance": "[]",
                },
            ),
        ]
    )
    total_response = SimpleNamespace(objects=[SimpleNamespace()])

    chunk_collection = MagicMock()
    chunk_collection.query.fetch_objects.side_effect = [page_response, total_response]
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())):
        result = await chunks.get_chunks(
            "doc-1",
            {"page": 1, "page_size": 10, "include_metadata": False},
            "user-1",
        )

    assert result["total"] == 1
    assert result["chunks"][0]["metadata"] == {
        "character_count": 16,
        "word_count": 3,
    }


@pytest.mark.asyncio
async def test_get_chunk_by_id_runs_inline_in_package_tool_subprocess(monkeypatch):
    monkeypatch.setenv("AGR_AI_CURATION_PACKAGE_TOOL_SUBPROCESS", "1")

    chunk_collection = MagicMock()
    chunk_collection.query.fetch_object_by_id.return_value = SimpleNamespace(
        uuid="chunk-uuid-1",
        properties={
            "documentId": "doc-1",
            "content": "Inline chunk content",
            "contentPreview": "Inline chunk",
            "chunkIndex": 3,
            "sectionTitle": "Results",
            "parentSection": "Results",
            "subsection": "Evidence",
            "pageNumber": 5,
            "metadata": '{"word_count": 3}',
            "docItemProvenance": '[{"id":"bbox-1"}]',
        },
    )
    pdf_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    async def _explode_to_thread(*args, **kwargs):
        raise AssertionError("asyncio.to_thread should not run in package tool subprocesses")

    monkeypatch.setattr(asyncio, "to_thread", _explode_to_thread)

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, pdf_collection)):
        result = await chunks.get_chunk_by_id(
            "chunk-uuid-1",
            "user-1",
            document_id="doc-1",
        )

    assert result == {
        "id": "chunk-uuid-1",
        "document_id": "doc-1",
        "text": "Inline chunk content",
        "content_preview": "Inline chunk",
        "chunk_index": 3,
        "section_title": "Results",
        "parent_section": "Results",
        "subsection": "Evidence",
        "page_number": 5,
        "metadata": {"word_count": 3},
        "doc_items": [{"id": "bbox-1"}],
    }


@pytest.mark.asyncio
async def test_get_chunk_neighbor_ids_fetches_adjacent_chunk_ids(monkeypatch):
    _sync_to_thread(monkeypatch)

    chunk_collection = MagicMock()
    chunk_collection.query.fetch_objects.side_effect = [
        SimpleNamespace(objects=[SimpleNamespace(uuid="chunk-prev")]),
        SimpleNamespace(objects=[SimpleNamespace(uuid="chunk-next")]),
    ]
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())):
        result = await chunks.get_chunk_neighbor_ids(
            document_id="doc-1",
            user_id="user-1",
            chunk_index=5,
        )

    assert result == {
        "previous_chunk_id": "chunk-prev",
        "next_chunk_id": "chunk-next",
    }
    assert chunk_collection.query.fetch_objects.call_count == 2


@pytest.mark.asyncio
async def test_get_chunk_neighbor_ids_requires_weaviate_objects_response(monkeypatch):
    _sync_to_thread(monkeypatch)

    chunk_collection = MagicMock()
    chunk_collection.query.fetch_objects.return_value = SimpleNamespace()
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())):
        with pytest.raises(AttributeError, match="objects"):
            await chunks.get_chunk_neighbor_ids(
                document_id="doc-1",
                user_id="user-1",
                chunk_index=5,
            )


@pytest.mark.asyncio
async def test_get_chunk_neighbor_ids_returns_empty_when_index_missing():
    result = await chunks.get_chunk_neighbor_ids(
        document_id="doc-1",
        user_id="user-1",
        chunk_index=None,
    )

    assert result == {
        "previous_chunk_id": None,
        "next_chunk_id": None,
    }


@pytest.mark.asyncio
async def test_get_chunk_neighbor_ids_rejects_invalid_chunk_index_type():
    with pytest.raises(TypeError, match="chunk_index must be an int or None"):
        await chunks.get_chunk_neighbor_ids(
            document_id="doc-1",
            user_id="user-1",
            chunk_index="5",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_get_chunk_neighbor_ids_propagates_thread_creation_failure(monkeypatch):
    async def _broken_to_thread(_func, *args, **kwargs):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(asyncio, "to_thread", _broken_to_thread)
    connection = _connection_with_client(MagicMock())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection):
        with pytest.raises(RuntimeError, match="can't start new thread"):
            await chunks.get_chunk_neighbor_ids(
                document_id="doc-1",
                user_id="user-1",
                chunk_index=5,
            )


@pytest.mark.asyncio
async def test_hybrid_search_retry_adapter_branches(monkeypatch):
    _sync_to_thread(monkeypatch)

    calls = []

    def _search(alpha_override, rerank, mmr):
        calls.append((alpha_override, rerank, mmr))
        # no results through lexical and first fallback, then final fallback returns data
        return [] if len(calls) < 4 else [{"id": "chunk-1"}]

    # lexical mode
    lexical = await chunks.hybrid_search_chunks_retry_adapter(_search, strategy="lexical", short_token=False)
    assert lexical == []
    assert calls[0] == (0.0, None, None)

    # hybrid lexical-first retries through both fallback attempts
    fallback = await chunks.hybrid_search_chunks_retry_adapter(_search, strategy="hybrid_lexical_first", short_token=False)
    assert fallback == [{"id": "chunk-1"}]
    assert calls[1] == (None, None, None)
    assert calls[2] == (0.0, None, None)
    assert calls[3] == (0.3, None, None)

    # short token should force lexical-first sequence even when strategy is hybrid
    calls.clear()

    def _search_short(alpha_override, rerank, mmr):
        calls.append((alpha_override, rerank, mmr))
        return [{"id": "chunk-2"}]

    short = await chunks.hybrid_search_chunks_retry_adapter(_search_short, strategy="hybrid", short_token=True)
    assert short == [{"id": "chunk-2"}]
    assert calls == [(None, None, None)]


# --- ALL-1246: single retry on Weaviate gRPC DEADLINE_EXCEEDED ---

_SEARCH_QUERY_SENTINEL = "QUERY-TEXT-SENTINEL-91b2 allele mentions in methods"


class _FakeRpcError(grpc.RpcError):
    def __init__(self, status_code):
        super().__init__()
        self._status_code = status_code

    def code(self):
        return self._status_code

    def details(self):
        return "Deadline Exceeded" if self._status_code == grpc.StatusCode.DEADLINE_EXCEEDED else "unavailable"


def _weaviate_query_error(status_code=None, message="Deadline Exceeded"):
    """Raise and capture WeaviateQueryError the way weaviate-client 4.21 does."""
    try:
        try:
            if status_code is None:
                raise RuntimeError("no grpc context")
            raise _FakeRpcError(status_code)
        except Exception:
            raise WeaviateQueryError(message, "GRPC search")
    except WeaviateQueryError as error:
        if status_code is None:
            error.__context__ = None
        return error


def _hybrid_response(uuid="chunk-1"):
    return SimpleNamespace(objects=[
        SimpleNamespace(
            uuid=uuid,
            properties={
                "content": "Allele text", "contentPreview": "Allele", "pageNumber": 3,
                "chunkIndex": 1, "sectionTitle": "Methods", "elementType": "NarrativeText",
                "documentId": "doc-1", "metadata": None, "docItemProvenance": None,
            },
            metadata=SimpleNamespace(score=0.9),
            vector=None,
        )
    ])


async def _run_hybrid_search(monkeypatch, hybrid_side_effect, **kwargs):
    _sync_to_thread(monkeypatch)
    monkeypatch.setattr(chunks, "get_effective_rerank_provider", lambda: "none")
    chunk_collection = MagicMock()
    chunk_collection.query.hybrid.side_effect = hybrid_side_effect
    connection = _connection_with_client(MagicMock())
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())):
        try:
            return await chunks.hybrid_search_chunks(
                document_id="doc-1",
                query=kwargs.pop("query", _SEARCH_QUERY_SENTINEL),
                user_id="user-1",
                limit=5,
                apply_mmr=False,
                **kwargs,
            ), chunk_collection
        except Exception as error:
            error.chunk_collection = chunk_collection
            raise


def _search_records(caplog, level):
    return [
        record for record in caplog.records
        if record.name == chunks.logger.name and record.levelno == level
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_error",
    [
        _weaviate_query_error(grpc.StatusCode.DEADLINE_EXCEEDED),
        _weaviate_query_error(None, message="Deadline Exceeded"),
    ],
    ids=["grpc-status", "message-fallback"],
)
async def test_hybrid_search_retries_once_after_deadline_exceeded(monkeypatch, caplog, first_error):
    caplog.set_level(logging.INFO, logger=chunks.logger.name)

    results, collection = await _run_hybrid_search(
        monkeypatch, [first_error, _hybrid_response()],
    )

    assert [chunk["id"] for chunk in results] == ["chunk-1"]
    assert collection.query.hybrid.call_count == 2
    assert _search_records(caplog, logging.ERROR) == []
    warnings = [
        record for record in _search_records(caplog, logging.WARNING)
        if getattr(record, "operation", None) == "weaviate_hybrid_search_deadline_retry"
    ]
    assert len(warnings) == 1
    assert warnings[0].attempt == 1
    assert isinstance(warnings[0].duration_ms, float)
    assert warnings[0].exc_info is None
    assert "QUERY-TEXT-SENTINEL" not in warnings[0].getMessage()
    assert "QUERY-TEXT-SENTINEL" not in repr(vars(warnings[0]))


@pytest.mark.asyncio
async def test_hybrid_search_reports_second_deadline_failure_without_sentry_event(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=chunks.logger.name)
    deadline = grpc.StatusCode.DEADLINE_EXCEEDED

    with pytest.raises(WeaviateQueryError) as raised:
        await _run_hybrid_search(
            monkeypatch,
            [_weaviate_query_error(deadline), _weaviate_query_error(deadline)],
        )

    assert raised.value.chunk_collection.query.hybrid.call_count == 2
    errors = _search_records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert errors[0].sentry_skip_event is True
    assert errors[0].exc_info is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        _weaviate_query_error(grpc.StatusCode.UNAVAILABLE, message="unavailable"),
        _weaviate_query_error(grpc.StatusCode.INTERNAL, message="Deadline Exceeded"),
        _weaviate_query_error(None, message="index not found"),
        RuntimeError("Deadline Exceeded"),
    ],
    ids=["unavailable", "grpc-status-wins-over-message", "other-query-error", "not-a-query-error"],
)
async def test_hybrid_search_does_not_retry_other_errors(monkeypatch, caplog, error):
    caplog.set_level(logging.INFO, logger=chunks.logger.name)

    with pytest.raises(type(error)) as raised:
        await _run_hybrid_search(monkeypatch, [error, _hybrid_response()])

    assert raised.value.chunk_collection.query.hybrid.call_count == 1
    assert not [
        record for record in caplog.records
        if getattr(record, "operation", None) == "weaviate_hybrid_search_deadline_retry"
    ]


@pytest.mark.asyncio
async def test_hybrid_search_deadline_retry_budget_spans_lexical_first_attempts(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=chunks.logger.name)
    deadline = grpc.StatusCode.DEADLINE_EXCEEDED

    # Attempt 1 times out and its single retry returns no rows, so the
    # lexical-first adapter moves on; the adapter's next attempt must not get
    # a second deadline retry.
    with pytest.raises(WeaviateQueryError) as raised:
        await _run_hybrid_search(
            monkeypatch,
            [
                _weaviate_query_error(deadline),
                SimpleNamespace(objects=[]),
                _weaviate_query_error(deadline),
                _hybrid_response(),
            ],
            strategy="hybrid_lexical_first",
        )

    assert raised.value.chunk_collection.query.hybrid.call_count == 3
    assert len([
        record for record in caplog.records
        if getattr(record, "operation", None) == "weaviate_hybrid_search_deadline_retry"
    ]) == 1
