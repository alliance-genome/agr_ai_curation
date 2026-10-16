"""Document retrieval preserves complete selected sections, ranked hits and exact provenance."""

import pytest

import agr_ai_curation_alliance.tools.weaviate_search as weaviate_search  # pyright: ignore[reportMissingImports]

SENTENCE = "Die Expression von β-Catenin 表达 wurde im Flügel nachgewiesen 😀. "


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(weaviate_search, "scoped_document_tool", lambda tool, **kwargs: tool)
    monkeypatch.setattr(weaviate_search, "retain_issued_spans", AsyncMock())
    monkeypatch.setattr(weaviate_search, "function_tool", lambda fn=None, **kwargs: fn if fn is not None else lambda decorated: decorated)


def _chunks(count: int, *, chars: int) -> list[dict]:
    return [
        {
            "id": f"chunk-{index}",
            "chunk_index": index,
            "text": f"[{index}] " + (SENTENCE * (chars // len(SENTENCE) + 1))[:chars],
            "page_number": index + 1,
            "section_title": "Results",
            "doc_items": [{"page": index + 1, "bbox": [1.0, 2.0, 3.0, 4.0]}],
        }
        for index in range(count)
    ]


def _patch_sections(monkeypatch, chunks):
    async def _section(**_kwargs):
        return chunks

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _section)
    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", _section)


def _body(result):
    return getattr(result, "section", None) or getattr(result, "subsection", None)


async def _read_all(tool, *args, **kwargs):
    ids, pages, offset = [], [], 0
    while True:
        result = await tool(*args, offset=offset, **kwargs)
        body = _body(result)
        pages.append(result)
        ids.extend(source.chunk_id for source in body.source_chunks or [])
        if body.next_offset is None:
            return ids, pages
        assert body.next_offset > offset
        offset = body.next_offset


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", ["section", "subsection"])
async def test_long_passages_return_whole_requested_section(monkeypatch, factory):
    chunks = _chunks(40, chars=1500)
    _patch_sections(monkeypatch, chunks)
    if factory == "section":
        tool = weaviate_search.create_read_section_tool("doc-1", "user-1")
        args = ("Results",)
    else:
        tool = weaviate_search.create_read_subsection_tool("doc-1", "user-1")
        args = ("Results", "Expression")

    ids, pages = await _read_all(tool, *args, max_chunks=100)

    assert ids == [chunk["id"] for chunk in chunks]
    joined = "\n\n".join(
        _body(page).content for page in pages
    )
    for chunk in chunks:
        assert chunk["text"] in joined
    assert len(pages) == 1
    first = _body(pages[0])
    assert first.page_ended_by == "end"


@pytest.mark.asyncio
async def test_explicit_max_chunks_has_no_context_ceiling(monkeypatch):
    _patch_sections(monkeypatch, _chunks(20, chars=40))
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")

    result = await tool("Results", max_chunks=10**6)

    assert result.section.returned_chunk_count == 20
    assert result.section.requested_max_chunks == 10**6
    assert result.section.effective_max_chunks == 10**6
    assert result.section.next_offset is None


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-2, 21])
async def test_invalid_section_offset_is_explicit(monkeypatch, offset):
    _patch_sections(monkeypatch, _chunks(20, chars=40))
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")

    result = await tool("Results", offset=offset)

    assert result.section is None
    assert result.error_code == "invalid_result_cursor"


@pytest.mark.asyncio
async def test_large_passage_is_preserved_inside_section(monkeypatch):
    chunks = _chunks(3, chars=200)
    chunks[1]["text"] = SENTENCE * 400
    _patch_sections(monkeypatch, chunks)
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")

    ids, pages = await _read_all(tool, "Results")

    assert ids == ["chunk-0", "chunk-1", "chunk-2"]
    assert len(pages) == 1
    assert all(chunk["text"] in pages[0].section.content for chunk in chunks)


@pytest.mark.asyncio
async def test_search_keeps_every_ranked_hit_with_full_content(monkeypatch):
    chunks = _chunks(10, chars=1800)

    async def _search(**_kwargs):
        return [{**chunk, "score": 1.0 - index / 10} for index, chunk in enumerate(chunks)]

    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", _search)
    tool = weaviate_search.create_search_tool("doc-1", "user-1")

    result = await tool(query="catenin", limit=10)

    assert [hit.chunk_index for hit in result.hits] == list(range(10))
    assert [hit.content for hit in result.hits] == [chunk["text"] for chunk in chunks]


@pytest.mark.asyncio
async def test_large_chunk_reads_complete_text_and_spans(monkeypatch):
    text = SENTENCE * 300

    async def _get(**_kwargs):
        return {"id": "chunk-9", "text": text, "chunk_index": 3, "page_number": 2}

    async def _neighbors(**_kwargs):
        return {"previous_chunk_id": "chunk-8", "next_chunk_id": "chunk-10"}

    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", _get)
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", _neighbors)
    tool = weaviate_search.create_read_chunk_tool("doc-1", "user-1")

    windows, span_ids, offset = [], [], 0
    while True:
        result = await tool(3, span_offset=offset)
        assert result.error_code is None
        windows.append(result.chunk.content)
        span_ids.extend(span.span_id for span in result.chunk.evidence_spans)
        page = result.chunk.span_page
        if page is None or page["complete"]:
            break
        offset = page["next_span_offset"]

    assert len(windows) == 1
    assert "".join(windows) == text
    assert len(span_ids) == len(set(span_ids))


@pytest.mark.asyncio
async def test_pdf_provenance_stays_exact_for_entire_section(monkeypatch):
    chunks = _chunks(12, chars=80)
    for chunk in chunks:
        chunk["doc_items"] = [
            {"page": chunk["page_number"], "bbox": [float(i), 2.0, 3.0, 4.0]}
            for i in range(25)
        ]
    _patch_sections(monkeypatch, chunks)
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")
    ids, pages = await _read_all(tool, "Results", max_chunks=10**6)
    assert ids == [chunk["id"] for chunk in chunks]
    boxes = [item for page in pages for item in page.section.doc_items]
    assert boxes == [item for chunk in chunks for item in chunk["doc_items"]]
    assert len(pages) == 1
