"""Unit tests for the Alliance package's canonical Weaviate search tools."""

import json
from unittest.mock import AsyncMock

import pytest

import agr_ai_curation_alliance.tools.weaviate_search as weaviate_search  # pyright: ignore[reportMissingImports]
from src.lib.document_sources.figure_metadata import (
    PROVIDER_FIGURE_METADATA_SECTION,
    PROVIDER_FIGURE_SUBSECTION_PREFIX,
)
from src.lib.packages.package_runner_entrypoint import _build_tool_context
from tests.readable_files_support import files, single_file

# What the source binding of each tool call may read; reset for every test.
_READABLE: dict = {}
# Stored section names per file label, for the not-found answers.
_OUTLINE: dict = {}


class _Tracker:
    def __init__(self):
        self.calls = []

    def record_call(self, name: str):
        self.calls.append(name)


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    def _identity_function_tool(fn=None, **_kwargs):
        if fn is not None:
            return fn
        return lambda wrapped: wrapped

    monkeypatch.setattr(weaviate_search, "function_tool", _identity_function_tool)
    # Formatting/selection tests isolate the source scope; SDK lifecycle and
    # durable source binding are covered in test_document_references.py.
    monkeypatch.setattr(weaviate_search, "scoped_document_tool", lambda tool, **kwargs: tool)
    monkeypatch.setattr(weaviate_search, "retain_issued_spans", AsyncMock())
    _READABLE["value"] = single_file("doc-12345678")
    _OUTLINE.clear()
    monkeypatch.setattr(weaviate_search, "current_readable_files", lambda: _READABLE["value"])

    async def outline(document_id, user_id, *, readable):
        # The stored section names of each readable file (none unless a test sets them).
        return {"files": [{"name": label, "sections": _OUTLINE.get(label, [])}
                          for label, _file in readable.labelled()]}

    monkeypatch.setattr(weaviate_search, "get_document_sections_hierarchical", outline)


def test_search_tool_schema_exposes_search_mode_enum(monkeypatch):
    from agents import function_tool  # pyright: ignore[reportMissingImports]

    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)

    tool = weaviate_search.create_search_tool("doc-12345678", "user-1")
    search_mode_schema = tool.params_json_schema["properties"]["search_mode"]

    assert search_mode_schema["default"] == "auto"
    assert search_mode_schema["enum"] == [
        "auto",
        "hybrid",
        "lexical",
        "hybrid_lexical_first",
    ]


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
    assert captured["strategy"] == "hybrid"
    assert captured["initial_limit"] == weaviate_search._SEARCH_INITIAL_LIMIT
    assert captured["alpha"] == weaviate_search._SEARCH_HYBRID_ALPHA
    assert captured["apply_mmr"] is weaviate_search._SEARCH_MMR_ENABLED
    assert captured["mmr_lambda"] == weaviate_search._SEARCH_MMR_LAMBDA
    assert tracker.calls == ["search_document"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected_strategy"),
    [
        ({}, "hybrid"),
        ({"search_mode": "auto"}, "hybrid"),
        ({"search_mode": "hybrid"}, "hybrid"),
        ({"search_mode": "lexical"}, "lexical"),
        ({"search_mode": "hybrid_lexical_first"}, "hybrid_lexical_first"),
    ],
)
async def test_search_tool_forwards_search_mode_to_strategy(
    monkeypatch, kwargs, expected_strategy
):
    captured = {}

    async def _fake_hybrid(**hybrid_kwargs):
        captured.update(hybrid_kwargs)
        return []

    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", _fake_hybrid)
    tool = weaviate_search.create_search_tool("doc-12345678", "user-1")

    result = await tool(query="wg", **kwargs)

    assert result.summary == "No relevant content found."
    assert captured["strategy"] == expected_strategy


@pytest.mark.asyncio
async def test_search_tool_rejects_invalid_search_mode(monkeypatch):
    async def _fake_hybrid(**_kwargs):
        raise AssertionError("invalid search_mode must not call Weaviate")

    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", _fake_hybrid)
    tool = weaviate_search.create_search_tool("doc-12345678", "user-1")

    result = await tool(query="wg", search_mode="semantic")

    assert "Unsupported search_mode 'semantic'" in result.summary
    assert "auto, hybrid, lexical, hybrid_lexical_first" in result.summary
    assert result.hits == []


