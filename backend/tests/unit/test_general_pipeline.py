"""Tests for the general retrieval pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List
from uuid import UUID, uuid4

import pytest

from lib.hybrid_search import (
    HybridSearchMetrics,
    HybridSearchResponse,
    HybridSearchResult,
)
from lib.reranker import RerankedResult
from lib.pipelines.general_pipeline import GeneralPipeline


@dataclass
class FakeHybridSearch:
    response: HybridSearchResponse

    def query(self, **kwargs):  # type: ignore[override]
        return self.response


class FakeReranker:
    def __init__(self, results: List[RerankedResult]) -> None:
        self._results = results
        self.calls: List[int] = []

    def rerank(self, **kwargs):  # type: ignore[override]
        self.calls.append(kwargs.get("top_k", 0))
        return self._results


class FakeEmbedder:
    def __call__(self, query: str):
        return [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_general_pipeline_returns_sorted_chunks():
    chunk_a = uuid4()
    chunk_b = uuid4()
    hybrid_response = HybridSearchResponse(
        results=[
            HybridSearchResult(
                chunk_id=chunk_a,
                text="Chunk A",
                score=0.3,
                source="vector",
                page=1,
                section="Intro",
                is_table=False,
                is_figure=False,
            ),
            HybridSearchResult(
                chunk_id=chunk_b,
                text="Chunk B",
                score=0.6,
                source="lexical",
                page=2,
                section=None,
                is_table=False,
                is_figure=False,
            ),
        ],
        metrics=HybridSearchMetrics(
            vector_candidates=1,
            lexical_candidates=1,
            overlap_count=0,
            final_count=2,
        ),
    )

    reranker = FakeReranker(
        [
            RerankedResult(
                chunk_id=chunk_b,
                rerank_score=0.8,
                combined_score=0.9,
                metadata={"retriever_score": 0.6},
                rank=0,
            ),
            RerankedResult(
                chunk_id=chunk_a,
                rerank_score=0.4,
                combined_score=0.5,
                metadata={"retriever_score": 0.3},
                rank=1,
            ),
        ]
    )

    pipeline = GeneralPipeline(
        hybrid_search=FakeHybridSearch(hybrid_response),
        reranker=reranker,
        query_embedder=FakeEmbedder(),
        vector_top_k=10,
        lexical_top_k=10,
        max_results=20,
        rerank_top_k=2,
        mmr_lambda=0.7,
    )

    output = await pipeline.run(pdf_id=uuid4(), query="What is BRCA1?")

    assert [chunk.text for chunk in output.sorted_chunks] == ["Chunk B", "Chunk A"]
    assert output.sorted_chunks[0].citation == {"page": 2, "section": None}
    assert reranker.calls[0] == 2
    assert output.sorted_chunks[0].metadata["retriever_score"] == 0.6
