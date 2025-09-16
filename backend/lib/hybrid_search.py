"""Hybrid search orchestrator combining vector and lexical signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .lexical_search import LexicalResult, LexicalSearch
from .vector_search import SearchResult, VectorSearch


@dataclass
class HybridSearchMetrics:
    """Telemetry describing how the hybrid query behaved."""

    vector_candidates: int
    lexical_candidates: int
    overlap_count: int
    final_count: int


@dataclass
class HybridSearchResult:
    """Single chunk produced by hybrid retrieval."""

    chunk_id: UUID
    text: str
    score: float
    source: Literal["vector", "lexical", "both"]
    page: Optional[int]
    section: Optional[str]
    is_table: bool
    is_figure: bool
    vector_distance: Optional[float] = None
    lexical_rank: Optional[float] = None


@dataclass
class HybridSearchResponse:
    """Full response from the hybrid search orchestrator."""

    results: List[HybridSearchResult]
    metrics: HybridSearchMetrics


@dataclass
class _ChunkMetadata:
    text: Optional[str]
    page_start: Optional[int]
    page_end: Optional[int]
    section: Optional[str]
    is_table: bool
    is_figure: bool


@dataclass
class _Candidate:
    chunk_id: UUID
    vector_distance: Optional[float] = None
    lexical_rank: Optional[float] = None
    vector_score: float = 0.0
    lexical_score: float = 0.0
    combined_score: float = 0.0
    lexical_snippet: Optional[str] = None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _distance_to_similarity(distance: Optional[float]) -> float:
    if distance is None:
        return 0.0
    return 1.0 / (1.0 + float(distance))


class HybridSearch:
    """Orchestrates vector and lexical retrieval with score merging."""

    def __init__(
        self,
        *,
        engine: Engine,
        vector_model: str,
        lexical_language: str = "english",
    ) -> None:
        self._engine = engine
        self._vector_search = VectorSearch(engine, model_name=vector_model)
        self._lexical_search = LexicalSearch(engine, language=lexical_language)

    def query(
        self,
        *,
        pdf_id: UUID,
        embedding: Sequence[float],
        query: str,
        vector_top_k: int = 50,
        lexical_top_k: int = 50,
        max_results: int = 20,
        vector_weight: float = 0.6,
    ) -> HybridSearchResponse:
        if max_results <= 0:
            empty_metrics = HybridSearchMetrics(
                vector_candidates=0,
                lexical_candidates=0,
                overlap_count=0,
                final_count=0,
            )
            return HybridSearchResponse(results=[], metrics=empty_metrics)

        weight = _clamp(vector_weight, 0.0, 1.0)

        vector_results: List[SearchResult] = []
        if vector_top_k > 0:
            vector_results = self._vector_search.query(
                pdf_id=pdf_id,
                embedding=embedding,
                top_k=vector_top_k,
            )

        lexical_results: List[LexicalResult] = []
        if lexical_top_k > 0:
            lexical_results = self._lexical_search.query(
                pdf_id=pdf_id,
                query=query,
                top_k=lexical_top_k,
            )

        candidates: Dict[UUID, _Candidate] = {}
        vector_scores: List[float] = []
        lexical_scores: List[float] = []

        for result in vector_results:
            distance = float(result.distance)
            candidate = candidates.setdefault(
                result.chunk_id, _Candidate(result.chunk_id)
            )
            candidate.vector_distance = distance
            score = _distance_to_similarity(distance)
            candidate.vector_score = score
            vector_scores.append(score)

        for result in lexical_results:
            rank = float(result.rank)
            candidate = candidates.setdefault(
                result.chunk_id, _Candidate(result.chunk_id)
            )
            candidate.lexical_rank = rank
            candidate.lexical_score = max(rank, 0.0)
            candidate.lexical_snippet = result.snippet
            lexical_scores.append(candidate.lexical_score)

        max_vector = max(vector_scores) if vector_scores else 0.0
        max_lexical = max(lexical_scores) if lexical_scores else 0.0

        has_vector = max_vector > 0.0
        has_lexical = max_lexical > 0.0

        vector_weight_final = weight if has_vector else 0.0
        lexical_weight_final = (1.0 - weight) if has_lexical else 0.0
        weight_total = vector_weight_final + lexical_weight_final
        if weight_total == 0:
            weight_total = 1.0

        for candidate in candidates.values():
            vector_component = (
                candidate.vector_score / max_vector
                if has_vector and max_vector > 0
                else 0.0
            )
            lexical_component = (
                candidate.lexical_score / max_lexical
                if has_lexical and max_lexical > 0
                else 0.0
            )

            combined = (
                vector_weight_final * vector_component
                + lexical_weight_final * lexical_component
            ) / weight_total
            candidate.combined_score = combined

        sorted_candidates = sorted(
            candidates.values(),
            key=lambda item: (
                -item.combined_score,
                -(item.lexical_rank or 0.0),
                (
                    item.vector_distance
                    if item.vector_distance is not None
                    else float("inf")
                ),
            ),
        )

        top_candidates = sorted_candidates[:max_results]
        chunk_ids = [candidate.chunk_id for candidate in top_candidates]
        metadata_map = self._fetch_chunk_metadata(chunk_ids)

        results: List[HybridSearchResult] = []
        for candidate in top_candidates:
            metadata = metadata_map.get(candidate.chunk_id)
            text_value = (
                (metadata.text if metadata else None) or candidate.lexical_snippet or ""
            )
            if not text_value:
                continue

            if (
                candidate.vector_distance is not None
                and candidate.lexical_rank is not None
            ):
                source: Literal["vector", "lexical", "both"] = "both"
            elif candidate.vector_distance is not None:
                source = "vector"
            else:
                source = "lexical"

            results.append(
                HybridSearchResult(
                    chunk_id=candidate.chunk_id,
                    text=text_value,
                    score=candidate.combined_score,
                    source=source,
                    page=metadata.page_start if metadata else None,
                    section=metadata.section if metadata else None,
                    is_table=metadata.is_table if metadata else False,
                    is_figure=metadata.is_figure if metadata else False,
                    vector_distance=candidate.vector_distance,
                    lexical_rank=candidate.lexical_rank,
                )
            )

        vector_ids = {result.chunk_id for result in vector_results}
        lexical_ids = {result.chunk_id for result in lexical_results}

        metrics = HybridSearchMetrics(
            vector_candidates=len(vector_results),
            lexical_candidates=len(lexical_results),
            overlap_count=len(vector_ids & lexical_ids),
            final_count=len(results),
        )

        return HybridSearchResponse(results=results, metrics=metrics)

    def _fetch_chunk_metadata(
        self, chunk_ids: Sequence[UUID]
    ) -> Dict[UUID, _ChunkMetadata]:
        if not chunk_ids:
            return {}

        placeholders = ", ".join(f":id_{idx}" for idx in range(len(chunk_ids)))
        stmt = text(
            ""
            "SELECT id, text, page_start, page_end, section_path, is_table, is_figure\n"
            "FROM pdf_chunks\n"
            "WHERE id IN (" + placeholders + ")"
        )
        params = {f"id_{idx}": str(chunk_id) for idx, chunk_id in enumerate(chunk_ids)}

        with self._engine.connect() as connection:
            rows = connection.execute(stmt, params).all()

        metadata: Dict[UUID, _ChunkMetadata] = {}
        for row in rows:
            metadata[row.id] = _ChunkMetadata(
                text=row.text,
                page_start=row.page_start,
                page_end=row.page_end,
                section=row.section_path,
                is_table=bool(row.is_table),
                is_figure=bool(row.is_figure),
            )

        return metadata


__all__ = [
    "HybridSearch",
    "HybridSearchMetrics",
    "HybridSearchResponse",
    "HybridSearchResult",
]