@pytest.mark.asyncio
async def test_search_tool_maps_hits_and_returns_full_content(monkeypatch):
    long_text = "A" * 1800

    async def _fake_hybrid(**_kwargs):
        return [
            {
                "chunk_index": 0, "id": "chunk-1",
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
                "chunk_index": 1, "id": "chunk-2",
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
    assert result.hits[0].content == long_text
    assert result.hits[0].doc_items == [{"id": "bbox-1"}]
    assert result.hits[1].section_title == "Discussion"
    assert result.hits[1].chunk_id == "chunk-2"
    assert result.hits[1].page_number == 8
    assert result.hits[1].doc_items == [{"id": "bbox-2"}]
    assert not hasattr(result.hits[0], "evidence_spans")


@pytest.mark.asyncio
async def test_search_tool_propagates_dependency_exception(monkeypatch):
    from agents import function_tool  # pyright: ignore[reportMissingImports]

    async def _boom(**_kwargs):
        raise RuntimeError("search blew up")

    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", _boom)
    tool = weaviate_search.create_search_tool("doc-12345678", "user-1")
    payload = {"query": "q"}

    with pytest.raises(RuntimeError, match="required PDF") as caught:
        await tool.on_invoke_tool(
            _build_tool_context(tool.name, payload), json.dumps(payload)
        )
    assert str(caught.value.__cause__) == "search blew up"


@pytest.mark.asyncio
async def test_read_chunk_tool_returns_raw_content_neighbors_and_deterministic_spans(monkeypatch):
    text = "Alpha sentence. Beta sentence supports evidence."

    async def _fake_get_chunk_by_index(**kwargs):
        assert kwargs == {
            "chunk_index": 1,
            "user_id": "user-1",
            "document_id": "doc-12345678",
            "readable": _READABLE["value"],
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
            "readable": _READABLE["value"],
        }
        return {
            "previous_chunk_id": "chunk-1",
            "next_chunk_id": "chunk-3",
        }

    tracker = _Tracker()
    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", _fake_get_chunk_by_index)
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", _fake_get_chunk_neighbor_ids)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1", tracker=tracker)

    first = await tool(1)
    second = await tool(1)

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
    async def _fake_get_chunk_by_index(**_kwargs):
        return {
            "chunk_index": 6, "id": "chunk-2",
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

    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", _fake_get_chunk_by_index)
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", _fake_get_chunk_neighbor_ids)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    result = await tool(6)

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
    async def _fake_get_chunk_by_index(**_kwargs):
        return {
            "chunk_index": 1, "id": "chunk-2",
            "text": "Exact raw chunk text.",
            "metadata": ["not", "metadata"],
        }

    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", _fake_get_chunk_by_index)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    with pytest.raises(TypeError, match="metadata must be an object"):
        await tool(1)


@pytest.mark.asyncio
async def test_read_chunk_tool_raises_when_backend_chunk_id_missing(monkeypatch):
    async def _fake_get_chunk_by_index(**_kwargs):
        return {
            "text": "Exact raw chunk text.",
            "chunk_index": 1,
        }

    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", _fake_get_chunk_by_index)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    with pytest.raises(ValueError, match="returned no concrete backend chunk id"):
        await tool(1)


def _missing_chunk_store(monkeypatch, *, chunk_count):
    """A store that finds no chunk at the requested index, for a one-file paper of
    ``chunk_count`` pieces (no readable file when it has none). Returns the lookups made."""
    looked_up = []

    async def _fake_get_chunk_by_index(**kwargs):
        looked_up.append(kwargs["chunk_index"])
        return None

    async def _no_neighbors(**_kwargs):
        raise AssertionError("a missing chunk must not be navigated")

    _READABLE["value"] = single_file("doc-12345678", chunk_count) if chunk_count else files("doc-12345678")
    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", _fake_get_chunk_by_index)
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", _no_neighbors)
    return looked_up


@pytest.mark.asyncio
async def test_read_chunk_past_the_end_returns_the_valid_range_to_the_model(monkeypatch):
    """The November 6 case: a 349-chunk document (indexes 0 to 348) and read_chunk(349)."""
    looked_up = _missing_chunk_store(monkeypatch, chunk_count=349)
    retain = AsyncMock()
    monkeypatch.setattr(weaviate_search, "retain_issued_spans", retain)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    result = await tool(349)

    assert result.summary == (
        "Invalid read_chunk request: chunk_index 349 does not exist; "
        "this document has chunks 0 to 348."
    )
    assert result.chunk is None
    assert result.error_code == "invalid_result_cursor"
    assert looked_up == []  # outside the readable range: no store lookup
    retain.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_chunk_past_the_end_reaches_the_model_as_a_request_error(monkeypatch):
    from src.lib.agent_studio import catalog_service

    _missing_chunk_store(monkeypatch, chunk_count=349)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    result = await tool(400)

    assert catalog_service._document_model_result("read_chunk", result.model_dump(mode="json")) == {
        "summary": (
            "Invalid read_chunk request: chunk_index 400 does not exist; "
            "this document has chunks 0 to 348."
        ),
        "error_code": "invalid_result_cursor",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("chunk_index", "chunk_count"), [(100, 349), (348, 349), (0, 0), (5, 0)])
async def test_read_chunk_missing_chunk_inside_the_document_still_fails(
    monkeypatch, chunk_index, chunk_count
):
    """An index the document should hold, or any read of a document with no stored chunks,
    is a failed source read and still stops the run."""
    _missing_chunk_store(monkeypatch, chunk_count=chunk_count)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    from agr_ai_curation_runtime.document_errors import DocumentReadError
    with pytest.raises(DocumentReadError):
        await tool(chunk_index)


@pytest.mark.asyncio
async def test_read_chunk_without_its_readable_files_still_fails(monkeypatch):
    from agents import function_tool  # pyright: ignore[reportMissingImports]

    _missing_chunk_store(monkeypatch, chunk_count=0)

    def _no_binding():
        raise RuntimeError("readable files unavailable")

    monkeypatch.setattr(weaviate_search, "current_readable_files", _no_binding)
    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")
    payload = {"chunk_index": 349}

    with pytest.raises(RuntimeError, match="required PDF") as caught:
        await tool.on_invoke_tool(
            _build_tool_context(tool.name, payload), json.dumps(payload)
        )
    assert str(caught.value.__cause__) == "readable files unavailable"


def _sdk_read_chunk_tool(monkeypatch):
    """The read_chunk tool as the model calls it: the real function_tool wrapper."""
    from agr_ai_curation_runtime.function_tools import function_tool

    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    return weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")


async def _invoke(tool, payload):
    return await tool.on_invoke_tool(_build_tool_context(tool.name, payload), json.dumps(payload))


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_index", [-1, -349, 349, 1000])
async def test_read_chunk_index_outside_the_document_reaches_the_model_through_the_sdk_tool(
    monkeypatch, chunk_index
):
    """A negative index and an index past the end both name the valid range; neither ends the run."""
    _missing_chunk_store(monkeypatch, chunk_count=349)

    async def _no_lookup_for_negative(**kwargs):
        assert kwargs["chunk_index"] >= 0, "a negative index must not reach the store lookup"
        return None

    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", _no_lookup_for_negative)
    tool = _sdk_read_chunk_tool(monkeypatch)

    result = await _invoke(tool, {"chunk_index": chunk_index})

    assert result.summary == (
        f"Invalid read_chunk request: chunk_index {chunk_index} does not exist; "
        "this document has chunks 0 to 348."
    )
    assert result.chunk is None
    assert result.error_code == "invalid_result_cursor"


@pytest.mark.asyncio
async def test_read_chunk_negative_index_in_a_document_with_no_chunks_still_fails(monkeypatch):
    _missing_chunk_store(monkeypatch, chunk_count=0)
    tool = _sdk_read_chunk_tool(monkeypatch)

    with pytest.raises(RuntimeError, match="required PDF"):
        await _invoke(tool, {"chunk_index": -1})


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"chunk_index": "3"}, {"chunk_index": 1.5}, {"chunk_index": None}])
async def test_read_chunk_other_invalid_chunk_index_arguments_still_end_the_read(monkeypatch, payload):
    """Only the integer range changed: a missing or non-integer chunk_index fails as before."""
    _missing_chunk_store(monkeypatch, chunk_count=349)
    tool = _sdk_read_chunk_tool(monkeypatch)

    with pytest.raises(RuntimeError, match="required PDF") as caught:
        await _invoke(tool, payload)
    assert "Invalid JSON input for tool read_chunk" in str(caught.value.__cause__)


