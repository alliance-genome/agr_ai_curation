"""TDD-RED: Tests for Maximal Marginal Relevance diversification."""

from __future__ import annotations

from uuid import uuid4

import pytest

from lib.mmr_diversifier import mmr_diversify


def test_mmr_promotes_diversity_with_lambda():
    """MMR should prefer diverse candidates when scores are similar."""

    candidates = [
        {
            "chunk_id": uuid4(),
            "score": 0.95,
            "embedding": [1.0, 0.0, 0.0],
        },
        {
            "chunk_id": uuid4(),
            "score": 0.94,
            "embedding": [0.9, 0.1, 0.0],
        },
        {
            "chunk_id": uuid4(),
            "score": 0.60,
            "embedding": [0.0, 1.0, 0.0],
        },
    ]

    reranked = mmr_diversify(candidates, lambda_param=0.7, top_k=2)

    assert [item["chunk_id"] for item in reranked] == [
        candidates[0]["chunk_id"],
        candidates[2]["chunk_id"],
    ]
    assert reranked[0]["mmr_score"] >= reranked[1]["mmr_score"]


def test_mmr_gracefully_handles_missing_embeddings():
    """MMR should fall back to score ordering when embeddings are missing."""

    candidates = [
        {"chunk_id": uuid4(), "score": 0.8, "embedding": None},
        {"chunk_id": uuid4(), "score": 0.9, "embedding": None},
    ]

    reranked = mmr_diversify(candidates, lambda_param=0.7, top_k=2)

    assert [item["chunk_id"] for item in reranked] == [
        candidates[1]["chunk_id"],
        candidates[0]["chunk_id"],
    ]
