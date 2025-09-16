"""Models shared across general-purpose RAG pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID


@dataclass
class GeneralPipelineChunk:
    """Chunk selected by the general pipeline for answer synthesis."""

    chunk_id: UUID
    text: str
    score: float
    source: str
    citation: Optional[Dict[str, Any]] = None
    retriever_score: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.text or not self.text.strip():
            raise ValueError("chunk text cannot be empty")
        if self.score is None:
            raise ValueError("chunk score is required")


@dataclass
class GeneralPipelineOutput:
    """Aggregated output from the general retrieval pipeline."""

    query: str
    pdf_id: UUID
    sorted_chunks: List[GeneralPipelineChunk]
    metadata: Dict[str, Any]

    @classmethod
    def from_chunks(
        cls,
        *,
        query: str,
        pdf_id: UUID,
        chunks: List[GeneralPipelineChunk],
    ) -> "GeneralPipelineOutput":
        ordered = sorted(chunks, key=lambda chunk: chunk.score, reverse=True)
        total_chunks = len(ordered)
        avg_score = (
            sum(chunk.score for chunk in ordered) / total_chunks if ordered else 0.0
        )
        metadata = {
            "total_chunks": total_chunks,
            "avg_score": avg_score,
        }
        return cls(query=query, pdf_id=pdf_id, sorted_chunks=ordered, metadata=metadata)
