"""Unit tests for Weaviate search tools used by OpenAI agents."""

import pytest

import src.lib.openai_agents.tools.weaviate_search as weaviate_search


class _Tracker:
    def __init__(self):
        self.calls = []

    def record_call(self, name: str):
        self.calls.append(name)


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    monkeypatch.setattr(weaviate_search, "function_tool", lambda fn: fn)


@pytest.mark.asyncio
async def test_search_tool_clamps_limit_and_handles_no_hits(monkeypatch):
    captured = {}

    async def _fake_hybrid(**kwargs):
        captured.update(kwargs)
        return []

    tracker = _Tracker()
    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", _fake_hybrid)
    tool = weaviate_search.create_search_tool("doc-12345678", "user-1", tracker=tracker)

    result = await tool(query="find genes", limit=99, section_keywords=["Methods"])

    assert result.summary == "No relevant content found."
    assert result.hits == []
    assert captured["limit"] == 10
    assert captured["section_keywords"] == ["Methods"]
    assert captured["document_id"] == "doc-12345678"
    assert captured["user_id"] == "user-1"
    assert tracker.calls == ["search_document"]


@pytest.mark.asyncio
async def test_search_tool_maps_hits_and_truncates_content(monkeypatch):
    long_text = "A" * 1800

    async def _fake_hybrid(**_kwargs):
        return [
            {
                "id": "chunk-1",
                "metadata": {
                    "chunk_id": "chunk-meta-1",
                    "section_title": "Results",
                    "page_number": 5,
                    "doc_items": [{"id": "bbox-1"}],
                },
                "score": 0.91,
                "text": long_text,
            },
            {
                "id": "chunk-2",
                "metadata": {"sectionTitle": "Discussion", "pageNumber": 8},
                "content": "Short content",
                "doc_items": [{"id": "bbox-2"}],
            },
        ]

    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", _fake_hybrid)
    tool = weaviate_search.create_search_tool("doc-12345678", "user-1")

    result = await tool(query="query", limit=2)

    assert result.summary == "Found 2 chunks"
    assert result.hits[0].chunk_id == "chunk-1"
    assert result.hits[0].section_title == "Results"
    assert result.hits[0].page_number == 5
    assert result.hits[0].score == 0.91
    assert result.hits[0].content.endswith("... [truncated]")
    assert result.hits[0].doc_items == [{"id": "bbox-1"}]
    assert result.hits[1].section_title == "Discussion"
    assert result.hits[1].chunk_id == "chunk-2"
    assert result.hits[1].page_number == 8
    assert result.hits[1].doc_items == [{"id": "bbox-2"}]
    assert not hasattr(result.hits[0], "evidence_spans")


@pytest.mark.asyncio
async def test_search_tool_returns_error_summary_on_exception(monkeypatch):
    async def _boom(**_kwargs):
        raise RuntimeError("search blew up")

    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", _boom)
    tool = weaviate_search.create_search_tool("doc-123", "user-1")
    result = await tool(query="q")
    assert "Error searching document" in result.summary
    assert result.hits == []


@pytest.mark.asyncio
async def test_read_chunk_tool_returns_raw_content_neighbors_and_deterministic_spans(monkeypatch):
    text = "Alpha sentence. Beta sentence supports evidence."

    async def _fake_get_chunk_by_id(**kwargs):
        assert kwargs == {
            "chunk_id": "chunk-2",
            "user_id": "user-1",
            "document_id": "doc-12345678",
        }
        return {
            "id": "chunk-2",
            "text": text,
            "chunk_index": 1,
            "page_number": 9,
            "section_title": "Results",
            "subsection": "Expression",
            "doc_items": [{"id": "bbox-1"}],
        }

    async def _fake_get_chunk_neighbor_ids(**kwargs):
        assert kwargs == {
            "document_id": "doc-12345678",
            "user_id": "user-1",
            "chunk_index": 1,
        }
        return {
            "previous_chunk_id": "chunk-1",
            "next_chunk_id": "chunk-3",
        }

    tracker = _Tracker()
    monkeypatch.setattr(weaviate_search, "get_chunk_by_id", _fake_get_chunk_by_id)
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", _fake_get_chunk_neighbor_ids)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1", tracker=tracker)

    first = await tool("chunk-2")
    second = await tool("chunk-2")

    assert first.chunk is not None
    assert first.chunk.chunk_id == "chunk-2"
    assert first.chunk.chunk_index == 1
    assert first.chunk.chunk_number == 2
    assert first.chunk.previous_chunk_id == "chunk-1"
    assert first.chunk.next_chunk_id == "chunk-3"
    assert first.chunk.page_number == 9
    assert first.chunk.section_title == "Results"
    assert first.chunk.subsection == "Expression"
    assert first.chunk.content == text
    assert first.chunk.doc_items == [{"id": "bbox-1"}]
    assert [span.span_id for span in first.chunk.evidence_spans] == [
        span.span_id for span in second.chunk.evidence_spans
    ]
    assert [span.text for span in first.chunk.evidence_spans] == [
        "Alpha sentence.",
        "Beta sentence supports evidence.",
    ]
    assert first.chunk.evidence_spans[1].text == text[
        first.chunk.evidence_spans[1].char_start:first.chunk.evidence_spans[1].char_end
    ]
    assert first.chunk.evidence_spans[1].span_id.startswith("chunk-2:s0001:")
    assert tracker.calls == ["read_chunk", "read_chunk"]


