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


@pytest.mark.asyncio
async def test_read_subsection_tool_error_branch(monkeypatch):
    async def _boom(**_kwargs):
        raise RuntimeError("subsection failed")

    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", _boom)
    tool = weaviate_search.create_read_subsection_tool("doc-123", "user-1")
    result = await tool("Results", "Expression")
    assert "Error reading subsection" in result.summary
    assert result.subsection is None
