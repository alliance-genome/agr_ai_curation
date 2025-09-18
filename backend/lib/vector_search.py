"""Vector search using PostgreSQL pgvector embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence
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
        embedding: Sequence[float],
        top_k: int = 5,
        pdf_id: Optional[UUID] = None,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
    ) -> List[SearchResult]:
        if top_k <= 0:
            return []

        vector_literal = self._format_vector(embedding)

        if pdf_id is None and (source_type is None or source_id is None):
            raise ValueError(
                "VectorSearch.query requires either pdf_id or source_type/source_id."
            )

        if pdf_id is not None:
            stmt = text(
                """
                SELECT chunk_id, embedding <-> CAST(:embedding AS vector) AS distance
                FROM pdf_embeddings
                WHERE pdf_id = :pdf_id AND model_name = :model_name
                ORDER BY embedding <-> CAST(:embedding AS vector)
                LIMIT :limit
                """
            )
            params = {
                "embedding": vector_literal,
                "pdf_id": str(pdf_id),
                "model_name": self._model_name,
                "limit": top_k,
            }
        else:
            stmt = text(
                """
                SELECT id AS chunk_id,
                       embedding <-> CAST(:embedding AS vector) AS distance
                FROM unified_chunks
                WHERE source_type = :source_type
                  AND source_id = :source_id
                  AND embedding IS NOT NULL
                ORDER BY embedding <-> CAST(:embedding AS vector)
                LIMIT :limit
                """
            )
            params = {
                "embedding": vector_literal,
                "source_type": source_type,
                "source_id": source_id,
                "limit": top_k,
            }

        with self._engine.connect() as connection:
            rows = connection.execute(stmt, params).all()

        return [
            SearchResult(chunk_id=row.chunk_id, distance=row.distance) for row in rows
        ]


__all__ = ["VectorSearch", "SearchResult"]
