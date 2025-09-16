"""Maximal Marginal Relevance helper for reranking results."""

from __future__ import annotations

from math import sqrt
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence


def _cosine_similarity(a: Sequence[float] | None, b: Sequence[float] | None) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(y * y for y in b))
    denominator = norm_a * norm_b
    if denominator == 0:
        return 0.0
    return dot / denominator


def _max_similarity(
    candidate: Mapping[str, Any], selected: Iterable[Mapping[str, Any]]
) -> float:
    return max(
        (
            _cosine_similarity(candidate.get("embedding"), item.get("embedding"))
            for item in selected
        ),
        default=0.0,
    )


def mmr_diversify(
    candidates: Sequence[Mapping[str, Any]],
    *,
    lambda_param: float,
    top_k: int,
) -> List[Dict[str, Any]]:
    """Select diversified candidates using Maximal Marginal Relevance."""

    if top_k <= 0 or not candidates:
        return []

    weight = max(0.0, min(1.0, lambda_param))
    remaining: List[Dict[str, Any]] = [dict(candidate) for candidate in candidates]
    selected: List[Dict[str, Any]] = []

    while remaining and len(selected) < top_k:
        if not selected:
            best = max(remaining, key=lambda item: float(item.get("score", 0.0)))
            mmr_score = float(best.get("score", 0.0))
        else:

            def _mmr_value(item: Mapping[str, Any]) -> float:
                relevance = float(item.get("score", 0.0))
                diversity_penalty = _max_similarity(item, selected)
                return weight * relevance - (1 - weight) * diversity_penalty

            best = max(remaining, key=_mmr_value)
            mmr_score = weight * float(best.get("score", 0.0)) - (
                1 - weight
            ) * _max_similarity(best, selected)

        result = dict(best)
        result["mmr_score"] = mmr_score
        selected.append(result)
        remaining.remove(best)

    return selected


__all__ = ["mmr_diversify"]
