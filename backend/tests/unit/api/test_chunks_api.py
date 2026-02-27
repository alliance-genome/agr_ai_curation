"""Unit tests for chunks API endpoint handler."""

import pytest
from fastapi import HTTPException

from src.api import chunks as chunks_api


def _chunk(document_id: str, idx: int = 0):
    return {
        "id": f"chunk-{idx}",
        "document_id": document_id,
        "chunk_index": idx,
        "content": "Example chunk content",
        "element_type": "NarrativeText",
        "page_number": 1,
        "section_title": None,
        "section_path": None,
        "parent_section": None,
        "subsection": None,
        "is_top_level": None,
        "doc_items": [],
        "metadata": {
            "character_count": 21,
            "word_count": 3,
            "has_table": False,
            "has_image": False,
            "chunking_strategy": "test",
            "section_path": None,
            "content_type": "text",
            "doc_items": [],
        },
    }


@pytest.mark.asyncio
async def test_get_document_chunks_requires_authenticated_user():
    with pytest.raises(HTTPException) as exc:
        await chunks_api.get_document_chunks_endpoint(
            document_id="doc-1",
            page=1,
            page_size=20,
            include_metadata=True,
            user=None,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_document_chunks_returns_404_when_no_chunks(monkeypatch):
    async def _no_chunks(_document_id, _pagination, _user_id):
        return {"total": 0, "chunks": []}

    monkeypatch.setattr(chunks_api, "get_chunks", _no_chunks)

    with pytest.raises(HTTPException) as exc:
        await chunks_api.get_document_chunks_endpoint(
            document_id="doc-1",
            page=1,
            page_size=20,
            include_metadata=True,
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_document_chunks_success(monkeypatch):
    async def _get_chunks(document_id, pagination, user_id):
        assert document_id == "doc-1"
        assert pagination == {"page": 2, "page_size": 1, "include_metadata": False}
        assert user_id == "user-1"
        return {"total": 2, "chunks": [_chunk(document_id, idx=1)]}

    monkeypatch.setattr(chunks_api, "get_chunks", _get_chunks)

    response = await chunks_api.get_document_chunks_endpoint(
        document_id="doc-1",
        page=2,
        page_size=1,
        include_metadata=False,
        user={"sub": "user-1"},
    )
    assert response.document_id == "doc-1"
    assert response.pagination.current_page == 2
    assert response.pagination.total_pages == 2
    assert response.pagination.total_items == 2
    assert len(response.chunks) == 1
    assert response.chunks[0].chunk_index == 1


@pytest.mark.asyncio
async def test_get_document_chunks_maps_unexpected_errors_to_500(monkeypatch):
    async def _boom(_document_id, _pagination, _user_id):
        raise RuntimeError("downstream exploded")

    monkeypatch.setattr(chunks_api, "get_chunks", _boom)

    with pytest.raises(HTTPException) as exc:
        await chunks_api.get_document_chunks_endpoint(
            document_id="doc-1",
            page=1,
            page_size=20,
            include_metadata=True,
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 500
    assert "downstream exploded" in exc.value.detail