@pytest.mark.asyncio
async def test_read_chunk_tool_parses_json_metadata_for_locator_fields(monkeypatch):
    async def _fake_get_chunk_by_id(**_kwargs):
        return {
            "id": "chunk-2",
            "text": "Exact raw chunk text.",
            "metadata": (
                '{"chunk_index": 6, "page_number": 11, '
                '"sectionTitle": "Discussion", "subsection": "Expression"}'
            ),
        }

    async def _fake_get_chunk_neighbor_ids(**kwargs):
        assert kwargs["chunk_index"] == 6
        return {
            "previous_chunk_id": "chunk-1",
            "next_chunk_id": "chunk-3",
        }

    monkeypatch.setattr(weaviate_search, "get_chunk_by_id", _fake_get_chunk_by_id)
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", _fake_get_chunk_neighbor_ids)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    result = await tool("chunk-2")

    assert result.chunk is not None
    assert result.chunk.chunk_index == 6
    assert result.chunk.chunk_number == 7
    assert result.chunk.page_number == 11
    assert result.chunk.section_title == "Discussion"
    assert result.chunk.subsection == "Expression"
    assert result.chunk.previous_chunk_id == "chunk-1"
    assert result.chunk.next_chunk_id == "chunk-3"


@pytest.mark.asyncio
async def test_read_chunk_tool_raises_when_metadata_is_not_object(monkeypatch):
    async def _fake_get_chunk_by_id(**_kwargs):
        return {
            "id": "chunk-2",
            "text": "Exact raw chunk text.",
            "metadata": ["not", "metadata"],
        }

    monkeypatch.setattr(weaviate_search, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    with pytest.raises(TypeError, match="metadata must be an object"):
        await tool("chunk-2")


@pytest.mark.asyncio
async def test_read_chunk_tool_raises_when_backend_chunk_id_missing(monkeypatch):
    async def _fake_get_chunk_by_id(**_kwargs):
        return {
            "text": "Exact raw chunk text.",
            "chunk_index": 1,
        }

    monkeypatch.setattr(weaviate_search, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    with pytest.raises(ValueError, match="returned no concrete backend chunk id"):
        await tool("chunk-2")


@pytest.mark.asyncio
async def test_read_chunk_tool_handles_missing_chunk(monkeypatch):
    async def _fake_get_chunk_by_id(**_kwargs):
        return None

    monkeypatch.setattr(weaviate_search, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    result = await tool("missing-chunk")

    assert "No chunk found" in result.summary
    assert result.chunk is None


@pytest.mark.asyncio
async def test_read_chunk_tool_raises_when_raw_text_missing(monkeypatch):
    async def _fake_get_chunk_by_id(**_kwargs):
        return {
            "id": "chunk-2",
            "content": "Alias content is not the exact read_chunk source field.",
            "chunk_index": 1,
        }

    monkeypatch.setattr(weaviate_search, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    with pytest.raises(ValueError, match="missing exact raw text content"):
        await tool("chunk-2")


@pytest.mark.asyncio
async def test_read_chunk_tool_raises_on_malformed_chunk_index(monkeypatch):
    async def _fake_get_chunk_by_id(**_kwargs):
        return {
            "id": "chunk-2",
            "text": "Exact raw chunk text.",
            "chunk_index": "not-an-index",
        }

    monkeypatch.setattr(weaviate_search, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    with pytest.raises(ValueError, match="invalid literal"):
        await tool("chunk-2")


@pytest.mark.asyncio
async def test_read_section_tool_no_content(monkeypatch):
    captured = {}

    async def _no_chunks(**_kwargs):
        captured.update(_kwargs)
        return []

    tracker = _Tracker()
    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _no_chunks)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1", tracker=tracker)
    result = await tool("Methods")

    assert "No content found for section" in result.summary
    assert result.section is None
    assert captured["document_id"] == "doc-12345678"
    assert captured["parent_section"] == "Methods"
    assert captured["user_id"] == "user-1"
    assert tracker.calls == ["read_section"]


@pytest.mark.asyncio
async def test_read_section_tool_combines_content_pages_and_doc_items(monkeypatch):
    async def _chunks(**_kwargs):
        return [
            {
                "id": "chunk-methods-1",
                "text": "Paragraph one",
                "page_number": 2,
                "section_title": "Materials and Methods",
                "subsection": "Animals",
                "metadata": '{"doc_items":[{"id":"bbox-1"}]}',
            },
            {
                "id": "chunk-methods-2",
                "content": "Paragraph two",
                "pageNumber": 3,
                "sectionTitle": "Materials and Methods",
                "metadata": {"doc_items": [{"id": "bbox-2"}]},
            },
        ]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _chunks)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")
    result = await tool("Methods")

    assert result.section is not None
    assert result.section.section_title == "Materials and Methods"
    assert result.section.page_numbers == [2, 3]
    assert result.section.chunk_count == 2
    assert result.section.content == "Paragraph one\n\nParagraph two"
    assert result.section.source_chunks is not None
    assert [source.chunk_id for source in result.section.source_chunks] == [
        "chunk-methods-1",
        "chunk-methods-2",
    ]
    assert result.section.source_chunks[0].subsection == "Animals"
    assert not hasattr(result.section.source_chunks[0], "evidence_spans")
    assert result.section.doc_items == [{"id": "bbox-1"}, {"id": "bbox-2"}]


@pytest.mark.asyncio
async def test_read_section_tool_returns_error_summary_on_exception(monkeypatch):
    async def _boom(**_kwargs):
        raise RuntimeError("section read failed")

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _boom)
    tool = weaviate_search.create_read_section_tool("doc-123", "user-1")
    result = await tool("Results")
    assert "Error reading section" in result.summary
    assert result.section is None


@pytest.mark.asyncio
async def test_read_subsection_tool_no_content_and_success(monkeypatch):
    captured = {}

    async def _no_chunks(**_kwargs):
        captured.update(_kwargs)
        return []

    tool = weaviate_search.create_read_subsection_tool("doc-123", "user-1")
    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", _no_chunks)
    empty_result = await tool("Methods", "Fly Strains")
    assert "No content found for subsection" in empty_result.summary
    assert empty_result.subsection is None
    assert captured["document_id"] == "doc-123"
    assert captured["parent_section"] == "Methods"
    assert captured["subsection"] == "Fly Strains"
    assert captured["user_id"] == "user-1"

    async def _chunks(**_kwargs):
        return [
            {"text": "Line one", "page_number": 9, "doc_items": [{"id": "bbox-1"}]},
            {"text": "Line two", "page_number": 10, "doc_items": []},
        ]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", _chunks)
    success_result = await tool("Methods", "Fly Strains")
    assert success_result.subsection is not None
    assert success_result.subsection.parent_section == "Methods"
    assert success_result.subsection.subsection == "Fly Strains"
    assert success_result.subsection.page_numbers == [9, 10]
    assert success_result.subsection.content == "Line one\n\nLine two"
    assert success_result.subsection.doc_items == [{"id": "bbox-1"}]
    assert not hasattr(success_result.subsection, "evidence_spans")


@pytest.mark.asyncio
async def test_read_subsection_tool_error_branch(monkeypatch):
    async def _boom(**_kwargs):
        raise RuntimeError("subsection failed")

    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", _boom)
    tool = weaviate_search.create_read_subsection_tool("doc-123", "user-1")
    result = await tool("Results", "Expression")
    assert "Error reading subsection" in result.summary
    assert result.subsection is None
