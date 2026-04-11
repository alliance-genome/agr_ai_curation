"""Unit tests for the public package-runtime Weaviate chunk wrappers."""

from types import SimpleNamespace

import pytest

runtime_weaviate_chunks = pytest.importorskip("agr_ai_curation_runtime.weaviate_chunks")


@pytest.mark.asyncio
async def test_public_runtime_weaviate_helpers_delegate(monkeypatch):
    captured: dict[str, dict[str, object]] = {}

    async def _fake_hybrid_search_chunks(**kwargs):
        captured["hybrid_search_chunks"] = kwargs
        return [{"id": "search-hit"}]

    async def _fake_get_document_sections(**kwargs):
        captured["get_document_sections"] = kwargs
        return [{"title": "Methods"}]

    async def _fake_get_chunks_by_parent_section(**kwargs):
        captured["get_chunks_by_parent_section"] = kwargs
        return [{"id": "parent-hit"}]

    async def _fake_get_chunks_by_subsection(**kwargs):
        captured["get_chunks_by_subsection"] = kwargs
        return [{"id": "subsection-hit"}]

    monkeypatch.setattr(
        runtime_weaviate_chunks,
        "_load_chunks_module",
        lambda: SimpleNamespace(
            hybrid_search_chunks=_fake_hybrid_search_chunks,
            get_document_sections=_fake_get_document_sections,
            get_chunks_by_parent_section=_fake_get_chunks_by_parent_section,
            get_chunks_by_subsection=_fake_get_chunks_by_subsection,
        ),
    )

    assert await runtime_weaviate_chunks.hybrid_search_chunks(
        document_id="doc-1",
        query="wg",
        user_id="user-1",
    ) == [{"id": "search-hit"}]
    assert await runtime_weaviate_chunks.get_document_sections(
        document_id="doc-1",
        user_id="user-1",
    ) == [{"title": "Methods"}]
    assert await runtime_weaviate_chunks.get_chunks_by_parent_section(
        document_id="doc-1",
        parent_section="Methods",
        user_id="user-1",
    ) == [{"id": "parent-hit"}]
    assert await runtime_weaviate_chunks.get_chunks_by_subsection(
        document_id="doc-1",
        parent_section="Methods",
        subsection="Fly Strains",
        user_id="user-1",
    ) == [{"id": "subsection-hit"}]

    assert captured == {
        "hybrid_search_chunks": {
            "document_id": "doc-1",
            "query": "wg",
            "user_id": "user-1",
        },
        "get_document_sections": {
            "document_id": "doc-1",
            "user_id": "user-1",
        },
        "get_chunks_by_parent_section": {
            "document_id": "doc-1",
            "parent_section": "Methods",
            "user_id": "user-1",
        },
        "get_chunks_by_subsection": {
            "document_id": "doc-1",
            "parent_section": "Methods",
            "subsection": "Fly Strains",
            "user_id": "user-1",
        },
    }
