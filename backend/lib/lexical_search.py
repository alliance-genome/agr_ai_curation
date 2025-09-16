"""Lexical search helper using PostgreSQL full-text search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass
class LexicalResult:
    chunk_id: UUID
    snippet: str
    rank: float


class LexicalSearch:
    """Runs lexical search against chunk_search using tsquery."""

    def __init__(self, engine: Engine, language: str = "english") -> None:
        self._engine = engine
        self._language = language

    def query(self, *, pdf_id: UUID, query: str, top_k: int = 5) -> List[LexicalResult]:
        if top_k <= 0 or not query.strip():
            return []

        stmt = text(
            """
            SELECT
                cs.chunk_id,
                pc.text AS chunk_text,
                ts_rank(cs.search_vector, plainto_tsquery(:language, :query)) AS rank,
                pc.chunk_index
            FROM chunk_search cs
            JOIN pdf_chunks pc ON pc.id = cs.chunk_id
            WHERE pc.pdf_id = :pdf_id
              AND cs.search_vector @@ plainto_tsquery(:language, :query)
            ORDER BY rank DESC, pc.chunk_index ASC
            LIMIT :limit
            """
        )

        with self._engine.connect() as connection:
            rows = connection.execute(
                stmt,
                {
                    "language": self._language,
                    "query": query,
                    "pdf_id": str(pdf_id),
                    "limit": top_k,
                },
            ).all()

        results: List[LexicalResult] = []
        for row in rows:
            rank = float(row.rank) if row.rank is not None else 0.0
            results.append(
                LexicalResult(chunk_id=row.chunk_id, snippet=row.chunk_text, rank=rank)
            )
        return results


__all__ = ["LexicalSearch", "LexicalResult"]
