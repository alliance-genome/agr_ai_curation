"""Unit tests for chunk overflow protections and core chunking behaviors."""

import pytest

from src.lib.pipeline.chunk import (
    _split_oversized_text,
    _chunk_by_title,
    _chunk_by_paragraph,
    _chunk_by_sentence,
    _chunk_by_character,
    _create_document_chunk,
    assign_chunk_indices,
    chunk_parsed_document,
    store_chunks_batch,
    ChunkingError,
)
from src.models.strategy import ChunkingStrategy, ChunkingMethod, StrategyName
from src.models.chunk import ChunkMetadata, DocumentChunk, ElementType


def _strategy(method: ChunkingMethod, max_chars: int = 500, overlap: int = 100) -> ChunkingStrategy:
    return ChunkingStrategy(
        strategy_name=StrategyName.RESEARCH,
        chunking_method=method,
        max_characters=max_chars,
        overlap_characters=overlap,
        include_metadata=True,
        exclude_element_types=[],
    )


def test_split_oversized_text_respects_max_chars():
    text = "word " * 120
    segments = _split_oversized_text(text, max_chars=500, overlap_chars=100)

    assert segments
    assert all(len(segment) <= 500 for segment in segments)


def test_split_oversized_text_rejects_invalid_max_chars():
    with pytest.raises(ValueError, match="max_chars must be greater than 0"):
        _split_oversized_text("abc", max_chars=0)


def test_split_oversized_text_hard_split_preserves_overlap():
    text = "A" * 1200
    segments = _split_oversized_text(text, max_chars=500, overlap_chars=100)

    assert len(segments) >= 3
    assert all(len(segment) <= 500 for segment in segments)
    assert segments[1][:100] == segments[0][-100:]


def test_chunk_by_title_splits_single_oversized_element():
    strategy = _strategy(ChunkingMethod.BY_TITLE, max_chars=500, overlap=100)
    elements = [{"type": "Paragraph", "text": "A" * 1300, "metadata": {"page_number": 1}}]

    chunks = _chunk_by_title(elements, strategy)

    assert len(chunks) >= 3
    assert all(len(chunk["content"]) <= 500 for chunk in chunks)


def test_chunk_by_character_generates_overlap_and_offsets():
    strategy = _strategy(ChunkingMethod.BY_CHARACTER, max_chars=500, overlap=100)
    elements = [{"type": "Paragraph", "text": "Z" * 1200, "metadata": {"page_number": 1}}]

    chunks = _chunk_by_character(elements, strategy)

    assert len(chunks) >= 3
    assert chunks[0]["metadata"]["start_char"] == 0
    assert chunks[1]["metadata"]["start_char"] < chunks[0]["metadata"]["end_char"]
    assert all(len(chunk["content"]) <= 500 for chunk in chunks)


def test_chunk_by_paragraph_splits_single_oversized_element():
    strategy = _strategy(ChunkingMethod.BY_PARAGRAPH, max_chars=500, overlap=100)
    elements = [{"type": "Paragraph", "text": "B" * 1300, "metadata": {"page_number": 1}}]

    chunks = _chunk_by_paragraph(elements, strategy)

    assert len(chunks) >= 3
    assert all(len(chunk["content"]) <= 500 for chunk in chunks)


def test_chunk_by_sentence_splits_single_oversized_sentence():
    strategy = _strategy(ChunkingMethod.BY_SENTENCE, max_chars=500, overlap=100)
    elements = [{"type": "Paragraph", "text": "C" * 1300, "metadata": {"page_number": 1}}]

    chunks = _chunk_by_sentence(elements, strategy)

    assert len(chunks) >= 3
    assert all(len(chunk["content"]) <= 500 for chunk in chunks)


@pytest.mark.asyncio
async def test_chunk_parsed_document_filters_page_footer_and_chunks():
    strategy = _strategy(ChunkingMethod.BY_PARAGRAPH, max_chars=500, overlap=50)
    elements = [
        {"type": "NarrativeText", "text": "Real content for chunking.", "metadata": {"page_number": 1}},
        {"type": "Footer", "text": "Page 1", "metadata": {"doc_item_label": "page_footer"}},
    ]

    chunks = await chunk_parsed_document(elements, strategy, "doc-1")

    assert len(chunks) == 1
    assert "Real content" in chunks[0].content
    assert "Page 1" not in chunks[0].content


@pytest.mark.asyncio
async def test_chunk_parsed_document_raises_when_only_footer_elements():
    strategy = _strategy(ChunkingMethod.BY_PARAGRAPH, max_chars=500, overlap=50)
    elements = [
        {"type": "Footer", "text": "footer only", "metadata": {"doc_item_label": "page_footer"}},
    ]

    with pytest.raises(ChunkingError, match="No elements to chunk"):
        await chunk_parsed_document(elements, strategy, "doc-1")


