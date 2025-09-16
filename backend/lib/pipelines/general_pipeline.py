"""General retrieval pipeline combining hybrid search and reranking."""

from __future__ import annotations

from typing import Dict, List, Sequence
from uuid import UUID

from openai import OpenAI

from app.config import get_settings
from app.database import engine as default_engine
from lib.hybrid_search import HybridSearch, HybridSearchResult
from lib.reranker import (
    Reranker,
    RerankerCandidate,
    RerankedResult,
)

from app.agents.pipeline_models import GeneralPipelineChunk, GeneralPipelineOutput


class QueryEmbedderProtocol:
    def __call__(self, query: str) -> Sequence[float]:  # pragma: no cover - protocol
        raise NotImplementedError


class OpenAIQueryEmbedder(QueryEmbedderProtocol):
    """Embed queries using the OpenAI embeddings API."""

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be configured to embed queries.")
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def __call__(self, query: str) -> Sequence[float]:
        response = self._client.embeddings.create(model=self._model, input=[query])
        return response.data[0].embedding


class GeneralPipeline:
    """Coordinate hybrid search and reranking for a given PDF."""

    def __init__(
        self,
        *,
        hybrid_search: HybridSearch,
        reranker: Reranker,
        query_embedder: QueryEmbedderProtocol,
        vector_top_k: int,
        lexical_top_k: int,
        max_results: int,
        rerank_top_k: int,
        mmr_lambda: float,
    ) -> None:
        self._hybrid_search = hybrid_search
        self._reranker = reranker
        self._query_embedder = query_embedder
        self._vector_top_k = max(1, vector_top_k)
        self._lexical_top_k = max(1, lexical_top_k)
        self._max_results = max(1, max_results)
        self._rerank_top_k = max(1, rerank_top_k)
        self._mmr_lambda = mmr_lambda

    async def run(self, *, pdf_id: UUID, query: str) -> GeneralPipelineOutput:
        embedding = self._query_embedder(query)
        search_response = self._hybrid_search.query(
            pdf_id=pdf_id,
            embedding=embedding,
            query=query,
            vector_top_k=self._vector_top_k,
            lexical_top_k=self._lexical_top_k,
            max_results=self._max_results,
        )

        result_map: Dict[UUID, HybridSearchResult] = {
            item.chunk_id: item for item in search_response.results
        }

        candidates = [
            RerankerCandidate(
                chunk_id=item.chunk_id,
                text=item.text,
                retriever_score=item.score,
                metadata={
                    "page": item.page,
                    "source": item.source,
                    "section": item.section,
                },
            )
            for item in search_response.results
        ]

        reranked = self._reranker.rerank(
            query=query,
            candidates=candidates,
            top_k=self._rerank_top_k,
            apply_mmr=True,
            lambda_param=self._mmr_lambda,
        )

        general_chunks = self._convert_to_general_chunks(reranked, result_map)
        return GeneralPipelineOutput.from_chunks(
            query=query, pdf_id=pdf_id, chunks=general_chunks
        )

    def _convert_to_general_chunks(
        self,
        reranked: List[RerankedResult],
        result_map: Dict[UUID, HybridSearchResult],
    ) -> List[GeneralPipelineChunk]:
        chunks: List[GeneralPipelineChunk] = []
        for item in reranked:
            result = result_map.get(item.chunk_id)
            if result is None:
                continue
            metadata = dict(item.metadata)
            chunks.append(
                GeneralPipelineChunk(
                    chunk_id=result.chunk_id,
                    text=result.text,
                    score=item.combined_score,
                    source=result.source,
                    citation={
                        "page": result.page,
                        "section": result.section,
                    },
                    retriever_score=metadata.get("retriever_score", result.score),
                    metadata=metadata,
                )
            )
        return chunks


def build_general_pipeline(
    *,
    hybrid_search: HybridSearch | None = None,
    reranker: Reranker | None = None,
    query_embedder: QueryEmbedderProtocol | None = None,
) -> GeneralPipeline:
    settings = get_settings()

    hybrid_search = hybrid_search or HybridSearch(
        engine=default_engine, vector_model=settings.embedding_model_name
    )
    reranker = reranker or Reranker()
    query_embedder = query_embedder or OpenAIQueryEmbedder(
        api_key=settings.openai_api_key,
        model=settings.embedding_model_name,
    )

    return GeneralPipeline(
        hybrid_search=hybrid_search,
        reranker=reranker,
        query_embedder=query_embedder,
        vector_top_k=settings.hybrid_vector_k,
        lexical_top_k=settings.hybrid_lexical_k,
        max_results=settings.hybrid_max_results,
        rerank_top_k=settings.rag_rerank_top_k,
        mmr_lambda=settings.mmr_lambda,
    )


__all__ = ["GeneralPipeline", "build_general_pipeline", "OpenAIQueryEmbedder"]
