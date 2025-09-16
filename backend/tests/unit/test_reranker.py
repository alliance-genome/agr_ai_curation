"""TDD-RED: Tests for cross-encoder reranker behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List
from uuid import uuid4

import pytest

from lib.reranker import Reranker, RerankerCandidate


@dataclass
class _RecordedCall:
    query: str
    candidates: List[str]


class FakeCrossEncoder:
    def __init__(self, scores: List[float]) -> None:
        self._scores = scores
        self.calls: list[_RecordedCall] = []

    def predict(self, pairs: List[list[str]]) -> List[float]:
        self.calls.append(
            _RecordedCall(query=pairs[0][0], candidates=[pair[1] for pair in pairs])
        )
        return list(self._scores[: len(pairs)])


def test_reranker_scores_and_sorts_candidates():
    """Reranker should call the cross-encoder and sort by rerank score."""

    fake_model = FakeCrossEncoder(scores=[0.9, 0.1, 0.6])
    reranker = Reranker(cross_encoder=fake_model)

    candidates = [
        RerankerCandidate(
            chunk_id=uuid4(),
            text="Gene BRCA1 is linked to cancer risk",
            retriever_score=0.42,
        ),
        RerankerCandidate(
            chunk_id=uuid4(),
            text="Methodology details about sequencing",
            retriever_score=0.61,
        ),
        RerankerCandidate(
            chunk_id=uuid4(),
            text="Background on unrelated pathways",
            retriever_score=0.58,
        ),
    ]

    results = reranker.rerank(
        query="Which genes are associated with cancer?",
        candidates=candidates,
        top_k=2,
        apply_mmr=False,
    )

    assert len(fake_model.calls) == 1
    recorded_call = fake_model.calls[0]
    assert recorded_call.query == "Which genes are associated with cancer?"
    assert recorded_call.candidates[0] == "Gene BRCA1 is linked to cancer risk"

    assert [result.chunk_id for result in results] == [
        candidates[0].chunk_id,
        candidates[2].chunk_id,
    ]
    assert results[0].rerank_score == pytest.approx(0.9)
    assert results[0].metadata["retriever_score"] == pytest.approx(0.42)
