"""Navigation/search path tests for Weaviate chunk helpers."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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


@pytest.mark.asyncio
async def test_hybrid_search_chunks_runs_full_path_with_mmr(monkeypatch):
    _sync_to_thread(monkeypatch)

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

    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), patch(
        "src.lib.weaviate_helpers.get_user_collections",
        return_value=(chunk_collection, MagicMock()),
    ):
        results = await chunks.hybrid_search_chunks(
            document_id="doc-1",
            query="this is a sufficiently long query for reranking",
            user_id="user-1",
            limit=2,
            initial_limit=10,
            apply_reranking=False,
            apply_mmr=True,
            section_keywords="methods",
        )

    assert len(results) == 2
    assert results[0]["id"] == "u1"
    assert "_vector" not in results[0]
    assert results[0]["metadata"]["doc_items"] == [{"id": "bbox-1"}]
    assert results[1]["metadata"]["chunk_id"] == "u2"


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
