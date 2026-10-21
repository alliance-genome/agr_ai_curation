"""Navigation/search path tests for Weaviate chunk helpers."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.lib.weaviate_client.chunks as chunks
from src.lib.bedrock_reranker import RerankProviderError


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


@pytest.mark.asyncio
async def test_hybrid_search_chunks_runs_full_path_with_mmr(monkeypatch, caplog):
    _sync_to_thread(monkeypatch)
    caplog.set_level("INFO", logger="src.lib.weaviate_client.chunks")

    obj1 = SimpleNamespace(
        uuid="u1",
        properties={
            "content": "Chunk one content",
            "pageNumber": 1,
            "chunkIndex": 0,
            "sectionTitle": "Intro",
            "elementType": "NarrativeText",
            "documentId": "doc-1",
            "metadata": '{"x":1}',
            "docItemProvenance": '[{"id":"bbox-1"}]',
        },
        metadata=SimpleNamespace(score=0.9, explain_score="ok"),
        vector=[0.1, 0.2],
    )
    obj2 = SimpleNamespace(
        uuid="u2",
        properties={
            "content": "Chunk two content",
            "pageNumber": 2,
            "chunkIndex": 1,
            "sectionTitle": "Methods",
            "elementType": "NarrativeText",
            "documentId": "doc-1",
            "metadata": {"y": 2},
            "docItemProvenance": [],
        },
        metadata=SimpleNamespace(score=0.8, explain_score="ok"),
        vector=[0.2, 0.3],
    )
    obj3 = SimpleNamespace(
        uuid="u3",
        properties={
            "content": "Chunk three content",
            "pageNumber": 3,
            "chunkIndex": 2,
            "sectionTitle": "Results",
            "elementType": "NarrativeText",
            "documentId": "doc-1",
            "metadata": "{bad-json",
            "docItemProvenance": "{bad-json",
        },
        metadata=SimpleNamespace(score=0.7, explain_score="ok"),
        vector=[0.3, 0.4],
    )
    response = SimpleNamespace(objects=[obj1, obj2, obj3])

    chunk_collection = MagicMock()
    chunk_collection.query.hybrid.return_value = response
    connection = _connection_with_client(MagicMock())

    monkeypatch.setenv("RETRIEVAL_EXPLAIN", "true")
    monkeypatch.setenv("WEAVIATE_VERSION", "1.31")
    monkeypatch.setattr(
        "src.lib.weaviate_client.mmr_diversifier.mmr_diversify",
        lambda rows, lambda_param, top_k, vector_field: rows[:top_k],
    )

    raw_query = "this is a sufficiently long query for reranking"
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), patch(
        "src.lib.weaviate_helpers.get_user_collections",
        return_value=(chunk_collection, MagicMock()),
    ):
        results = await chunks.hybrid_search_chunks(
            document_id="doc-1",
            query=raw_query,
            user_id="user-1",
            limit=2,
            initial_limit=10,
            apply_reranking=False,
            apply_mmr=True,
            section_keywords=["methods"],
            strategy="lexical",
        )

    assert len(results) == 2
    assert results[0]["id"] == "u1"
    assert [row["chunk_index"] for row in results] == [0, 1]
    assert "_vector" not in results[0]
    assert results[0]["metadata"]["doc_items"] == [{"id": "bbox-1"}]
    assert results[1]["metadata"]["chunk_id"] == "u2"
    query_kwargs = chunk_collection.query.hybrid.call_args.kwargs
    assert "auto_limit" not in query_kwargs
    assert query_kwargs["alpha"] == 0.0
    assert query_kwargs["query_properties"] == ["content"]

    audit_record = next(
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "weaviate_retrieval_ranking_audit"
    )
    assert audit_record.retrieval_alpha == 0.0
    assert audit_record.retrieval_query_properties == ["content"]
    assert len(audit_record.retrieval_candidates) == 3
    assert len(audit_record.reranked_candidates) == 3
    assert len(audit_record.final_results) == 2
    assert all("content" not in row for row in audit_record.retrieval_candidates)
    assert len(audit_record.retrieval_query_fingerprint) == 16
    assert all(raw_query not in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_hybrid_search_chunks_applies_backend_reranking(monkeypatch):
    _sync_to_thread(monkeypatch)

    obj1 = SimpleNamespace(
        uuid="u1",
        properties={
            "content": "Chunk one content",
            "contentPreview": "Preview one",
            "pageNumber": 1,
            "chunkIndex": 0,
            "sectionTitle": "Intro",
            "elementType": "NarrativeText",
            "documentId": "doc-1",
            "metadata": '{"x":1}',
            "docItemProvenance": "[]",
        },
        metadata=SimpleNamespace(score=0.9, explain_score="ok"),
        vector=[0.1, 0.2],
    )
    obj2 = SimpleNamespace(
        uuid="u2",
        properties={
            "content": "Chunk two content",
            "contentPreview": "Preview two",
            "pageNumber": 2,
            "chunkIndex": 1,
            "sectionTitle": "Results",
            "elementType": "NarrativeText",
            "documentId": "doc-1",
            "metadata": '{"y":2}',
            "docItemProvenance": "[]",
        },
        metadata=SimpleNamespace(score=0.8, explain_score="ok"),
        vector=[0.2, 0.3],
    )
    response = SimpleNamespace(objects=[obj1, obj2])
    chunk_collection = MagicMock()
    chunk_collection.query.hybrid.return_value = response
    connection = _connection_with_client(MagicMock())

    def _rerank(query, rows, *, top_n):
        assert query == "gene expression in developing retina"
        assert top_n == 2
        assert rows[0]["content_preview"] == "Preview one"
        assert rows[0]["_rerank_text"] == "Chunk one content"
        assert rows[1]["_rerank_text"] == "Chunk two content"
        return list(reversed(rows))

    monkeypatch.setattr(chunks, "get_effective_rerank_provider", lambda: "bedrock_cohere")
    monkeypatch.setattr(chunks, "rerank_chunks", _rerank)

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), patch(
        "src.lib.weaviate_helpers.get_user_collections",
        return_value=(chunk_collection, MagicMock()),
    ):
        results = await chunks.hybrid_search_chunks(
            document_id="doc-1",
            query="gene expression in developing retina",
            user_id="user-1",
            limit=2,
            apply_reranking=True,
            apply_mmr=False,
        )

    assert [chunk["id"] for chunk in results] == ["u2", "u1"]
    assert [row["chunk_index"] for row in results] == [1, 0]
    assert "rerank" not in chunk_collection.query.hybrid.call_args.kwargs
    assert "auto_limit" not in chunk_collection.query.hybrid.call_args.kwargs
    assert chunk_collection.query.hybrid.call_args.kwargs["limit"] == 50
    assert chunk_collection.query.hybrid.call_args.kwargs["alpha"] == 0.4
    assert chunk_collection.query.hybrid.call_args.kwargs["include_vector"] is False
    assert "_rerank_text" not in results[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "failure_category"),
    [("bedrock_cohere", "provider_failure"), ("", "configuration")],
    ids=["provider-outage", "blank-provider"],
)
async def test_hybrid_search_chunks_surfaces_configured_rerank_failure(
    monkeypatch, provider, failure_category
):
    _sync_to_thread(monkeypatch)
    obj = SimpleNamespace(
        uuid="u1",
        properties={
            "content": "Chunk one content",
            "contentPreview": "Preview one",
            "pageNumber": 1,
            "chunkIndex": 0,
            "sectionTitle": "Intro",
            "elementType": "NarrativeText",
            "documentId": "doc-1",
            "metadata": "{}",
            "docItemProvenance": "[]",
        },
        metadata=SimpleNamespace(score=0.9, explain_score="ok"),
        vector=[0.1, 0.2],
    )
    chunk_collection = MagicMock()
    chunk_collection.query.hybrid.return_value = SimpleNamespace(objects=[obj])
    connection = _connection_with_client(MagicMock())
    failure = RerankProviderError(provider, failure_category)

    monkeypatch.setattr(chunks, "get_effective_rerank_provider", lambda: provider)
    monkeypatch.setattr(
        chunks,
        "rerank_chunks",
        lambda query, rows, *, top_n: (_ for _ in ()).throw(failure),
    )

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), patch(
        "src.lib.weaviate_helpers.get_user_collections",
        return_value=(chunk_collection, MagicMock()),
    ):
        with pytest.raises(RerankProviderError) as exc_info:
            await chunks.hybrid_search_chunks(
                document_id="doc-1",
                query="gene expression in developing retina",
                user_id="user-1",
                limit=1,
                apply_reranking=True,
                apply_mmr=False,
                strategy="hybrid_lexical_first",
            )

    assert exc_info.value is failure
    assert chunk_collection.query.hybrid.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected_minimum_match"),
    (("CG18135", 1), ("CG18135 insertion", 2)),
)
async def test_hybrid_search_chunks_clamps_bm25_minimum_match_to_query_terms(
    monkeypatch,
    query,
    expected_minimum_match,
):
    _sync_to_thread(monkeypatch)
    chunk_collection = MagicMock()
    chunk_collection.query.hybrid.return_value = SimpleNamespace(objects=[])
    connection = _connection_with_client(MagicMock())
    monkeypatch.setenv("WEAVIATE_VERSION", "1.31")

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), patch(
        "src.lib.weaviate_helpers.get_user_collections",
        return_value=(chunk_collection, MagicMock()),
    ):
        await chunks.hybrid_search_chunks(
            document_id="doc-1",
            query=query,
            user_id="user-1",
            apply_reranking=False,
            apply_mmr=False,
            strategy="lexical",
        )

    operator = chunk_collection.query.hybrid.call_args.kwargs["bm25_operator"]
    assert operator.minimum_should_match == expected_minimum_match


@pytest.mark.asyncio
async def test_hybrid_search_chunks_guardrails(monkeypatch):
    _sync_to_thread(monkeypatch)

    with pytest.raises(ValueError):
        await chunks.hybrid_search_chunks("doc-1", "query", user_id="")

    monkeypatch.setattr(chunks, "get_connection", lambda: None)
    with pytest.raises(RuntimeError, match="No Weaviate connection established"):
        await chunks.hybrid_search_chunks("doc-1", "query", user_id="user-1")


@pytest.mark.asyncio
async def test_section_keyword_and_index_helpers(monkeypatch):
    _sync_to_thread(monkeypatch)

    chunk_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    # get_chunks_by_section responses
    section_start = SimpleNamespace(
        objects=[SimpleNamespace(properties={"chunkIndex": 5, "sectionTitle": "Methods"})]
    )
    section_range = SimpleNamespace(
        objects=[
            SimpleNamespace(properties={"content": "A", "chunkIndex": 5, "sectionTitle": "Methods", "pageNumber": 2, "metadata": {}}),
            SimpleNamespace(properties={"content": "B", "chunkIndex": 6, "sectionTitle": "Methods", "pageNumber": 2, "metadata": {}}),
        ]
    )
    chunk_collection.query.fetch_objects.side_effect = [section_start, section_range]

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), patch(
        "src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())
    ):
        rows = await chunks.get_chunks_by_section("doc-1", "Methods", "user-1", max_chunks=10)
    assert len(rows) == 2
    assert rows[0]["chunk_index"] == 5

    # search_chunks_by_keyword + get_chunks_from_index
    keyword_response = SimpleNamespace(
        objects=[
            SimpleNamespace(properties={"content": "Contains ABSTRACT text", "pageNumber": 1, "chunkIndex": 0, "sectionTitle": "Abstract", "parentSection": "Abstract"}),
            SimpleNamespace(properties={"content": "No hit", "pageNumber": 2, "chunkIndex": 1, "sectionTitle": "Intro", "parentSection": "Intro"}),
        ]
    )
    index_response = SimpleNamespace(
        objects=[
            SimpleNamespace(properties={"content": "M1", "pageNumber": 2, "chunkIndex": 5, "sectionTitle": "Methods", "parentSection": "Methods"}),
            SimpleNamespace(properties={"content": "M2", "pageNumber": 2, "chunkIndex": 6, "sectionTitle": "Methods", "parentSection": "Methods"}),
            SimpleNamespace(properties={"content": "R1", "pageNumber": 3, "chunkIndex": 7, "sectionTitle": "Results", "parentSection": "Results"}),
        ]
    )
    chunk_collection.query.bm25.return_value = keyword_response
    chunk_collection.query.fetch_objects.side_effect = [index_response]

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), patch(
        "src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())
    ):
        keyword_rows = await chunks.search_chunks_by_keyword("doc-1", "abstract", "user-1", max_page=3, limit=1)
        index_rows = await chunks.get_chunks_from_index("doc-1", start_index=5, user_id="user-1", max_chunks=5)

    assert len(keyword_rows) == 1
    assert keyword_rows[0]["section_title"] == "Abstract"
    assert len(index_rows) == 2
    assert index_rows[-1]["chunk_index"] == 6


@pytest.mark.asyncio
async def test_section_listing_and_hierarchy_helpers(monkeypatch):
    _sync_to_thread(monkeypatch)

    chunk_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    section_response = SimpleNamespace(
        objects=[
            SimpleNamespace(properties={"sectionTitle": "Abstract", "pageNumber": 1, "chunkIndex": 0}),
            SimpleNamespace(properties={"sectionTitle": "Methods", "pageNumber": 2, "chunkIndex": 3}),
            SimpleNamespace(properties={"sectionTitle": "Methods", "pageNumber": 3, "chunkIndex": 4}),
        ]
    )
    hier_response = SimpleNamespace(
        objects=[
            SimpleNamespace(properties={"parentSection": "Methods", "subsection": "Fly Strains", "isTopLevel": True, "pageNumber": 2, "chunkIndex": 3}),
            SimpleNamespace(properties={"parentSection": "Methods", "subsection": "Fly Strains", "isTopLevel": True, "pageNumber": 2, "chunkIndex": 4}),
            SimpleNamespace(properties={"parentSection": "Results", "subsection": None, "isTopLevel": True, "pageNumber": 5, "chunkIndex": 8}),
        ]
    )
    chunk_collection.query.fetch_objects.side_effect = [section_response, hier_response]

    fake_sql_doc = SimpleNamespace(hierarchy_metadata={"abstract_section_title": "Abstract"})

    class _Session:
        def query(self, *_args, **_kwargs):
            return SimpleNamespace(filter=lambda *_a, **_k: SimpleNamespace(first=lambda: fake_sql_doc))

        def close(self):
            pass

    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: _Session())

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), patch(
        "src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())
    ):
        sections = await chunks.get_document_sections("doc-1", "user-1")
        hierarchical = await chunks.get_document_sections_hierarchical(
            "11111111-1111-1111-1111-111111111111",
            "user-1",
        )

    assert sections[0]["title"] == "Abstract"
    assert sections[1]["chunk_count"] == 2
    assert hierarchical["top_level_sections"] == ["Methods", "Results"]
    assert hierarchical["abstract_section_title"] == "Abstract"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, "absent"])
async def test_hierarchy_preserves_chunks_with_missing_metadata(monkeypatch, missing):
    _sync_to_thread(monkeypatch)
    collection = MagicMock()
    unknown = {"parentSection": "Methods", "subsection": "Unknown pages"}
    if missing is None:
        unknown.update(pageNumber=None, chunkIndex=None)
    collection.query.fetch_objects.return_value = SimpleNamespace(objects=[
        SimpleNamespace(properties=unknown),
        SimpleNamespace(properties={"parentSection": "Methods", "subsection": "Known", "pageNumber": 3, "chunkIndex": 4}),
        SimpleNamespace(properties={"parentSection": "Methods", "subsection": "Known", "pageNumber": None, "chunkIndex": None}),
    ])
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), patch(
        "src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())
    ):
        result = await chunks.get_document_sections_hierarchical("doc-1", "user-1")
    section = result["sections"][0]
    assert section["chunk_count"] == 3
    assert section["page_numbers"] == [3]
    assert section["subsections"] == [
        {"name": "Known", "page_numbers": [3], "chunk_count": 2},
        {"name": "Unknown pages", "page_numbers": [], "chunk_count": 1},
    ]


@pytest.mark.asyncio
async def test_parent_and_subsection_chunk_helpers(monkeypatch):
    _sync_to_thread(monkeypatch)

    chunk_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    parent_response = SimpleNamespace(
        objects=[
            SimpleNamespace(
                properties={
                    "content": "P1",
                    "chunkIndex": 10,
                    "sectionTitle": "Methods",
                    "parentSection": "Methods",
                    "subsection": "Fly Strains",
                    "isTopLevel": True,
                    "pageNumber": 4,
                    "metadata": {},
                    "docItemProvenance": '[{"id":"bbox-parent"}]',
                }
            )
        ]
    )
    subsection_response = SimpleNamespace(
        objects=[
            SimpleNamespace(
                uuid="subsection-uuid",
                properties={
                    "content": "S1",
                    "chunkIndex": 11,
                    "sectionTitle": "Methods",
                    "parentSection": "Methods",
                    "subsection": "Fly Strains",
                    "isTopLevel": False,
                    "pageNumber": 4,
                    "metadata": {},
                    "docItemProvenance": "not-json",
                }
            )
        ]
    )
    chunk_collection.query.fetch_objects.side_effect = [parent_response, subsection_response]

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), patch(
        "src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())
    ):
        parent_rows = await chunks.get_chunks_by_parent_section("doc-1", "Methods", "user-1", max_chunks=5)
        subsection_rows = await chunks.get_chunks_by_subsection(
            "doc-1",
            parent_section="Methods",
            subsection="Fly Strains",
            user_id="user-1",
            max_chunks=5,
        )

    assert len(parent_rows) == 1
    assert parent_rows[0]["doc_items"] == [{"id": "bbox-parent"}]
    assert len(subsection_rows) == 1
    assert subsection_rows[0]["doc_items"] == []
    assert subsection_rows[0]["id"] == "subsection-uuid"


@pytest.mark.asyncio
async def test_document_hierarchy_fetches_db_metadata(monkeypatch):
    _sync_to_thread(monkeypatch)

    fake_sql_doc = SimpleNamespace(hierarchy_metadata={"sections": [{"name": "Intro"}]})

    class _Session:
        def query(self, *_args, **_kwargs):
            return SimpleNamespace(filter=lambda *_a, **_k: SimpleNamespace(first=lambda: fake_sql_doc))

        def close(self):
            pass

    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: _Session())
    hierarchy = await chunks.get_document_hierarchy("11111111-1111-1111-1111-111111111111", "user-1")
    assert hierarchy == {"sections": [{"name": "Intro"}]}


_PREPARATION_QUERIES = [
    ("get_document_sections_hierarchical", {}),
    ("get_chunks_by_parent_section", {"parent_section": "Abstract"}),
    ("search_chunks_by_keyword", {"keyword": "abstract"}),
    ("get_chunks_from_index", {"start_index": 0}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name, kwargs", _PREPARATION_QUERIES)
async def test_preparation_dispatch_failure_never_calls_connection_on_loop(monkeypatch, name, kwargs):
    def forbidden():
        pytest.fail("Blocking connection must not run after dispatch failure")

    async def unavailable(*args, **kwargs):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(chunks, "get_connection", forbidden)
    monkeypatch.setattr(asyncio, "to_thread", unavailable)
    with pytest.raises(RuntimeError, match="can't start new thread"):
        await getattr(chunks, name)(document_id="doc", user_id="user", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("name, kwargs", _PREPARATION_QUERIES)
@pytest.mark.parametrize("cancel", [False, True])
async def test_cold_connection_and_query_yield_and_worker_closes(monkeypatch, name, kwargs, cancel):
    from contextvars import ContextVar
    from threading import Event, get_ident
    from src.lib.weaviate_client import connection as wc

    loop = asyncio.get_running_loop()
    entered, finished = asyncio.Event(), asyncio.Event()
    release = Event()
    owner: ContextVar[str | None] = ContextVar("owner", default=None)
    main_thread = get_ident()
    lifecycle = []
    monkeypatch.setattr(wc, "_connection", None)

    class Connection:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            assert get_ident() != main_thread
            assert owner.get() == "run-a"
            lifecycle.append("connect")
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "observer failed to release connection"

        @contextmanager
        def session(self):
            try:
                assert get_ident() != main_thread
                lifecycle.append("session")
                yield MagicMock()
            finally:
                lifecycle.append("closed")
                loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(wc, "WeaviateConnection", Connection)
    monkeypatch.setattr(chunks, "get_connection", wc.get_connection)
    collection = MagicMock()
    collection.query.fetch_objects.return_value = SimpleNamespace(objects=[])
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", lambda *args: (collection, None))
    # The hierarchy SQL session must also be created and closed on the worker.
    sql_threads = []
    sql = MagicMock()
    sql.query.return_value.filter.return_value.first.return_value = None
    def session_local():
        sql_threads.append(get_ident())
        return sql
    monkeypatch.setattr("src.models.sql.database.SessionLocal", session_local)
    token = owner.set("run-a")
    task = asyncio.create_task(getattr(chunks, name)(document_id="11111111-1111-1111-1111-111111111111", user_id="user", **kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not task.done()  # Observer progresses while synchronous connect waits.
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
        await asyncio.wait_for(finished.wait(), 5)
        # Hierarchy SQL processing follows the Weaviate session body.
        owner.reset(token)
    assert lifecycle == ["connect", "session", "closed"]
    if name == "get_document_sections_hierarchical":
        assert len(sql_threads) == 1 and sql_threads[0] != main_thread
        sql.close.assert_called_once()
    assert wc.get_connection() is wc._connection


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_fails", [False, True])
async def test_hierarchy_sql_failure_is_optional_sanitized_and_closes(monkeypatch, caplog, capture_fails):
    connection = _connection_with_client(MagicMock())
    collection = MagicMock()
    collection.query.fetch_objects.return_value = SimpleNamespace(objects=[])
    sql = MagicMock()
    sql.query.side_effect = RuntimeError("SECRET sql content")
    report = MagicMock(side_effect=RuntimeError("SECRET report") if capture_fails else None)
    monkeypatch.setattr(chunks, "get_connection", lambda: connection)
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", lambda *args: (collection, None))
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: sql)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)
    result = await chunks.get_document_sections_hierarchical("11111111-1111-1111-1111-111111111111", "user")
    assert result == {"sections": [], "top_level_sections": [], "abstract_section_title": None}
    sql.close.assert_called_once()
    report.assert_called_once()
    assert "SECRET" not in caplog.text + str(report.call_args)
@pytest.mark.asyncio
@pytest.mark.parametrize("index", [True, False, "42", -1, 1.5, None])
async def test_local_chunk_index_requires_actual_nonnegative_integer(index):
    with pytest.raises(ValueError, match="non-negative integer"):
        await chunks.get_chunk_by_index(chunk_index=index, user_id="alice", document_id="doc-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 2])
async def test_local_chunk_index_never_selects_first_duplicate(monkeypatch, count):
    from unittest.mock import AsyncMock
    collection = MagicMock()
    collection.query.fetch_objects.return_value = SimpleNamespace(objects=[
        SimpleNamespace(uuid=f"chunk-{i}", properties={"documentId": "doc-a", "chunkIndex": 42})
        for i in range(count)
    ])
    monkeypatch.setattr(chunks, "get_connection", lambda: _connection_with_client(MagicMock()))
    get_collections = MagicMock(return_value=(collection, None))
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", get_collections)
    fetch = AsyncMock(return_value={"id": "chunk-0", "chunk_index": 42, "text": "Not expressed."})
    monkeypatch.setattr(chunks, "get_chunk_by_id", fetch)
    if count == 2:
        with pytest.raises(ValueError, match="ambiguous"):
            await chunks.get_chunk_by_index(chunk_index=42, user_id="alice", document_id="doc-a")
        fetch.assert_not_called()
    else:
        result = await chunks.get_chunk_by_index(chunk_index=42, user_id="alice", document_id="doc-a")
        assert result == (fetch.return_value if count else None)
        if count:
            fetch.assert_awaited_once_with(chunk_id="chunk-0", user_id="alice", document_id="doc-a")
        else:
            fetch.assert_not_called()
    assert get_collections.call_args.args[1] == "alice"
    args = collection.query.fetch_objects.call_args.kwargs
    assert args["limit"] == 2
    assert [f.value for f in args["filters"].filters] == ["doc-a", 42]
