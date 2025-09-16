"""General-purpose orchestrator for PDF Q&A using PydanticAI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List
from uuid import UUID

from pydantic import BaseModel
from pydantic_ai import Agent

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


class GeneralAnswer(BaseModel):
    answer: str


class OrchestratorDeps(BaseModel):
    query: str
    context: str


def build_pydantic_agent(
    model_name: str, *, temperature: float, max_tokens: int
) -> Agent[OrchestratorDeps, GeneralAnswer]:
    system_prompt = (
        "You are a helpful scientific assistant."
        " Use the provided context passages from a PDF document to answer"
        " the user's question. Always ground your answer in the context and"
        " include evidence-based citations in the answer text (e.g., [page 3])."
    )
    return Agent(
        model_name,
        deps_type=OrchestratorDeps,
        output_type=GeneralAnswer,
        system_prompt=system_prompt,
        model_settings={
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        },
    )


class GeneralOrchestrator:
    """Coordinates retrieval pipeline and PydanticAI answer generation."""

    def __init__(
        self,
        *,
        pipeline: Any,
        agent: Agent[OrchestratorDeps, GeneralAnswer],
        config: OrchestratorConfig | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._agent = agent
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

        prompt = self._build_prompt(query=query, chunks=eligible_chunks)
        deps = OrchestratorDeps(
            query=query, context=self._format_context(eligible_chunks)
        )
        run_result = await self._agent.run(prompt, deps=deps)
        answer_text = run_result.output.answer

        citations = [chunk.citation for chunk in eligible_chunks if chunk.citation]
        metadata = {
            "chunks_used": len(eligible_chunks),
            "query": pipeline_output.query,
            "pdf_id": str(pipeline_output.pdf_id),
            "pipeline_metadata": pipeline_output.metadata,
        }

        return OrchestratorResult(
            answer=answer_text, citations=citations, metadata=metadata
        )

    def _build_prompt(self, *, query: str, chunks: List[GeneralPipelineChunk]) -> str:
        context = self._format_context(chunks)
        return (
            "Question: "
            + query
            + "\n\nContext:\n"
            + context
            + "\n\nProvide a concise answer grounded in the context."
        )

    @staticmethod
    def _format_context(chunks: List[GeneralPipelineChunk]) -> str:
        lines = []
        for index, chunk in enumerate(chunks, start=1):
            citation_info = chunk.citation or {}
            page = citation_info.get("page", "?")
            section = citation_info.get("section", "")
            lines.append(
                f"Chunk {index} (score={chunk.score:.2f}, page={page}, section={section}):\n{chunk.text}"
            )
        return "\n\n".join(lines)


__all__ = [
    "GeneralOrchestrator",
    "OrchestratorConfig",
    "OrchestratorResult",
    "build_pydantic_agent",
]