def test_read_chunk_schema_keeps_chunk_index_a_required_strict_integer(monkeypatch):
    tool = _sdk_read_chunk_tool(monkeypatch)

    schema = tool.params_json_schema
    assert schema["required"] == ["chunk_index", "span_offset"]
    assert schema["properties"]["chunk_index"] == {
        "description": "The passage number, as shown by search_document, read_section or read_subsection.",
        "title": "Chunk Index",
        "type": "integer",
    }


@pytest.mark.asyncio
async def test_read_chunk_reads_and_navigates_within_the_readable_files(monkeypatch):
    seen = {}

    async def _fake_get_chunk_by_index(**kwargs):
        seen["read"] = kwargs["readable"]
        return {"id": "chunk-1", "text": "Exact raw chunk text.", "chunk_index": 12}

    async def _fake_get_chunk_neighbor_ids(**kwargs):
        seen["neighbors"] = kwargs["readable"]
        return {"previous_chunk_id": None, "next_chunk_id": None}

    _READABLE["value"] = files("doc-12345678", ("paper.pdf", 0, 11), ("Table S1.xlsx", 12, 13))
    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", _fake_get_chunk_by_index)
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", _fake_get_chunk_neighbor_ids)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    result = await tool(12)

    assert result.chunk is not None
    assert result.error_code is None
    assert seen == {"read": _READABLE["value"], "neighbors": _READABLE["value"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_index", [14, 19, 22])
async def test_read_chunk_in_a_gap_or_past_the_last_file_names_each_readable_file(monkeypatch, chunk_index):
    looked_up = _missing_chunk_store(monkeypatch, chunk_count=12)
    _READABLE["value"] = files(
        "doc-12345678", ("paper.pdf", 0, 11), ("Table S1.xlsx", 12, 13), ("Data S2.docx", 20, 21))
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    result = await tool(chunk_index)

    assert result.summary == (
        f"Invalid read_chunk request: chunk_index {chunk_index} does not exist; this document has chunks "
        "0 to 11 (paper.pdf), 12 to 13 (Table S1.xlsx), 20 to 21 (Data S2.docx)."
    )
    assert result.error_code == "invalid_result_cursor"
    assert looked_up == []


@pytest.mark.asyncio
async def test_search_and_section_reads_pass_the_readable_files(monkeypatch):
    seen = []

    async def _capture(**kwargs):
        seen.append(kwargs["readable"])
        return []

    _READABLE["value"] = files("doc-12345678", ("paper.pdf", 0, 11), ("Table S1.xlsx", 12, 13))
    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", _capture)
    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _capture)
    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", _capture)

    await weaviate_search.create_search_tool("doc-12345678", "user-1")(query="wing disc expression")
    await weaviate_search.create_read_section_tool("doc-12345678", "user-1")(section_name="Results")
    await weaviate_search.create_read_subsection_tool("doc-12345678", "user-1")(
        parent_section="Results", subsection="Expression")

    assert seen == [_READABLE["value"]] * 3


@pytest.mark.asyncio
async def test_read_chunk_tool_raises_when_raw_text_missing(monkeypatch):
    async def _fake_get_chunk_by_index(**_kwargs):
        return {
            "id": "chunk-2",
            "content": "Alias content is not the exact read_chunk source field.",
            "chunk_index": 1,
        }

    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", _fake_get_chunk_by_index)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    with pytest.raises(ValueError, match="missing exact raw text content"):
        await tool(1)


@pytest.mark.asyncio
async def test_read_chunk_tool_raises_on_malformed_chunk_index(monkeypatch):
    async def _fake_get_chunk_by_index(**_kwargs):
        return {
            "id": "chunk-2",
            "text": "Exact raw chunk text.",
            "chunk_index": "not-an-index",
        }

    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", _fake_get_chunk_by_index)
    tool = weaviate_search.create_read_chunk_tool("doc-12345678", "user-1")

    with pytest.raises(ValueError, match="non-negative integer"):
        await tool(1)


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

    assert result.summary == "No section named 'Methods' in the readable files. Section names, exactly as stored: none."
    assert result.error_code == "section_not_found"
    assert result.section is None
    assert captured["document_id"] == "doc-12345678"
    assert captured["parent_section"] == "Methods"
    assert captured["user_id"] == "user-1"
    assert tracker.calls == ["read_section"]


@pytest.mark.asyncio
async def test_read_section_tool_returns_whole_numbered_passages_and_issues_them(monkeypatch):
    async def _chunks(**_kwargs):
        return [
            {
                "chunk_index": 17, "id": "chunk-methods-1",
                "text": "Paragraph one. It has two sentences.",
                "page_number": 2,
                "section_title": "Animals",
                "parent_section": "Materials and Methods",
                "subsection": "Animals",
                "metadata": '{"doc_items":[{"id":"bbox-1"}]}',
            },
            {
                "chunk_index": 19, "id": "chunk-methods-2",
                "content": "Paragraph two",
                "pageNumber": 3,
                "parent_section": "Materials and Methods",
                "metadata": {"doc_items": [{"id": "bbox-2"}]},
            },
        ]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _chunks)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")
    result = await tool("Materials and Methods")

    section = result.section
    assert section is not None
    assert result.error_code is None
    assert section.requested_section == "Materials and Methods"
    assert section.file == "paper.pdf"
    assert not hasattr(section, "matched_sections")
    assert section.page_numbers == [2, 3]
    assert section.total_chunk_count == 2
    assert section.text_contains is None and section.matched_chunk_count is None
    assert [p.chunk_index for p in section.passages] == [17, 19]
    assert [p.chunk_id for p in section.passages] == ["chunk-methods-1", "chunk-methods-2"]
    assert [p.content for p in section.passages] == ["Paragraph one. It has two sentences.", "Paragraph two"]
    assert section.passages[0].section == "Materials and Methods"
    assert section.passages[0].subsection == "Animals"
    assert [s.text for s in section.passages[0].evidence_spans] == ["Paragraph one.", "It has two sentences."]
    assert section.passages[0].doc_items == [{"id": "bbox-1"}]
    assert section.passages[1].doc_items == [{"id": "bbox-2"}]
    assert result.summary.startswith("Read all 2 chunks of 'Materials and Methods'. Each passage lists")
    assert not hasattr(section, "content") and not hasattr(section, "source_chunks")

    # Every shown span is issued, in one batched call for the whole read.
    weaviate_search.retain_issued_spans.assert_awaited_once()
    (reads,), _ = weaviate_search.retain_issued_spans.await_args
    assert [(chunk["chunk_index"], chunk["id"], issued) for chunk, _spans, issued in reads] == [
        (17, "chunk-methods-1", [0, 1]),
        (19, "chunk-methods-2", [0]),
    ]


