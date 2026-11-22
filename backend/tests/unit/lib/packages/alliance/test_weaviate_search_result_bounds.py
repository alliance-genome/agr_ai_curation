"""Document retrieval preserves complete selected sections, ranked hits and exact provenance."""

import pytest

import agr_ai_curation_alliance.tools.weaviate_search as weaviate_search  # pyright: ignore[reportMissingImports]
from tests.readable_files_support import single_file

SENTENCE = "Die Expression von β-Catenin 表达 wurde im Flügel nachgewiesen 😀. "


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(weaviate_search, "scoped_document_tool", lambda tool, **kwargs: tool)
    monkeypatch.setattr(weaviate_search, "current_readable_files", lambda: single_file("doc-12345678"))
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

    body = _body(await tool(*args))

    assert [passage.chunk_id for passage in body.passages] == [chunk["id"] for chunk in chunks]
    assert [passage.content for passage in body.passages] == [chunk["text"] for chunk in chunks]
    assert body.total_chunk_count == 40


@pytest.mark.asyncio
async def test_large_passage_is_preserved_inside_section(monkeypatch):
    chunks = _chunks(3, chars=200)
    chunks[1]["text"] = SENTENCE * 400
    _patch_sections(monkeypatch, chunks)
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")

    result = await tool("Results")

    assert [passage.content for passage in result.section.passages] == [chunk["text"] for chunk in chunks]


@pytest.mark.asyncio
async def test_section_of_about_ninety_thousand_characters_reaches_the_model_view_whole(monkeypatch):
    from src.lib.openai_agents.document_result_view import document_result_view

    chunks = _chunks(60, chars=1500)
    _patch_sections(monkeypatch, chunks)
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")

    canonical = await tool("Results")
    view, _events = document_result_view("read_section", canonical)

    assert sum(len(chunk["text"]) for chunk in chunks) >= 90_000
    passages = view["section"]["passages"]
    assert [passage["chunk_index"] for passage in passages] == list(range(60))
    for passage, chunk, source in zip(passages, chunks, canonical.section.passages):
        lines = passage["text"].split("\n")
        assert lines == [f"[{span.span_index}] {span.text}" for span in source.evidence_spans]
        # Numbered lines carry every non-space character of the chunk, in order.
        assert "".join(line.split("] ", 1)[1] for line in lines).replace(" ", "") == chunk["text"].replace(" ", "")


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
    from src.lib.openai_agents.document_result_view import document_result_view

    chunks = _chunks(12, chars=80)
    for chunk in chunks:
        chunk["doc_items"] = [
            {"page": chunk["page_number"], "bbox": [float(i), 2.0, 3.0, 4.0]}
            for i in range(25)
        ]
    _patch_sections(monkeypatch, chunks)
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")
    result = await tool("Results")
    assert [passage.doc_items for passage in result.section.passages] == [chunk["doc_items"] for chunk in chunks]
    view, events = document_result_view("read_section", result)
    assert [event["doc_items"] for event in events] == [[item for chunk in chunks for item in chunk["doc_items"]]]
    assert "doc_items" not in str(view)
