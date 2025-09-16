"""General-purpose orchestrator for PDF Q&A."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List
from uuid import UUID

from .pipeline_models import GeneralPipelineChunk, GeneralPipelineOutput


@dataclass
class OrchestratorConfig:
    confidence_threshold: float = 0.5
    top_k: int = 5


@dataclass
class OrchestratorResult:
    answer: str
    citations: List[Dict[str, Any]]
    metadata: Dict[str, Any] = field(default_factory=dict)


class GeneralOrchestrator:
    """Coordinates retrieval pipeline and LLM answer generation."""

    def __init__(
        self,
        *,
        pipeline: Any,
        llm: Any,
        config: OrchestratorConfig | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._llm = llm
        self._config = config or OrchestratorConfig()

    async def answer_question(self, *, pdf_id: UUID, query: str) -> OrchestratorResult:
        pipeline_output: GeneralPipelineOutput = await self._pipeline.run(
            pdf_id=pdf_id, query=query
        )

        eligible_chunks = [
            chunk
            for chunk in pipeline_output.sorted_chunks[: self._config.top_k]
            if chunk.score >= self._config.confidence_threshold
        ]
        if not eligible_chunks:
            eligible_chunks = pipeline_output.sorted_chunks[: self._config.top_k]

        context_prompt = self._build_prompt(query=query, chunks=eligible_chunks)
        answer = await self._llm.generate(
            prompt=context_prompt, context=eligible_chunks
        )

        citations = [chunk.citation for chunk in eligible_chunks if chunk.citation]
        metadata = {
            "chunks_used": len(eligible_chunks),
            "query": pipeline_output.query,
            "pdf_id": str(pipeline_output.pdf_id),
            "pipeline_metadata": pipeline_output.metadata,
        }

        return OrchestratorResult(answer=answer, citations=citations, metadata=metadata)

    def _build_prompt(self, *, query: str, chunks: List[GeneralPipelineChunk]) -> str:
        joined_chunks = "\n\n".join(
            f"Chunk {index + 1} (score={chunk.score:.2f}, source={chunk.source}):\n{chunk.text}"
            for index, chunk in enumerate(chunks)
        )
        return (
            "You are a helpful assistant answering questions based on the provided chunks.\n"
            "Use evidence from the chunks when answering and cite support when possible.\n\n"
            f"Question: {query}\n\n"
            f"Chunks:\n{joined_chunks}\n\n"
            "Answer:"
        )
