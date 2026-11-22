"""Navigation/search path tests for Weaviate chunk helpers."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.lib.weaviate_client.chunks as chunks
from src.lib.bedrock_reranker import RerankProviderError
from tests.readable_files_support import files, single_file


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
            strategy="lexical", readable=single_file("doc-1"),
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
            apply_mmr=False, readable=single_file("doc-1"),
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
                strategy="hybrid_lexical_first", readable=single_file("doc-1"),
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
            strategy="lexical", readable=single_file("doc-1"),
        )

    operator = chunk_collection.query.hybrid.call_args.kwargs["bm25_operator"]
    assert operator.minimum_should_match == expected_minimum_match


@pytest.mark.asyncio
async def test_hybrid_search_chunks_guardrails(monkeypatch):
    _sync_to_thread(monkeypatch)

    with pytest.raises(ValueError):
        await chunks.hybrid_search_chunks("doc-1", "query", user_id="", readable=single_file("doc-1"))

    monkeypatch.setattr(chunks, "get_connection", lambda: None)
    with pytest.raises(RuntimeError, match="No Weaviate connection established"):
        await chunks.hybrid_search_chunks("doc-1", "query", user_id="user-1", readable=single_file("doc-1"))


@pytest.mark.asyncio
async def test_hierarchy_helper(monkeypatch):
    _sync_to_thread(monkeypatch)

    chunk_collection = MagicMock()
    connection = _connection_with_client(MagicMock())

    hier_response = SimpleNamespace(
        objects=[
            SimpleNamespace(properties={"parentSection": "Methods", "subsection": "Fly Strains", "isTopLevel": True, "pageNumber": 2, "chunkIndex": 3}),
            SimpleNamespace(properties={"parentSection": "Methods", "subsection": "Fly Strains", "isTopLevel": True, "pageNumber": 2, "chunkIndex": 4}),
            SimpleNamespace(properties={"parentSection": "Results", "subsection": None, "isTopLevel": True, "pageNumber": 5, "chunkIndex": 8}),
        ]
    )
    chunk_collection.query.fetch_objects.return_value = hier_response

    def no_sql():
        pytest.fail("The section outline is read from the chunk store only")

    monkeypatch.setattr("src.models.sql.database.SessionLocal", no_sql)

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), patch(
        "src.lib.weaviate_helpers.get_user_collections", return_value=(chunk_collection, MagicMock())
    ):
        hierarchical = await chunks.get_document_sections_hierarchical(
            "11111111-1111-1111-1111-111111111111",
            "user-1", readable=single_file("11111111-1111-1111-1111-111111111111"),
        )

    assert set(hierarchical) == {"sections", "top_level_sections", "files"}
    assert hierarchical["top_level_sections"] == ["Methods", "Results"]
    [only] = hierarchical["files"]
    assert (only["name"], only["role"], only["chunk_count"]) == ("paper.pdf", "main", 100_000)
    assert only["sections"] == hierarchical["sections"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, "absent"])
async def test_hierarchy_preserves_chunks_with_missing_pages(monkeypatch, missing):
    _sync_to_thread(monkeypatch)
    collection = MagicMock()
    # Every listed piece has a piece number (the readable-range filter needs it); pages may be missing.
    unknown = {"parentSection": "Methods", "subsection": "Unknown pages", "chunkIndex": 9}
    if missing is None:
        unknown.update(pageNumber=None)
    collection.query.fetch_objects.return_value = SimpleNamespace(objects=[
        SimpleNamespace(properties={"parentSection": "Methods", "subsection": "Known", "pageNumber": 3, "chunkIndex": 4}),
        SimpleNamespace(properties={"parentSection": "Methods", "subsection": "Known", "pageNumber": None, "chunkIndex": 5}),
        SimpleNamespace(properties=unknown),
    ])
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), patch(
        "src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())
    ):
        result = await chunks.get_document_sections_hierarchical("doc-1", "user-1", readable=single_file("doc-1"))
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
        parent_rows = await chunks.get_chunks_by_parent_section("doc-1", "Methods", "user-1", readable=single_file("doc-1"))
        subsection_rows = await chunks.get_chunks_by_subsection(
            "doc-1",
            parent_section="Methods",
            subsection="Fly Strains",
            user_id="user-1", readable=single_file("doc-1"),
        )

    assert len(parent_rows) == 1
    assert parent_rows[0]["doc_items"] == [{"id": "bbox-parent"}]
    assert len(subsection_rows) == 1
    assert subsection_rows[0]["doc_items"] == []
    assert subsection_rows[0]["id"] == "subsection-uuid"


def _paging_store(objects):
    """A chunk collection whose fetch honours limit and offset like the real store."""
    collection = MagicMock()
    calls = []

    def fetch_objects(**kwargs):
        calls.append(kwargs)
        offset = kwargs.get("offset") or 0
        return SimpleNamespace(objects=objects[offset: offset + kwargs["limit"]])

    collection.query.fetch_objects.side_effect = fetch_objects
    return collection, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 249, 250, 251, 600])
@pytest.mark.parametrize("helper", ["parent", "subsection"])
async def test_section_helpers_page_until_every_matching_chunk_is_read(monkeypatch, count, helper):
    _sync_to_thread(monkeypatch)
    objects = [SimpleNamespace(uuid=f"uuid-{index}", properties={
        "content": f"P{index}", "chunkIndex": index, "parentSection": "Methods",
        "subsection": "Fly Strains", "pageNumber": 1,
    }) for index in range(count)]
    collection, calls = _paging_store(objects)
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())):
        if helper == "parent":
            rows = await chunks.get_chunks_by_parent_section("doc-1", "Methods", "user-1", readable=single_file("doc-1"))
        else:
            rows = await chunks.get_chunks_by_subsection("doc-1", "Methods", "Fly Strains", "user-1", readable=single_file("doc-1"))

    assert [row["chunk_index"] for row in rows] == list(range(count))
    assert [row["id"] for row in rows] == [f"uuid-{index}" for index in range(count)]
    page = chunks.get_weaviate_chunk_fetch_page_size()
    assert [call["offset"] for call in calls] == list(range(0, count + 1, page))
    assert all(call["limit"] == page for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 6, 7, 8, 20])
async def test_section_reads_page_by_the_configured_store_page_size(monkeypatch, count):
    """WEAVIATE_CHUNK_FETCH_PAGE_SIZE sets each round trip; every chunk is still read."""
    monkeypatch.setenv("WEAVIATE_CHUNK_FETCH_PAGE_SIZE", "7")
    _sync_to_thread(monkeypatch)
    objects = [SimpleNamespace(uuid=f"uuid-{index}", properties={
        "content": f"P{index}", "chunkIndex": index, "parentSection": "Methods",
        "subsection": None, "pageNumber": 1,
    }) for index in range(count)]
    collection, calls = _paging_store(objects)
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())):
        rows = await chunks.get_chunks_by_parent_section("doc-1", "Methods", "user-1", readable=single_file("doc-1"))

    assert [row["chunk_index"] for row in rows] == list(range(count))
    assert [call["offset"] for call in calls] == list(range(0, count + 1, 7))
    assert all(call["limit"] == 7 for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("helper", ["parent", "subsection"])
async def test_every_page_repeats_the_filter_and_a_total_order_sort(monkeypatch, helper):
    """Offset paging is only exact when every page uses one filter and a total order.

    Two stored objects can share a chunkIndex, so the object id breaks ties.
    """
    _sync_to_thread(monkeypatch)
    objects = [SimpleNamespace(uuid=f"uuid-{index}", properties={
        "content": f"P{index}", "chunkIndex": index, "parentSection": "Methods",
        "subsection": "Fly Strains", "pageNumber": 1,
    }) for index in range(600)]
    collection, calls = _paging_store(objects)
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())):
        if helper == "parent":
            await chunks.get_chunks_by_parent_section("doc-1", "Methods", "user-1", readable=single_file("doc-1"))
        else:
            await chunks.get_chunks_by_subsection("doc-1", "Methods", "Fly Strains", "user-1", readable=single_file("doc-1"))

    assert len(calls) == 3
    assert all(call["filters"] is calls[0]["filters"] for call in calls)
    for call in calls:
        assert [(sort.prop, sort.ascending) for sort in call["sort"].sorts] == [
            ("chunkIndex", True), ("_id", True),
        ]


@pytest.mark.asyncio
async def test_section_outline_counts_every_chunk_of_a_document_over_five_hundred(monkeypatch):
    _sync_to_thread(monkeypatch)
    objects = [SimpleNamespace(properties={
        "parentSection": "Methods" if index < 300 else "Supplementary Data",
        "subsection": None, "isTopLevel": True, "pageNumber": 1 + index // 20, "chunkIndex": index,
    }) for index in range(720)]
    collection, calls = _paging_store(objects)
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())):
        outline = await chunks.get_document_sections_hierarchical(
            "11111111-1111-1111-1111-111111111111", "user-1", readable=single_file("11111111-1111-1111-1111-111111111111"),
        )

    assert [(section["name"], section["chunk_count"]) for section in outline["sections"]] == [
        ("Methods", 300), ("Supplementary Data", 420),
    ]
    assert outline["sections"][1]["page_numbers"][-1] == 36
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_document_hierarchy_fetches_db_metadata(monkeypatch):
    _sync_to_thread(monkeypatch)

    fake_sql_doc = SimpleNamespace(main_file_or_none=SimpleNamespace(hierarchy_metadata={"sections": [{"name": "Intro"}]}))

    class _Session:
        def query(self, *_args, **_kwargs):
            return SimpleNamespace(filter=lambda *_a, **_k: SimpleNamespace(first=lambda: fake_sql_doc))

        def close(self):
            pass

    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: _Session())
    hierarchy = await chunks.get_document_hierarchy("11111111-1111-1111-1111-111111111111", "user-1")
    assert hierarchy == {"sections": [{"name": "Intro"}]}


@pytest.mark.asyncio
async def test_a_paper_without_a_main_file_has_no_document_hierarchy(monkeypatch):
    from tests.pdf_document_test_support import new_supplements_only_paper

    _sync_to_thread(monkeypatch)
    paper = new_supplements_only_paper(user_id=1, filename="supplements-only-paper")

    class _Session:
        def query(self, *_args, **_kwargs):
            return SimpleNamespace(filter=lambda *_a, **_k: SimpleNamespace(first=lambda: paper))

        def close(self):
            pass

    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: _Session())
    errors = []
    monkeypatch.setattr(chunks.logger, "error", lambda *args, **_kwargs: errors.append(args))

    assert await chunks.get_document_hierarchy("11111111-1111-1111-1111-111111111111", "user-1") is None
    # No hierarchy is the answer, not a swallowed failure.
    assert errors == []


_PREPARATION_QUERIES = [
    ("get_document_sections_hierarchical", {}),
    ("get_chunks_by_parent_section", {"parent_section": "Abstract"}),
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
        await getattr(chunks, name)(document_id="doc", user_id="user", readable=single_file("doc"), **kwargs)


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
    monkeypatch.setenv("WEAVIATE_EXPECTED_VERSION", "1.31.18")

    class Connection:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            assert get_ident() != main_thread
            assert owner.get() == "run-a"
            lifecycle.append("connect")
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "observer failed to release connection"
            client = MagicMock()
            client.get_meta.return_value = {"version": "1.31.18"}
            return client

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
    token = owner.set("run-a")
    document_id = "11111111-1111-1111-1111-111111111111"
    task = asyncio.create_task(getattr(chunks, name)(
        document_id=document_id, user_id="user", readable=single_file(document_id), **kwargs))
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
        owner.reset(token)
    assert lifecycle == ["connect", "session", "closed"]
    assert wc.get_connection() is wc._connection


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [True, False, "42", -1, 1.5, None])
async def test_local_chunk_index_requires_actual_nonnegative_integer(index):
    with pytest.raises(ValueError, match="non-negative integer"):
        await chunks.get_chunk_by_index(chunk_index=index, user_id="alice", document_id="doc-a", readable=single_file("doc-a"))


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
            await chunks.get_chunk_by_index(chunk_index=42, user_id="alice", document_id="doc-a", readable=single_file("doc-a"))
        fetch.assert_not_called()
    else:
        result = await chunks.get_chunk_by_index(chunk_index=42, user_id="alice", document_id="doc-a", readable=single_file("doc-a"))
        assert result == (fetch.return_value if count else None)
        if count:
            fetch.assert_awaited_once_with(chunk_id="chunk-0", user_id="alice", document_id="doc-a")
        else:
            fetch.assert_not_called()
    assert get_collections.call_args.args[1] == "alice"
    args = collection.query.fetch_objects.call_args.kwargs
    assert args["limit"] == 2
    assert [f.value for f in args["filters"].filters] == ["doc-a", 42]


def _ranges(filter_value):
    """(start, end) of each readable range in a readable_pieces_filter, in order."""
    from weaviate.collections.classes.filters import _FilterOr

    document, in_ranges = filter_value.filters
    assert (document.target, document.value) == ("documentId", "doc-1")
    ranges = in_ranges.filters if isinstance(in_ranges, _FilterOr) else [in_ranges]
    return [tuple(bound.value for bound in each.filters) for each in ranges]


def test_the_readable_filter_keeps_to_each_readable_files_range():
    readable = files("doc-1", ("paper.pdf", 0, 41), ("Table S1.xlsx", 42, 60), ("Data S3.docx", 70, 75))

    assert _ranges(chunks.readable_pieces_filter("doc-1", readable)) == [(0, 41), (42, 60), (70, 75)]
    assert _ranges(chunks.readable_pieces_filter("doc-1", single_file("doc-1", 42))) == [(0, 41)]


def test_no_readable_file_means_nothing_to_read_and_another_papers_value_is_refused():
    assert chunks.readable_pieces_filter("doc-1", files("doc-1")) is None
    with pytest.raises(ValueError, match="another document"):
        chunks.readable_pieces_filter("doc-1", single_file("doc-2"))
    with pytest.raises(TypeError, match="ReadableFiles"):
        chunks.readable_pieces_filter("doc-1", [(0, 41)])


@pytest.mark.asyncio
async def test_hybrid_search_searches_only_the_readable_ranges(monkeypatch):
    _sync_to_thread(monkeypatch)
    collection = MagicMock()
    collection.query.hybrid.return_value = SimpleNamespace(objects=[])
    readable = files("doc-1", ("paper.pdf", 0, 41), ("Table S1.xlsx", 50, 60))
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())):
        await chunks.hybrid_search_chunks(
            "doc-1", "a long enough query for hybrid search", user_id="user-1",
            apply_reranking=False, apply_mmr=False, readable=readable,
        )

    assert _ranges(collection.query.hybrid.call_args.kwargs["filters"]) == [(0, 41), (50, 60)]


@pytest.mark.asyncio
async def test_with_no_readable_file_reads_return_nothing_without_a_query(monkeypatch):
    monkeypatch.setattr(chunks, "get_connection", lambda: pytest.fail("no query without a readable file"))
    nothing = files("doc-1")

    assert await chunks.hybrid_search_chunks("doc-1", "query text", user_id="u", readable=nothing) == []
    assert await chunks.get_chunks_by_parent_section("doc-1", "Methods", "u", readable=nothing) == []
    assert await chunks.get_chunks_by_subsection("doc-1", "Methods", "Strains", "u", readable=nothing) == []
    assert await chunks.get_document_sections_hierarchical("doc-1", "u", readable=nothing) == {
        "sections": [], "top_level_sections": [], "files": [],
    }
    assert await chunks.get_chunks("doc-1", {"page": 1, "page_size": 5}, "u", readable=nothing) == {
        "chunks": [], "total": 0,
    }
    assert await chunks.get_chunk_by_index(chunk_index=0, user_id="u", document_id="doc-1", readable=nothing) is None
    assert await chunks.get_existing_chunk_indexes(
        chunk_indexes=[0, 1], user_id="u", document_id="doc-1", readable=nothing) == set()


@pytest.mark.asyncio
async def test_an_index_outside_every_readable_file_is_never_looked_up(monkeypatch):
    monkeypatch.setattr(chunks, "get_connection", lambda: pytest.fail("no query outside the readable ranges"))
    readable = files("doc-1", ("paper.pdf", 0, 41), ("Table S1.xlsx", 50, 60))

    for index in (42, 49, 61):
        assert await chunks.get_chunk_by_index(
            chunk_index=index, user_id="u", document_id="doc-1", readable=readable) is None
    assert await chunks.get_existing_chunk_indexes(
        chunk_indexes=[45, 61], user_id="u", document_id="doc-1", readable=readable) == set()


@pytest.mark.asyncio
async def test_existing_index_check_asks_only_about_readable_indexes(monkeypatch):
    _sync_to_thread(monkeypatch)
    collection = MagicMock()
    collection.query.fetch_objects.return_value = SimpleNamespace(objects=[
        SimpleNamespace(properties={"documentId": "doc-1", "chunkIndex": 3})])
    readable = files("doc-1", ("paper.pdf", 0, 41), ("Table S1.xlsx", 50, 60))
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())):
        found = await chunks.get_existing_chunk_indexes(
            chunk_indexes=[3, 45], user_id="u", document_id="doc-1", readable=readable)

    assert found == {3}
    asked = collection.query.fetch_objects.call_args.kwargs["filters"].filters[1]
    assert (asked.target, asked.value) == ("chunkIndex", 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("index, previous, following", [
    (0, None, 1), (41, 40, None), (50, None, 51), (60, 59, None), (45, None, None),
])
async def test_neighbors_stay_inside_the_pieces_own_file(monkeypatch, index, previous, following):
    _sync_to_thread(monkeypatch)
    asked = []

    def fetch_objects(**kwargs):
        wanted = kwargs["filters"].filters[1].value
        asked.append(wanted)
        return SimpleNamespace(objects=[SimpleNamespace(uuid=f"id-{wanted}", properties={})])

    collection = MagicMock()
    collection.query.fetch_objects.side_effect = fetch_objects
    readable = files("doc-1", ("paper.pdf", 0, 41), ("Table S1.xlsx", 50, 60))
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())):
        neighbors = await chunks.get_chunk_neighbor_ids(
            document_id="doc-1", user_id="u", chunk_index=index, readable=readable)

    assert neighbors == {
        "previous_chunk_id": None if previous is None else f"id-{previous}",
        "next_chunk_id": None if following is None else f"id-{following}",
    }
    assert asked == [value for value in (previous, following) if value is not None]



@pytest.mark.asyncio
async def test_the_outline_lists_each_readable_file_with_its_own_sections(monkeypatch):
    _sync_to_thread(monkeypatch)
    collection = MagicMock()
    collection.query.fetch_objects.return_value = SimpleNamespace(objects=[
        SimpleNamespace(properties={"parentSection": "Materials and Methods", "subsection": None,
                                    "pageNumber": 2, "chunkIndex": 0}),
        SimpleNamespace(properties={"parentSection": "Sheet1", "subsection": None, "pageNumber": None,
                                    "chunkIndex": 42}),
        SimpleNamespace(properties={"parentSection": "Sheet1", "subsection": None, "pageNumber": None,
                                    "chunkIndex": 61}),
    ])
    readable = files("doc-1", ("paper.pdf", 0, 41), ("Table S1.xlsx", 42, 60), ("Table S1.xlsx", 61, 70))
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())):
        outline = await chunks.get_document_sections_hierarchical("doc-1", "u", readable=readable)

    assert [(f["name"], f["role"], f["chunk_index_start"], f["chunk_count"]) for f in outline["files"]] == [
        ("paper.pdf", "main", 0, 42), ("Table S1.xlsx", "supplement", 42, 19), ("Table S1.xlsx (2)", "supplement", 61, 10),
    ]
    assert [[s["name"] for s in f["sections"]] for f in outline["files"]] == [
        ["Materials and Methods"], ["Sheet1"], ["Sheet1"],
    ]
    # Together, as before, for consumers of the whole outline.
    assert [(s["name"], s["chunk_count"]) for s in outline["sections"]] == [("Materials and Methods", 1), ("Sheet1", 2)]


@pytest.mark.asyncio
async def test_a_listed_piece_outside_the_readable_files_is_an_error(monkeypatch):
    _sync_to_thread(monkeypatch)
    collection = MagicMock()
    collection.query.fetch_objects.return_value = SimpleNamespace(objects=[
        SimpleNamespace(properties={"parentSection": "Results", "subsection": None, "pageNumber": 1, "chunkIndex": 99}),
    ])
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())):
        with pytest.raises(ValueError, match="outside the readable files"):
            await chunks.get_document_sections_hierarchical("doc-1", "u", readable=single_file("doc-1", 10))


@pytest.mark.asyncio
@pytest.mark.parametrize("helper", ["parent", "subsection"])
async def test_section_reads_keep_only_the_exact_stored_name(monkeypatch, helper):
    """The store's equal filter is word-based; "Methods" must not return "Materials and Methods"."""
    _sync_to_thread(monkeypatch)
    objects = [SimpleNamespace(uuid=f"uuid-{index}", properties={
        "content": f"P{index}", "chunkIndex": index, "parentSection": parent,
        "subsection": sub, "pageNumber": 1,
    }) for index, (parent, sub) in enumerate([
        ("Materials and Methods", "Fly strains"), ("Methods", "Fly strains"), ("methods", "Fly strains"),
        ("Methods", "Fly strains and genetics"),
    ])]
    collection, calls = _paging_store(objects)
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=_connection_with_client(MagicMock())), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())):
        if helper == "parent":
            rows = await chunks.get_chunks_by_parent_section("doc-1", "Methods", "u", readable=single_file("doc-1"))
        else:
            rows = await chunks.get_chunks_by_subsection(
                "doc-1", "Methods", "Fly strains", "u", readable=single_file("doc-1"))

    assert [row["chunk_index"] for row in rows] == ([1, 3] if helper == "parent" else [1])
    def leaves(filter_value):
        nested = getattr(filter_value, "filters", None)
        return [leaf for child in nested for leaf in leaves(child)] if nested else [filter_value]

    sent = leaves(calls[0]["filters"])
    assert [(f.target, f.value) for f in sent if f.target in ("parentSection", "subsection")] == (
        [("parentSection", "Methods")] if helper == "parent"
        else [("parentSection", "Methods"), ("subsection", "Fly strains")])
