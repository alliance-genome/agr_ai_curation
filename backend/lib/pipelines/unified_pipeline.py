"""Source-agnostic retrieval pipeline shared across all document types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID

from lib.hybrid_search import HybridSearch
from lib.reranker import Reranker, RerankerCandidate, RerankedResult
from lib.pipelines.document_source import DocumentSource, IndexStatus
from lib.pipelines.query_embedder import QueryEmbedderProtocol


@dataclass
class UnifiedPipelineChunk:
    """Chunk selected by the unified pipeline."""

    chunk_id: UUID
    text: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    citation: Optional[Dict[str, Any]] = None


@dataclass
class UnifiedPipelineOutput:
    """Full response from a unified pipeline search."""

    source_type: str
    source_id: str
    query: str
    chunks: List[UnifiedPipelineChunk]
    metadata: Dict[str, Any] = field(default_factory=dict)


class UnifiedRAGPipeline:
    """Single retrieval pipeline that supports multiple document sources."""

    def __init__(
        self,
        *,
        hybrid_search: HybridSearch,
        reranker: Reranker,
        query_embedder: QueryEmbedderProtocol,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._hybrid_search = hybrid_search
        self._reranker = reranker
        self._query_embedder = query_embedder
        self._config = config or {
            "vector_top_k": 50,
            "lexical_top_k": 50,
            "max_results": 20,
            "rerank_top_k": 10,
            "apply_mmr": True,
            "mmr_lambda": 0.7,
            "source_overrides": {},
        }
        self._sources: Dict[str, DocumentSource] = {}

    def register_source(self, source: DocumentSource) -> None:
        registration = source.registration()
        self._sources[registration.source_type] = source

    def get_source(self, source_type: str) -> DocumentSource:
        if source_type not in self._sources:
            raise ValueError(f"Unknown source type: {source_type}")
        return self._sources[source_type]

    async def ensure_index_ready(
        self, *, source_type: str, source_id: str
    ) -> IndexStatus:
        source = self.get_source(source_type)
        status = await source.index_status(source_id=source_id)
        if status == IndexStatus.NOT_INDEXED:
            status = await source.ingest(source_id=source_id)
        return status

    async def search(
        self,
        *,
        source_type: str,
        source_id: str,
        query: str,
        context: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> UnifiedPipelineOutput:
        source = self.get_source(source_type)

        # Merge configuration overrides (global -> per-source -> request)
        config = dict(self._config)
        source_overrides = self._config.get("source_overrides", {}).get(source_type, {})
        config.update(source_overrides)
        if config_overrides:
            config.update(config_overrides)

        query_embedding = self._query_embedder(query)
        search_response = self._hybrid_search.query(
            embedding=query_embedding,
            query=query,
            vector_top_k=config.get("vector_top_k", 50),
            lexical_top_k=config.get("lexical_top_k", 50),
            max_results=config.get("max_results", 20),
            vector_weight=config.get("vector_weight", 0.6),
            source_type=source_type,
            source_id=source_id,
        )

        result_lookup = {result.chunk_id: result for result in search_response.results}
        candidates: List[RerankerCandidate] = []
        for result in search_response.results:
            metadata = {
                "source": result.source,
                "page": result.page,
                "section": result.section,
            }
            if result.metadata:
                metadata["chunk_metadata"] = result.metadata
            candidates.append(
                RerankerCandidate(
                    chunk_id=result.chunk_id,
                    text=result.text,
                    retriever_score=result.score,
                    metadata=metadata,
                )
            )

        if context:
            candidates = self._apply_context_boost(
                candidates,
                context=context,
                boost_factor=config.get("context_boost", 1.5),
            )

        reranked: List[RerankedResult] = self._reranker.rerank(
            query=query,
            candidates=candidates,
            top_k=config.get("rerank_top_k", 10),
            apply_mmr=config.get("apply_mmr", True),
            lambda_param=config.get("mmr_lambda", 0.7),
        )

        final_chunks: List[UnifiedPipelineChunk] = []
        for reranked_chunk in reranked:
            original = result_lookup.get(reranked_chunk.chunk_id)
            if original is None:
                continue

            metadata = dict(reranked_chunk.metadata)
            if original.metadata:
                metadata.setdefault("chunk_metadata", original.metadata)

            citation = source.format_citation(metadata)

            final_chunks.append(
                UnifiedPipelineChunk(
                    chunk_id=reranked_chunk.chunk_id,
                    text=original.text,
                    score=reranked_chunk.combined_score,
                    metadata=metadata,
                    citation=citation,
                )
            )

        pipeline_metadata = {
            "total_candidates": len(candidates),
            "final_results": len(final_chunks),
            "metrics": search_response.metrics.__dict__,
            "config": {
                key: value
                for key, value in config.items()
                if key
                in {
                    "vector_top_k",
                    "lexical_top_k",
                    "max_results",
                    "rerank_top_k",
                    "apply_mmr",
                    "mmr_lambda",
                }
            },
        }

        return UnifiedPipelineOutput(
            source_type=source_type,
            source_id=source_id,
            query=query,
            chunks=final_chunks,
            metadata=pipeline_metadata,
        )

    @staticmethod
    def _apply_context_boost(
        candidates: List[RerankerCandidate],
        *,
        context: str,
        boost_factor: float = 1.5,
    ) -> List[RerankerCandidate]:
        if boost_factor <= 1.0:
            return candidates

        context_lower = context.lower()
        boosted: List[RerankerCandidate] = []
        for candidate in candidates:
            modified = RerankerCandidate(
                chunk_id=candidate.chunk_id,
                text=candidate.text,
                retriever_score=candidate.retriever_score,
                embedding=candidate.embedding,
                metadata=dict(candidate.metadata),
            )
            first_terms = modified.text.lower().split()[:10]
            if any(term in context_lower for term in first_terms):
                modified.metadata["context_boost"] = boost_factor
                modified.retriever_score *= boost_factor
            boosted.append(modified)
        return boosted


__all__ = [
    "UnifiedRAGPipeline",
    "UnifiedPipelineOutput",
    "UnifiedPipelineChunk",
]
