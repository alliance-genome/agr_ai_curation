"""Lexical search helper using PostgreSQL full-text search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
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

    def query(
        self,
        *,
        query: str,
        top_k: int = 5,
        pdf_id: Optional[UUID] = None,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
    ) -> List[LexicalResult]:
        if top_k <= 0 or not query.strip():
            return []

        if pdf_id is None and (source_type is None or source_id is None):
            raise ValueError(
                "LexicalSearch.query requires either pdf_id or source_type/source_id."
            )

        if pdf_id is not None:
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
            params = {
                "language": self._language,
                "query": query,
                "pdf_id": str(pdf_id),
                "limit": top_k,
            }
        else:
            stmt = text(
                """
                SELECT
                    id AS chunk_id,
                    chunk_text,
                    ts_rank(search_vector, plainto_tsquery(:language, :query)) AS rank
                FROM unified_chunks
                WHERE source_type = :source_type
                  AND source_id = :source_id
                  AND search_vector @@ plainto_tsquery(:language, :query)
                ORDER BY rank DESC
                LIMIT :limit
                """
            )
            params = {
                "language": self._language,
                "query": query,
                "source_type": source_type,
                "source_id": source_id,
                "limit": top_k,
            }

        with self._engine.connect() as connection:
            rows = connection.execute(stmt, params).all()

        results: List[LexicalResult] = []
        for row in rows:
            rank = float(row.rank) if row.rank is not None else 0.0
            results.append(
                LexicalResult(chunk_id=row.chunk_id, snippet=row.chunk_text, rank=rank)
            )
        return results


__all__ = ["LexicalSearch", "LexicalResult"]