@pytest.mark.asyncio
async def test_read_section_tool_retrieves_provider_figure_metadata(monkeypatch):
    async def _chunks(**kwargs):
        assert kwargs["parent_section"] == PROVIDER_FIGURE_METADATA_SECTION
        return [
            {
                "chunk_index": 0, "id": "chunk-provider-figure-1",
                "text": (
                    "Figure label: Figure 1\n\n"
                    "Legend:\n"
                    "Fig. 1A shows wg expression in the embryo."
                ),
                "page_number": 12,
                "section_title": PROVIDER_FIGURE_METADATA_SECTION,
                "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
                "subsection": f"{PROVIDER_FIGURE_SUBSECTION_PREFIX} Figure 1",
            }
        ]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _chunks)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")
    result = await tool(PROVIDER_FIGURE_METADATA_SECTION)

    assert result.section is not None
    assert result.section.file == "paper.pdf"
    assert "Fig. 1A shows wg expression" in result.section.passages[0].content
    assert result.section.passages[0].subsection == f"{PROVIDER_FIGURE_SUBSECTION_PREFIX} Figure 1"


@pytest.mark.asyncio
async def test_read_section_tool_propagates_dependency_exception(monkeypatch):
    from agents import function_tool  # pyright: ignore[reportMissingImports]

    async def _boom(**_kwargs):
        raise RuntimeError("section read failed")

    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _boom)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")
    payload = {"section_name": "Results"}

    with pytest.raises(RuntimeError, match="required PDF") as caught:
        await tool.on_invoke_tool(
            _build_tool_context(tool.name, payload), json.dumps(payload)
        )
    assert str(caught.value.__cause__) == "section read failed"


@pytest.mark.asyncio
async def test_read_subsection_tool_no_content_and_success(monkeypatch):
    captured = {}

    async def _no_chunks(**_kwargs):
        captured.update(_kwargs)
        return []

    tool = weaviate_search.create_read_subsection_tool("doc-123", "user-1")
    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", _no_chunks)
    empty_result = await tool("Methods", "Fly Strains")
    assert empty_result.summary == (
        "No section named 'Methods' in the readable files. Section names, exactly as stored: none.")
    assert empty_result.error_code == "section_not_found"
    assert empty_result.subsection is None
    assert captured == {
        "document_id": "doc-123", "parent_section": "Methods",
        "subsection": "Fly Strains", "user_id": "user-1", "readable": _READABLE["value"],
    }
    weaviate_search.retain_issued_spans.assert_not_awaited()

    async def _chunks(**_kwargs):
        return [
            {
                "chunk_index": 0, "id": "chunk-subsection-1",
                "text": "Line one",
                "page_number": 9,
                "parent_section": "Methods",
                "subsection": "Fly Strains",
                "doc_items": [{"id": "bbox-1"}],
            },
            {
                "chunk_index": 1, "id": "chunk-subsection-2",
                "content": "Line two",
                "parent_section": "Methods",
                "metadata": {
                    "pageNumber": 10,
                    "subsection": "Fly Strains",
                },
                "doc_items": [],
            },
        ]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", _chunks)
    success_result = await tool("Methods", "Fly Strains")
    section = success_result.subsection
    assert section is not None
    assert section.requested_section == "Methods"
    assert section.requested_subsection == "Fly Strains"
    assert section.file == "paper.pdf"
    assert section.page_numbers == [9, 10]
    assert [p.content for p in section.passages] == ["Line one", "Line two"]
    assert [p.chunk_id for p in section.passages] == ["chunk-subsection-1", "chunk-subsection-2"]
    assert section.total_chunk_count == 2
    assert section.passages[0].doc_items == [{"id": "bbox-1"}]
    assert section.passages[1].doc_items is None
    assert success_result.summary.startswith("Read all 2 chunks of 'Methods > Fly Strains'.")
    weaviate_search.retain_issued_spans.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_subsection_tool_retrieves_provider_figure_entry(monkeypatch):
    provider_subsection = f"{PROVIDER_FIGURE_SUBSECTION_PREFIX} Figure 2"

    async def _chunks(**kwargs):
        assert kwargs["parent_section"] == PROVIDER_FIGURE_METADATA_SECTION
        assert kwargs["subsection"] == provider_subsection
        return [
            {
                "chunk_index": 1, "id": "chunk-provider-figure-2",
                "text": (
                    "Figure label: Figure 2\n\n"
                    "Legend:\n"
                    "Fig. 2B shows eve expression in stripes."
                ),
                "page_number": 13,
                "section_title": PROVIDER_FIGURE_METADATA_SECTION,
                "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
                "subsection": provider_subsection,
            }
        ]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", _chunks)
    tool = weaviate_search.create_read_subsection_tool("doc-12345678", "user-1")
    result = await tool(PROVIDER_FIGURE_METADATA_SECTION, provider_subsection)

    assert result.subsection is not None
    assert result.subsection.file == "paper.pdf"
    assert "Fig. 2B shows eve expression" in result.subsection.passages[0].content
    assert result.subsection.passages[0].chunk_id == "chunk-provider-figure-2"


@pytest.mark.asyncio
async def test_read_subsection_tool_propagates_dependency_exception(monkeypatch):
    from agents import function_tool  # pyright: ignore[reportMissingImports]

    async def _boom(**_kwargs):
        raise RuntimeError("subsection failed")

    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", _boom)
    tool = weaviate_search.create_read_subsection_tool("doc-12345678", "user-1")
    payload = {"parent_section": "Results", "subsection": "Expression"}

    with pytest.raises(RuntimeError, match="required PDF") as caught:
        await tool.on_invoke_tool(
            _build_tool_context(tool.name, payload), json.dumps(payload)
        )
    assert str(caught.value.__cause__) == "subsection failed"


