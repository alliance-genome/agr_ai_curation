"""Cross-encoder reranker with optional MMR diversification."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from .mmr_diversifier import mmr_diversify

try:  # pragma: no cover - optional dependency
    from sentence_transformers import CrossEncoder  # type: ignore
except Exception:  # pragma: no cover - sentence-transformers not installed
    CrossEncoder = None  # type: ignore[misc]


@dataclass
class RerankerCandidate:
    """Candidate chunk pending reranking."""

    chunk_id: UUID
    text: str
    retriever_score: float
    embedding: Optional[Sequence[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RerankedResult:
    """Reranked chunk with scores and metadata."""

    chunk_id: UUID
    rerank_score: float
    combined_score: float
    metadata: Dict[str, Any]
    rank: int


class Reranker:
    """Applies cross-encoder reranking with optional MMR diversification."""

    def __init__(
        self,
        *,
        cross_encoder: Any | None = None,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-12-v2",
    ) -> None:
        if cross_encoder is not None:
            self._model = cross_encoder
        else:
            if CrossEncoder is None:
                raise RuntimeError(
                    "sentence-transformers is not installed. Provide a cross_encoder instance"
                )
            self._model = CrossEncoder(model_name)
        self._model_name = model_name

    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankerCandidate],
        top_k: int = 5,
        apply_mmr: bool = True,
        lambda_param: float = 0.7,
    ) -> List[RerankedResult]:
        if not candidates or top_k <= 0:
            return []

        pairs = [[query, candidate.text] for candidate in candidates]
        scores = self._model.predict(pairs)

        scored_candidates: List[Dict[str, Any]] = []
        for candidate, score in zip(candidates, scores):
            metadata = dict(candidate.metadata)
            metadata.setdefault("retriever_score", candidate.retriever_score)
            scored_candidates.append(
                {
                    "chunk_id": candidate.chunk_id,
                    "score": float(score),
                    "embedding": candidate.embedding,
                    "metadata": metadata,
                    "retriever_score": candidate.retriever_score,
                }
            )

        if apply_mmr:
            diversified = mmr_diversify(
                scored_candidates, lambda_param=lambda_param, top_k=top_k
            )
        else:
            diversified = sorted(
                scored_candidates, key=lambda item: item["score"], reverse=True
            )[:top_k]
            for item in diversified:
                item["mmr_score"] = item["score"]

        results: List[RerankedResult] = []
        for idx, item in enumerate(diversified):
            metadata = dict(item.get("metadata", {}))
            metadata["retriever_score"] = item.get("retriever_score")
            metadata["model_name"] = self._model_name
            metadata["mmr_score"] = item.get("mmr_score")
            results.append(
                RerankedResult(
                    chunk_id=item["chunk_id"],
                    rerank_score=float(item["score"]),
                    combined_score=float(item.get("mmr_score", item["score"])),
                    metadata=metadata,
                    rank=idx,
                )
            )

        return results


__all__ = ["Reranker", "RerankerCandidate", "RerankedResult"]


def main(
    argv: Optional[Sequence[str]] | None = None,
) -> None:  # pragma: no cover - CLI passthrough
    from .cli import rerank_cli

    rerank_cli.main(argv)


if __name__ == "__main__":  # pragma: no cover
    main()
