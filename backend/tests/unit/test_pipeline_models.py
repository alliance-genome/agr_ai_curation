"""TDD-RED: Tests for general pipeline output model."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.agents.pipeline_models import GeneralPipelineChunk, GeneralPipelineOutput


def test_general_pipeline_output_requires_chunk_text():
    chunk_id = uuid4()
    with pytest.raises(ValueError):
        GeneralPipelineChunk(chunk_id=chunk_id, text="", score=0.8, source="vector")


def test_general_pipeline_output_sorts_chunks_by_score():
    chunks = [
        GeneralPipelineChunk(chunk_id=uuid4(), text="B", score=0.1, source="lexical"),
        GeneralPipelineChunk(chunk_id=uuid4(), text="A", score=0.8, source="vector"),
    ]

    output = GeneralPipelineOutput.from_chunks(
        query="example", pdf_id=uuid4(), chunks=chunks
    )

    assert output.sorted_chunks[0].text == "A"
    assert output.metadata["total_chunks"] == 2
    assert output.query == "example"
