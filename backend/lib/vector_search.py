"""Vector search using PostgreSQL pgvector embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass
class SearchResult:
    chunk_id: UUID
    distance: float


class VectorSearch:
    """Lightweight vector search utility backed by pgvector."""

    def __init__(self, engine: Engine, model_name: str) -> None:
        self._engine = engine
        self._model_name = model_name

    @staticmethod
    def _format_vector(values: Sequence[float]) -> str:
        if not values:
            raise ValueError("embedding must contain at least one value")
        formatted = ",".join(f"{value:.8f}" for value in values)
        return f"[{formatted}]"

    def query(
        self,
        *,
        pdf_id: UUID,
        embedding: Sequence[float],
        top_k: int = 5,
    ) -> List[SearchResult]:
        if top_k <= 0:
            return []

        vector_literal = self._format_vector(embedding)

        stmt = text(
            """
            SELECT chunk_id, embedding <-> CAST(:embedding AS vector) AS distance
            FROM pdf_embeddings
            WHERE pdf_id = :pdf_id AND model_name = :model_name
            ORDER BY embedding <-> CAST(:embedding AS vector)
            LIMIT :limit
            """
        )

        with self._engine.connect() as connection:
            rows = connection.execute(
                stmt,
                {
                    "embedding": vector_literal,
                    "pdf_id": str(pdf_id),
                    "model_name": self._model_name,
                    "limit": top_k,
                },
            ).all()

        return [
            SearchResult(chunk_id=row.chunk_id, distance=row.distance) for row in rows
        ]


__all__ = ["VectorSearch", "SearchResult"]
