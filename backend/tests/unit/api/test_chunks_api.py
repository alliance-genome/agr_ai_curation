"""Unit tests for chunks API endpoint handler."""

import logging

import pytest
from fastapi import HTTPException

from src.api import chunks as chunks_api
from src.lib.document_files.readable import ReadableFilesError, ReadingScope, current_reading_scope
from tests.readable_files_support import single_file

DOC = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def readable(monkeypatch):
    """The paper's ready files, resolved for the endpoint's own user."""
    resolved = []

    def resolve(document_id, user_id):
        resolved.append((document_id, user_id, current_reading_scope()))
        return single_file(document_id)

    monkeypatch.setattr(chunks_api, "resolve_readable_files", resolve)
    return resolved


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
            document_id=DOC,
            page=1,
            page_size=20,
            include_metadata=True,
            user=None,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_document_chunks_returns_404_when_no_chunks(monkeypatch):
    async def _no_chunks(_document_id, _pagination, _user_id, *, readable):
        return {"total": 0, "chunks": []}

    monkeypatch.setattr(chunks_api, "get_chunks", _no_chunks)

    with pytest.raises(HTTPException) as exc:
        await chunks_api.get_document_chunks_endpoint(
            document_id=DOC,
            page=1,
            page_size=20,
            include_metadata=True,
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_document_chunks_success(monkeypatch):
    async def _get_chunks(document_id, pagination, user_id, *, readable):
        assert readable == single_file(DOC)
        assert document_id == DOC
        assert pagination == {"page": 2, "page_size": 1, "include_metadata": False}
        assert user_id == "user-1"
        return {"total": 2, "chunks": [_chunk(document_id, idx=1)]}

    monkeypatch.setattr(chunks_api, "get_chunks", _get_chunks)

    response = await chunks_api.get_document_chunks_endpoint(
        document_id=DOC,
        page=2,
        page_size=1,
        include_metadata=False,
        user={"sub": "user-1"},
    )
    assert response.document_id == DOC
    assert response.pagination.current_page == 2
    assert response.pagination.total_pages == 2
    assert response.pagination.total_items == 2
    assert len(response.chunks) == 1
    assert response.chunks[0].chunk_index == 1


@pytest.mark.asyncio
async def test_get_document_chunks_accepts_minimal_metadata_when_include_metadata_false(monkeypatch):
    async def _get_chunks(document_id, pagination, user_id, *, readable):
        assert readable == single_file(DOC)
        assert document_id == DOC
        assert pagination == {"page": 1, "page_size": 20, "include_metadata": False}
        assert user_id == "user-1"
        return {
            "total": 1,
            "chunks": [
                {
                    "id": "chunk-1",
                    "document_id": document_id,
                    "chunk_index": 0,
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
                    },
                }
            ],
        }

    monkeypatch.setattr(chunks_api, "get_chunks", _get_chunks)

    response = await chunks_api.get_document_chunks_endpoint(
        document_id=DOC,
        page=1,
        page_size=20,
        include_metadata=False,
        user={"sub": "user-1"},
    )

    assert response.document_id == DOC
    assert len(response.chunks) == 1
    assert response.chunks[0].metadata.character_count == 21
    assert response.chunks[0].metadata.word_count == 3


@pytest.mark.asyncio
async def test_get_document_chunks_maps_unexpected_errors_to_500(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=chunks_api.logger.name)

    async def _boom(_document_id, _pagination, _user_id, *, readable):
        raise RuntimeError("downstream exploded")

    monkeypatch.setattr(chunks_api, "get_chunks", _boom)

    with pytest.raises(HTTPException) as exc:
        await chunks_api.get_document_chunks_endpoint(
            document_id=DOC,
            page=1,
            page_size=20,
            include_metadata=True,
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to retrieve chunks"
    assert "downstream exploded" not in str(exc.value.detail)
    assert "downstream exploded" in caplog.text


@pytest.mark.asyncio
async def test_get_document_chunks_lists_only_the_ready_files(monkeypatch, readable):
    captured = {}

    async def _get_chunks(document_id, pagination, user_id, *, readable):
        captured["readable"] = readable
        return {"total": 1, "chunks": [_chunk(document_id, idx=0)]}

    monkeypatch.setattr(chunks_api, "get_chunks", _get_chunks)
    await chunks_api.get_document_chunks_endpoint(
        document_id=DOC, page=1, page_size=20, include_metadata=True, user={"sub": "user-1"},
    )

    assert readable == [(DOC, "user-1", ReadingScope.ALL_READY_FILES)]
    assert captured["readable"] == single_file(DOC)


@pytest.mark.asyncio
@pytest.mark.parametrize("document_id", ["not-a-uuid", DOC])
async def test_get_document_chunks_is_404_for_a_paper_that_is_not_the_users(monkeypatch, document_id):
    def not_theirs(_document_id, _user_id):
        raise ReadableFilesError("not a paper of this user")

    monkeypatch.setattr(chunks_api, "resolve_readable_files", not_theirs)
    monkeypatch.setattr(chunks_api, "get_chunks", lambda *a, **k: pytest.fail("no chunk read"))

    with pytest.raises(HTTPException) as exc:
        await chunks_api.get_document_chunks_endpoint(
            document_id=document_id, page=1, page_size=20, include_metadata=True, user={"sub": "user-1"},
        )
    assert exc.value.status_code == 404
