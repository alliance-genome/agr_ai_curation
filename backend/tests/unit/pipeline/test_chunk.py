"""Unit tests for chunk overflow protections."""

from src.lib.pipeline.chunk import (
    _split_oversized_text,
    _chunk_by_title,
    _chunk_by_paragraph,
    _chunk_by_sentence,
)
from src.models.strategy import ChunkingStrategy, ChunkingMethod, StrategyName


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


def test_chunk_by_title_splits_single_oversized_element():
    strategy = _strategy(ChunkingMethod.BY_TITLE, max_chars=500, overlap=100)
    elements = [{"type": "Paragraph", "text": "A" * 1300, "metadata": {"page_number": 1}}]

    chunks = _chunk_by_title(elements, strategy)

    assert len(chunks) >= 3
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