def _numbered_chunks(count: int, *, prefix: str = "chunk", text_for=None):
    """Build ``count`` fake section chunks with predictable ids and text."""
    chunks = []
    for i in range(count):
        text = text_for(i) if text_for else f"Chunk text {i} " + ("z" * 50)
        chunks.append(
            {
                "chunk_index": i, "id": f"{prefix}-{i}",
                "text": text,
                "page_number": i + 1,
                "parent_section": "Results",
            }
        )
    return chunks


@pytest.mark.parametrize("factory", ["create_read_section_tool", "create_read_subsection_tool"])
def test_section_tools_have_no_paging_or_size_parameters(monkeypatch, factory):
    from agents import function_tool  # pyright: ignore[reportMissingImports]

    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    tool = getattr(weaviate_search, factory)("doc-12345678", "user-1")
    properties = set(tool.params_json_schema["properties"])
    assert not properties & {"max_chunks", "offset", "next_offset", "limit"}
    assert "text_contains" in properties


@pytest.mark.asyncio
async def test_read_section_default_returns_entire_large_section(monkeypatch):
    async def _chunks(**_kwargs):
        return _numbered_chunks(45)

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _chunks)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")

    result = await tool("Results")
    assert result.section is not None
    assert len(result.section.passages) == 45
    assert result.section.total_chunk_count == 45


@pytest.mark.asyncio
async def test_read_section_text_contains_returns_whole_matching_chunks_and_issues_them(monkeypatch):
    pre = "p" * 400
    post = "q" * 400
    target_text = f"{pre} hedgehog ligand {post}. A second sentence follows."

    async def _chunks(**_kwargs):
        return [
            {"chunk_index": 0, "id": "chunk-0", "text": "no match here", "page_number": 1, "parent_section": "Results"},
            {"chunk_index": 1, "id": "chunk-1", "text": target_text, "page_number": 2, "parent_section": "Results"},
            {"chunk_index": 2, "id": "chunk-2", "text": "still nothing", "page_number": 3, "parent_section": "Results"},
        ]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _chunks)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")

    result = await tool("Results", text_contains="HEDGEHOG")
    section = result.section
    assert section is not None
    assert section.text_contains == "HEDGEHOG"
    assert section.matched_chunk_count == 1
    assert section.total_chunk_count == 3
    assert [p.chunk_index for p in section.passages] == [1]
    # The whole chunk, not an excerpt.
    assert section.passages[0].content == target_text
    assert section.page_numbers == [2]
    assert "1 of the 3 chunks of 'Results' contain 'HEDGEHOG'" in result.summary

    (reads,), _ = weaviate_search.retain_issued_spans.await_args
    assert [(chunk["chunk_index"], issued) for chunk, _spans, issued in reads] == [(1, [0, 1])]


@pytest.mark.asyncio
async def test_section_summary_names_no_sections_when_no_passage_has_a_section_name(monkeypatch):
    async def _chunks(**_kwargs):
        return [{"chunk_index": 0, "id": "chunk-0", "text": "Unnamed text.", "page_number": 1}]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _chunks)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")

    result = await tool("Results")
    assert result.section.file == "paper.pdf"
    assert "Matched section names" not in result.summary
    assert result.summary.startswith("Read all 1 chunks of 'Results'. Each passage lists numbered sentences;")


@pytest.mark.asyncio
async def test_read_section_text_contains_without_match_reports_counts_and_issues_nothing(monkeypatch):
    async def _chunks(**_kwargs):
        return _numbered_chunks(3)

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _chunks)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")

    result = await tool("Results", text_contains="absent phrase")
    assert result.section.passages == []
    assert result.section.matched_chunk_count == 0
    assert result.section.total_chunk_count == 3
    weaviate_search.retain_issued_spans.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_subsection_text_contains_returns_whole_matching_chunks(monkeypatch):
    async def _chunks(**_kwargs):
        return [
            {"chunk_index": 0, "id": "sub-0", "text": "alpha mention", "page_number": 1, "parent_section": "Methods", "subsection": "Fly Strains"},
            {"chunk_index": 1, "id": "sub-1", "text": "beta mention", "page_number": 2, "parent_section": "Methods", "subsection": "Fly Strains"},
            {"chunk_index": 2, "id": "sub-2", "text": "gamma mention", "page_number": 3, "parent_section": "Methods", "subsection": "Fly Strains"},
        ]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", _chunks)
    tool = weaviate_search.create_read_subsection_tool("doc-12345678", "user-1")

    filtered = await tool("Methods", "Fly Strains", text_contains="beta")
    assert filtered.subsection.total_chunk_count == 3
    assert filtered.subsection.matched_chunk_count == 1
    assert [p.chunk_id for p in filtered.subsection.passages] == ["sub-1"]
    assert filtered.subsection.passages[0].content == "beta mention"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_kinds",
    [
        ("deadline", "deadline"), ("reset", "reset"), ("deadline", "reset"),
        ("reset", "deadline"), ("unknown",),
    ],
    ids=["deadlines", "resets", "deadline-reset", "reset-deadline", "unknown-non-transient"],
)
async def test_search_tool_terminal_failure_has_one_capture_owner(monkeypatch, caplog, failure_kinds):
    """Search failures propagate to runtime capture without duplicate log events."""
    import asyncio
    import logging
    from contextlib import contextmanager
    from unittest.mock import MagicMock, patch

    import grpc
    import sentry_sdk
    from weaviate.exceptions import WeaviateQueryError

    import src.lib.weaviate_client.chunks as chunks
    from src.lib.observability.runtime import report_runtime_exception
    from src.lib.observability.sentry import _with_safe_log_record_metadata, before_send

    class _SearchRpcError(grpc.RpcError):
        def __init__(self, status):
            self.status = status

        def code(self):
            return self.status

    def _query_error(kind):
        status = grpc.StatusCode.DEADLINE_EXCEEDED if kind == "deadline" else grpc.StatusCode.UNKNOWN
        message = {
            "deadline": "Deadline Exceeded",
            "reset": 'vectorize: Post "https://api.openai.com/v1/embeddings": read: connection reset by peer',
            "unknown": "vectorize: invalid API key",
        }[kind]
        try:
            try:
                raise _SearchRpcError(status)
            except grpc.RpcError:
                raise WeaviateQueryError(message, "GRPC search")
        except WeaviateQueryError as error:
            return error

    async def _immediate(func, *args, **kwargs):
        return func(*args, **kwargs)

    @contextmanager
    def _session():
        yield MagicMock()

    monkeypatch.setattr(asyncio, "to_thread", _immediate)
    monkeypatch.setattr(chunks, "get_effective_rerank_provider", lambda: "none")
    connection = MagicMock()
    connection.session.side_effect = _session
    collection = MagicMock()
    errors = [_query_error(kind) for kind in failure_kinds]
    collection.query.hybrid.side_effect = errors
    caplog.set_level(logging.INFO)

    tool = weaviate_search.create_search_tool("doc-12345678", "user-1")
    with patch("src.lib.weaviate_client.chunks.get_connection", return_value=connection), \
         patch("src.lib.weaviate_helpers.get_user_collections", return_value=(collection, MagicMock())) as tenant_collections:
        with pytest.raises(WeaviateQueryError) as raised:
            await tool(query="Methods allele search", section_keywords=["Methods"])

    assert raised.value is errors[-1]
    assert collection.query.hybrid.call_count == len(failure_kinds)
    assert tenant_collections.call_count == 1
    assert tenant_collections.call_args.args[1] == "user-1"
    if len(failure_kinds) == 2:
        # Includes the same document/section filters and ranking parameters.
        assert collection.query.hybrid.call_args_list[0] == collection.query.hybrid.call_args_list[1]
    error_records = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert {record.name for record in error_records} == {chunks.logger.name}
    sentry_eligible = [
        record for record in caplog.records if record.levelno >= logging.WARNING
        if _with_safe_log_record_metadata({}, {"log_record": record}) is not None
    ]
    assert sentry_eligible == []
    assert len([
        record for record in caplog.records
        if getattr(record, "operation", None) == "weaviate_hybrid_search_transient_retry"
        and record.levelno == logging.WARNING
    ]) == len(failure_kinds) - 1

    # Exercise the canonical terminal reporting facade with an in-memory SDK
    # transport. The exhausted tool must leave its exception eligible for it.
    events = []
    with sentry_sdk.Client(
        dsn="http://public@example.invalid/1", transport=events.append,
        before_send=before_send, default_integrations=False, include_local_variables=False,
    ) as client, sentry_sdk.new_scope() as scope:
        scope.set_client(client)
        assert report_runtime_exception(
            raised.value, component="test_search_runtime", operation="search_failed",
        )
        client.flush()
    assert len(events) == 1
    assert events[0]["level"] == "error"


