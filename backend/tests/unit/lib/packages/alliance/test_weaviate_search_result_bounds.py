"""ALL-1278: document retrieval pages are bounded by serialized size, not only chunk count."""

import pytest

import agr_ai_curation_alliance.tools.weaviate_search as weaviate_search  # pyright: ignore[reportMissingImports]
from agr_ai_curation_runtime.tool_result_bounds import serialized_size

SENTENCE = "Die Expression von β-Catenin 表达 wurde im Flügel nachgewiesen 😀. "


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    monkeypatch.setattr(weaviate_search, "function_tool", lambda fn=None, **kwargs: fn if fn is not None else lambda decorated: decorated)


def _chunks(count: int, *, chars: int) -> list[dict]:
    return [
        {
            "id": f"chunk-{index}",
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
async def test_long_passages_page_by_size_with_complete_continuation(monkeypatch, factory):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "12000")
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
    for page in pages:
        assert serialized_size(page) <= 12000
    first = _body(pages[0])
    assert first.page_ended_by == "size_budget"


@pytest.mark.asyncio
async def test_extreme_max_chunks_clamps_and_reports(monkeypatch):
    monkeypatch.setenv("SECTION_READ_PAGE_MAX_CHUNKS", "7")
    _patch_sections(monkeypatch, _chunks(20, chars=40))
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")

    result = await tool("Results", max_chunks=10**6)

    assert result.section.returned_chunk_count == 7
    assert result.section.requested_max_chunks == 10**6
    assert result.section.effective_max_chunks == 7
    assert result.section.max_chunks_clamped is True
    assert result.section.next_offset == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-2, 21])
async def test_invalid_section_offset_is_explicit(monkeypatch, offset):
    _patch_sections(monkeypatch, _chunks(20, chars=40))
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")

    result = await tool("Results", offset=offset)

    assert result.section is None
    assert result.error_code == "invalid_result_cursor"


@pytest.mark.asyncio
async def test_single_oversized_passage_is_withheld_with_a_read_chunk_pointer(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "6000")
    chunks = _chunks(3, chars=200)
    chunks[1]["text"] = SENTENCE * 400
    _patch_sections(monkeypatch, chunks)
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")

    ids, pages = await _read_all(tool, "Results")

    assert ids == ["chunk-0", "chunk-1", "chunk-2"]
    withheld = [
        source
        for page in pages
        for source in page.section.source_chunks
        if source.content_withheld
    ]
    assert [source.chunk_id for source in withheld] == ["chunk-1"]
    assert withheld[0].char_count == len(chunks[1]["text"])
    assert all(serialized_size(page) <= 6000 for page in pages)
    assert any("read_chunk" in page.summary and "chunk-1" in page.summary for page in pages)


@pytest.mark.asyncio
async def test_search_keeps_every_ranked_hit_within_budget(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "8000")
    chunks = _chunks(10, chars=1800)

    async def _search(**_kwargs):
        return [{**chunk, "score": 1.0 - index / 10} for index, chunk in enumerate(chunks)]

    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", _search)
    tool = weaviate_search.create_search_tool("doc-1", "user-1")

    result = await tool(query="catenin", limit=10)

    assert serialized_size(result) <= 8000
    assert [hit.chunk_id for hit in result.hits] == [chunk["id"] for chunk in chunks]
    shown = [hit for hit in result.hits if not hit.content_withheld]
    withheld = [hit for hit in result.hits if hit.content_withheld]
    assert shown and withheld
    assert shown[0].chunk_id == "chunk-0"
    texts = {chunk["id"]: chunk["text"] for chunk in chunks}
    assert all(hit.content == "" and hit.content_chars == len(texts[hit.chunk_id]) for hit in withheld)
    assert all(hit.content == texts[hit.chunk_id] for hit in shown)
    assert "read_chunk" in result.summary


@pytest.mark.asyncio
async def test_oversized_chunk_reads_in_exact_span_windows(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "6000")
    text = SENTENCE * 300

    async def _get(**_kwargs):
        return {"id": "chunk-9", "text": text, "chunk_index": 3, "page_number": 2}

    async def _neighbors(**_kwargs):
        return {"previous_chunk_id": "chunk-8", "next_chunk_id": "chunk-10"}

    monkeypatch.setattr(weaviate_search, "get_chunk_by_id", _get)
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", _neighbors)
    tool = weaviate_search.create_read_chunk_tool("doc-1", "user-1")

    windows, span_ids, offset = [], [], 0
    while True:
        result = await tool("chunk-9", span_offset=offset)
        assert result.error_code is None
        assert serialized_size(result) <= 6000
        windows.append(result.chunk.content)
        span_ids.extend(span.span_id for span in result.chunk.evidence_spans)
        page = result.chunk.span_page
        if page["complete"]:
            break
        offset = page["next_span_offset"]

    assert len(windows) > 1
    assert "".join(windows) == text
    assert len(span_ids) == len(set(span_ids)) == page["total_spans"]