@pytest.mark.asyncio
async def test_chunk_parsed_document_unknown_method_is_wrapped():
    strategy = _strategy(ChunkingMethod.BY_PARAGRAPH, max_chars=500, overlap=50)
    strategy.chunking_method = "unknown"  # type: ignore[assignment]
    elements = [{"type": "NarrativeText", "text": "text", "metadata": {}}]

    with pytest.raises(ChunkingError, match="Unknown chunking method"):
        await chunk_parsed_document(elements, strategy, "doc-1")


def test_create_document_chunk_maps_type_hierarchy_and_provenance():
    strategy = _strategy(ChunkingMethod.BY_TITLE, max_chars=500, overlap=50)
    chunk_data = {
        "content": "Table section content",
        "elements": [
            {
                "type": "Table",
                "index": 3,
                "metadata": {
                    "element_id": "el-1",
                    "doc_item_label": "table",
                    "page_number": "2",
                    "provenance": [
                        {
                            "page_no": "2",
                            "bbox": {
                                "left": 0.1,
                                "top": 1.0,
                                "right": 0.5,
                                "bottom": 0.2,
                                "coord_origin": "BOTTOMLEFT",
                            },
                        }
                    ],
                },
            }
        ],
        "metadata": {
            "section_title": "Results",
            "section_path": ["Results", "Subsection"],
            "content_type": "table",
            "parent_section": "Results",
            "subsection": "Subsection",
            "is_top_level": False,
        },
    }

    chunk = _create_document_chunk(chunk_data, 0, "doc-1", strategy)

    assert chunk.element_type == ElementType.TABLE
    assert chunk.page_number == 2
    assert chunk.section_title == "Results"
    assert chunk.parent_section == "Results"
    assert chunk.subsection == "Subsection"
    assert chunk.is_top_level is False
    assert len(chunk.doc_items) == 1
    assert chunk.metadata.has_table is True
    assert chunk.metadata.content_type == "table"


def test_create_document_chunk_falls_back_for_invalid_page_and_bbox():
    strategy = _strategy(ChunkingMethod.BY_PARAGRAPH, max_chars=500, overlap=50)
    chunk_data = {
        "content": "Image chunk",
        "elements": [
            {
                "type": "ListItem",
                "metadata": {
                    "page_number": "-3",
                    "content_type": "image",
                    "provenance": [
                        {
                            "page_no": "not-an-int",
                            "bbox": {
                                "left": "bad",
                                "top": 1.0,
                                "right": 0.5,
                                "bottom": 0.2,
                                "coord_origin": "BOTTOMLEFT",
                            },
                        }
                    ],
                },
            }
        ],
        "metadata": {"content_type": "image"},
    }

    chunk = _create_document_chunk(chunk_data, 1, "doc-2", strategy)

    assert chunk.element_type == ElementType.LIST_ITEM
    assert chunk.page_number == 1
    assert chunk.doc_items == []
    assert chunk.metadata.has_image is True


def test_assign_chunk_indices_rewrites_ids_sequentially():
    metadata = ChunkMetadata(character_count=1, word_count=1)
    chunks = [
        DocumentChunk(
            id="wrong_1",
            document_id="doc-1",
            chunk_index=9,
            content="a",
            element_type=ElementType.NARRATIVE_TEXT,
            page_number=1,
            metadata=metadata,
        ),
        DocumentChunk(
            id="wrong_2",
            document_id="doc-1",
            chunk_index=8,
            content="b",
            element_type=ElementType.NARRATIVE_TEXT,
            page_number=1,
            metadata=metadata,
        ),
    ]

    updated = assign_chunk_indices(chunks)

    assert [chunk.chunk_index for chunk in updated] == [0, 1]
    assert updated[0].id == "doc-1_chunk_0000"
    assert updated[1].id == "doc-1_chunk_0001"


@pytest.mark.asyncio
async def test_store_chunks_batch_counts_all_chunks():
    metadata = ChunkMetadata(character_count=1, word_count=1)
    chunks = [
        DocumentChunk(
            id=f"doc-1_chunk_{idx:04d}",
            document_id="doc-1",
            chunk_index=idx,
            content=f"c{idx}",
            element_type=ElementType.NARRATIVE_TEXT,
            page_number=1,
            metadata=metadata,
        )
        for idx in range(5)
    ]

    stored = await store_chunks_batch(chunks, batch_size=2)
    assert stored == 5