@pytest.mark.asyncio
async def test_real_sdk_preserves_required_pdf_read_failure(monkeypatch):
    from agents import function_tool
    from agr_ai_curation_runtime.document_errors import DocumentReadError, document_read_failure
    from src.lib.openai_agents.curator_errors import classify_failure
    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    cause = ValueError("Not valid uuid: PRIVATE DOCUMENT VALUE")
    async def fail(**kwargs):
        raise cause
    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", fail)
    tool = weaviate_search.create_read_chunk_tool("document", "owner")
    with pytest.raises(DocumentReadError) as caught:
        await tool.on_invoke_tool(_build_tool_context(tool.name, {}), json.dumps({
            "chunk_index": 42, "span_offset": 0,
        }))
    assert document_read_failure(caught.value).__cause__ is cause
    failure = classify_failure(caught.value)
    assert failure.category == "pdf_read_failed"
    assert "PRIVATE" not in failure.message


def _store_honouring_limit(monkeypatch, objects):
    """Fake Weaviate store whose fetch honours limit/offset as the real store does."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from src.lib.weaviate_client import chunks

    calls = []

    def fetch_objects(**kwargs):
        calls.append(kwargs)
        offset = kwargs.get("offset") or 0
        return SimpleNamespace(objects=objects[offset: offset + kwargs["limit"]])

    collection = MagicMock()
    collection.query.fetch_objects.side_effect = fetch_objects
    client = object()

    @contextmanager
    def session():
        yield client

    monkeypatch.setattr(chunks, "get_connection", lambda: SimpleNamespace(session=session))
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", lambda *_: (collection, MagicMock()))
    return calls


def _stored_section_objects(count, *, parent="Results", subsection="Expression"):
    from types import SimpleNamespace
    from uuid import UUID
    return [SimpleNamespace(uuid=UUID(int=index + 1), properties={
        "content": f"Passage {index} reports a finding. It has a second sentence.",
        "chunkIndex": index, "sectionTitle": parent, "parentSection": parent,
        "subsection": subsection, "pageNumber": 1 + index // 10, "metadata": {},
        "docItemProvenance": None,
    }) for index in range(count)]


@pytest.mark.asyncio
async def test_read_section_returns_every_chunk_of_a_section_over_fifty(monkeypatch):
    _store_honouring_limit(monkeypatch, _stored_section_objects(60))
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")

    result = await tool("Results")

    assert result.section is not None
    assert result.section.total_chunk_count == 60
    assert [passage.chunk_index for passage in result.section.passages] == list(range(60))


@pytest.mark.asyncio
async def test_read_subsection_returns_every_chunk_of_a_subsection_over_thirty(monkeypatch):
    _store_honouring_limit(monkeypatch, _stored_section_objects(40))
    tool = weaviate_search.create_read_subsection_tool("doc-12345678", "user-1")

    result = await tool("Results", "Expression")

    assert result.subsection is not None
    assert result.subsection.total_chunk_count == 40
    assert [passage.chunk_index for passage in result.subsection.passages] == list(range(40))


@pytest.mark.asyncio
@pytest.mark.parametrize("parent,count", [("Results", 4), ("Methods", 1)])
@pytest.mark.parametrize("partial_name", [False, True])
@pytest.mark.parametrize("mode", ["all", "filtered", "missing_identity", "missing_index", "no_match"])
async def test_subsection_real_retrieval_to_registered_tool_and_model_view(
    monkeypatch, parent, count, partial_name, mode,
):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import UUID
    from agents import function_tool
    from agr_ai_curation_alliance.tools.documents import create_read_subsection_tool
    from src.lib.weaviate_client import chunks
    from src.lib.agent_studio.catalog_service import _document_model_result
    from src.lib.openai_agents import streaming_tools

    texts = [f"  α passage {index}.\nNegative finding preserved.  " for index in range(count)]
    objects = [SimpleNamespace(uuid=UUID(int=index + 1), properties={
        "content": text, "chunkIndex": 30 + index, "sectionTitle": parent,
        "parentSection": parent, "subsection": "Animals and expression",
        "pageNumber": index + 2, "metadata": {},
        "docItemProvenance": json.dumps([{"page": index + 2, "bbox": [1, 2, 3, 4]}]),
    }) for index, text in enumerate(texts)]
    if mode == "missing_identity":
        objects[0].uuid = None
    elif mode == "missing_index":
        objects[0].properties["chunkIndex"] = None
    elif mode == "no_match":
        objects = []
    collection = MagicMock()
    collection.query.fetch_objects.return_value = SimpleNamespace(objects=objects)
    client = object()
    @contextmanager
    def session():
        yield client
    monkeypatch.setattr(chunks, "get_connection", lambda: SimpleNamespace(session=session))
    tenants = []
    def collections(actual_client, user_id):
        assert actual_client is client
        tenants.append(user_id)
        return collection, MagicMock()
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", collections)
    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)
    tool = create_read_subsection_tool({"document_id": "doc-12345678", "user_id": "owner-1"})
    payload = {"parent_section": parent, "subsection": "Animals" if partial_name else "Animals and expression"}
    selected = list(range(count))
    if mode == "filtered":
        payload["text_contains"] = "passage 0"
        selected = [0]
    if mode in {"missing_identity", "missing_index"} and not partial_name:
        from agr_ai_curation_runtime.document_errors import DocumentReadError
        with pytest.raises(DocumentReadError):
            await tool.on_invoke_tool(_build_tool_context(tool.name, payload), json.dumps(payload))
        weaviate_search.retain_issued_spans.assert_not_awaited()
        return
    canonical = await tool.on_invoke_tool(_build_tool_context(tool.name, payload), json.dumps(payload))
    raw = canonical.model_dump(mode="json")
    if mode == "no_match" or partial_name:
        # Names are exact (Q9): "Animals" is not "Animals and expression".
        assert raw["subsection"] is None
        assert raw["error_code"] == "section_not_found"
        assert raw["summary"].startswith("No ")
        weaviate_search.retain_issued_spans.assert_not_awaited()
        return
    result = raw["subsection"]
    assert [p["content"] for p in result["passages"]] == [texts[index] for index in selected]
    assert result["total_chunk_count"] == count
    assert result["matched_chunk_count"] == (1 if mode == "filtered" else None)
    assert result["file"] == "paper.pdf"
    assert "matched_sections" not in result
    assert [p["chunk_id"] for p in result["passages"]] == [str(objects[index].uuid) for index in selected]
    assert [p["chunk_index"] for p in result["passages"]] == [30 + index for index in selected]
    view = _document_model_result("read_subsection", canonical)
    passages = view["subsection"]["passages"]
    assert [p["chunk_index"] for p in passages] == [30 + index for index in selected]
    for passage, index in zip(passages, selected):
        assert passage["text"] == f"[0] α passage {index}.\n[1] Negative finding preserved."
        assert not {"content", "evidence_spans", "chunk_id", "doc_items"} & set(passage)
    assert emitted[0]["doc_items"] == [item for p in result["passages"] for item in p["doc_items"]]
    assert len(emitted) == 1
    assert tenants == ["owner-1", "owner-1"]  # names listed, then the chosen pieces read, both in the tenant
    assert all(str(obj.uuid) not in json.dumps(view) for obj in objects)
    (reads,), _ = weaviate_search.retain_issued_spans.await_args
    assert [(chunk["chunk_index"], chunk["id"], issued) for chunk, _spans, issued in reads] == [
        (30 + index, str(objects[index].uuid), [0, 1]) for index in selected
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_kind", ["section", "subsection"])
@pytest.mark.parametrize("bad_field,bad_value", [
    ("id", None), ("chunk_index", None), ("chunk_index", True), ("chunk_index", -1), ("text", ""), ("text", None),
])
@pytest.mark.parametrize("filtered", [False, True])
async def test_section_source_metadata_fault_is_not_empty_success_or_cursor_error(
    monkeypatch, tool_kind, bad_field, bad_value, filtered,
):
    from agents import function_tool
    from agr_ai_curation_runtime.document_errors import DocumentReadError
    row = {"id": "canonical-id", "chunk_index": 0, "text": "Unchanged scientific text", "page_number": 2}
    row[bad_field] = bad_value
    async def retrieve(**kwargs):
        return [row]
    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", retrieve)
    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", retrieve)
    factory = getattr(weaviate_search, f"create_read_{tool_kind}_tool")
    tool = factory("doc-12345678", "user-1")
    payload = {"section_name": "Methods"} if tool_kind == "section" else {"parent_section": "Methods", "subsection": "Animals"}
    if filtered:
        payload["text_contains"] = "scientific"
    with pytest.raises(DocumentReadError) as caught:
        await tool.on_invoke_tool(_build_tool_context(tool.name, payload), json.dumps(payload))
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "Stored section chunk" in str(caught.value.__cause__)


@pytest.mark.asyncio
async def test_read_section_reads_a_section_named_abstract(monkeypatch):
    """Agents no longer get the abstract in their prompt; they read it like any section."""
    requested = []

    async def _chunks(**kwargs):
        requested.append(kwargs["parent_section"])
        return [{
            "chunk_index": 0, "id": "chunk-abstract-1",
            "text": "A synthetic summary sentence. A second synthetic sentence.",
            "page_number": 1, "parent_section": "Abstract",
        }]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _chunks)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")
    result = await tool("Abstract")

    section = result.section
    assert requested == ["Abstract"]
    assert section is not None and section.file == "paper.pdf"
    assert [p.chunk_index for p in section.passages] == [0]
    assert [s.text for s in section.passages[0].evidence_spans] == [
        "A synthetic summary sentence.", "A second synthetic sentence.",
    ]


# --- Exact section names within one file (Q9, P8c) ------------------------------------


def _paper_with_two_sheets():
    return files("doc-12345678", ("paper.pdf", 0, 11), ("Table S1.xlsx", 12, 13), ("Table S1.xlsx", 14, 15))


def _sheet_chunk(index, section="Sheet1"):
    return {"chunk_index": index, "id": f"chunk-{index}", "text": f"Row {index}.", "parent_section": section}


@pytest.mark.asyncio
async def test_a_section_in_several_files_asks_for_the_file(monkeypatch):
    _READABLE["value"] = _paper_with_two_sheets()

    async def _chunks(**kwargs):
        return [_sheet_chunk(index) for index in range(12, 16) if kwargs["readable"].contains(index)]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _chunks)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")

    result = await tool("Sheet1")

    assert result.section is None
    assert result.error_code == "section_ambiguous"
    assert result.summary == (
        "Section 'Sheet1' is in more than one file: Table S1.xlsx, Table S1.xlsx (2). "
        "Call again with file set to one of these names."
    )
    weaviate_search.retain_issued_spans.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_reads_only_that_files_section(monkeypatch):
    _READABLE["value"] = _paper_with_two_sheets()
    asked = []

    async def _chunks(**kwargs):
        asked.append(kwargs["readable"])
        return [_sheet_chunk(index) for index in range(12, 16) if kwargs["readable"].contains(index)]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _chunks)
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")

    result = await tool("Sheet1", file="Table S1.xlsx (2)")

    assert result.error_code is None
    assert result.section.file == "Table S1.xlsx (2)"
    assert [p.chunk_index for p in result.section.passages] == [14, 15]
    assert [(f.chunk_index_start, f.chunk_index_end) for f in asked[0].files] == [(14, 15)]


@pytest.mark.asyncio
async def test_an_unknown_file_lists_the_readable_files(monkeypatch):
    _READABLE["value"] = _paper_with_two_sheets()
    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section",
                        AsyncMock(side_effect=AssertionError("no read for an unknown file")))
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")

    result = await tool("Sheet1", file="Table S2.xlsx")

    assert result.error_code == "unknown_file"
    assert result.summary == (
        "There is no readable file named 'Table S2.xlsx'. "
        "Readable files: paper.pdf, Table S1.xlsx, Table S1.xlsx (2)."
    )


@pytest.mark.asyncio
async def test_a_short_name_gets_every_exact_name_file_by_file(monkeypatch):
    """A built-in prompt still saying read_section('Methods') gets a helpful answer, not an error."""
    _READABLE["value"] = files("doc-12345678", ("paper.pdf", 0, 11), ("Table S1.xlsx", 12, 13))
    _OUTLINE["paper.pdf"] = [{"name": "Introduction", "subsections": []},
                             {"name": "Materials and Methods", "subsections": [{"name": "Fly strains"}]}]
    _OUTLINE["Table S1.xlsx"] = [{"name": "Sheet1", "subsections": []}]
    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", AsyncMock(return_value=[]))
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")

    result = await tool("Methods")

    assert result.section is None
    assert result.error_code == "section_not_found"
    assert result.summary == (
        "No section named 'Methods' in the readable files. Section names, exactly as stored: "
        "paper.pdf: 'Introduction', 'Materials and Methods'; Table S1.xlsx: 'Sheet1'."
    )


@pytest.mark.asyncio
async def test_a_missing_subsection_lists_that_sections_subsections(monkeypatch):
    _OUTLINE["paper.pdf"] = [{"name": "Materials and Methods",
                              "subsections": [{"name": "Fly strains"}, {"name": "Imaging"}]}]
    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", AsyncMock(return_value=[]))
    tool = weaviate_search.create_read_subsection_tool("doc-12345678", "user-1")

    result = await tool("Materials and Methods", "Strains")

    assert result.error_code == "section_not_found"
    assert result.summary == (
        "No subsection 'Strains' in section 'Materials and Methods' in the readable files. "
        "Subsection names of 'Materials and Methods', exactly as stored: paper.pdf: 'Fly strains', 'Imaging'."
    )


@pytest.mark.asyncio
async def test_section_answers_reach_the_model_and_do_not_end_the_run(monkeypatch):
    from agents import function_tool
    from src.lib.agent_studio.catalog_service import _document_model_result

    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", AsyncMock(return_value=[]))
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")
    payload = {"section_name": "Methods"}

    result = await tool.on_invoke_tool(_build_tool_context(tool.name, payload), json.dumps(payload))

    assert _document_model_result("read_section", result.model_dump(mode="json"))["error_code"] == "section_not_found"


def test_section_tools_take_an_optional_file(monkeypatch):
    from agents import function_tool

    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    for factory in (weaviate_search.create_read_section_tool, weaviate_search.create_read_subsection_tool):
        schema = factory("doc-12345678", "user-1").params_json_schema
        assert "file" in schema["properties"]


@pytest.mark.asyncio
async def test_a_subsection_of_a_section_without_subsections_says_so(monkeypatch):
    """Recorded call shape: read_subsection("Methods", "Animals") on a paper whose Methods has none."""
    _OUTLINE["paper.pdf"] = [{"name": "Introduction", "subsections": []}, {"name": "Methods", "subsections": []}]
    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", AsyncMock(return_value=[]))
    tool = weaviate_search.create_read_subsection_tool("doc-12345678", "user-1")

    result = await tool("Methods", "Animals")

    assert result.error_code == "section_not_found"
    assert result.summary == (
        "Section 'Methods' in the readable files has no subsections. Read it whole with read_section."
    )


@pytest.mark.asyncio
async def test_a_subsection_of_a_missing_section_gets_the_section_names(monkeypatch):
    _OUTLINE["paper.pdf"] = [{"name": "Materials and Methods", "subsections": [{"name": "Animals"}]}]
    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", AsyncMock(return_value=[]))
    tool = weaviate_search.create_read_subsection_tool("doc-12345678", "user-1")

    result = await tool("Methods", "Animals")

    assert result.summary == (
        "No section named 'Methods' in the readable files. "
        "Section names, exactly as stored: paper.pdf: 'Materials and Methods'."
    )


@pytest.mark.asyncio
async def test_pieces_without_a_stored_section_name_are_not_offered_as_a_name(monkeypatch):
    _OUTLINE["paper.pdf"] = [{"name": "Unknown", "name_stored": False, "subsections": []},
                             {"name": "Results", "name_stored": True, "subsections": []}]
    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", AsyncMock(return_value=[]))
    tool = weaviate_search.create_read_section_tool("doc-12345678", "user-1")

    result = await tool("Methods")

    assert result.summary == (
        "No section named 'Methods' in the readable files. Section names, exactly as stored: paper.pdf: 'Results'."
    )
