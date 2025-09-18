"""Factory helpers for building the unified RAG pipeline."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal, engine as default_engine
from app.services.document_sources import OntologyDocumentSource, PDFDocumentSource
from app.services.embedding_service_factory import get_embedding_service
from lib.hybrid_search import HybridSearch
from lib.pipelines import (
    OpenAIQueryEmbedder,
    UnifiedRAGPipeline,
)
from lib.reranker import Reranker


@lru_cache
def get_unified_pipeline() -> UnifiedRAGPipeline:
    settings = get_settings()

    hybrid_search = HybridSearch(
        engine=default_engine,
        vector_model=settings.embedding_model_name,
    )
    reranker = Reranker()
    query_embedder = OpenAIQueryEmbedder(
        api_key=settings.openai_api_key,
        model=settings.embedding_model_name,
    )

    config = {
        "vector_top_k": settings.hybrid_vector_k,
        "lexical_top_k": settings.hybrid_lexical_k,
        "max_results": settings.hybrid_max_results,
        "rerank_top_k": settings.rag_rerank_top_k,
        "apply_mmr": True,
        "mmr_lambda": settings.mmr_lambda,
        "source_overrides": {},
    }

    pipeline = UnifiedRAGPipeline(
        hybrid_search=hybrid_search,
        reranker=reranker,
        query_embedder=query_embedder,
        config=config,
    )

    pipeline.register_source(PDFDocumentSource(SessionLocal))

    ontology_path = Path(settings.disease_ontology_path)
    if ontology_path.exists():
        pipeline.register_source(
            OntologyDocumentSource(
                ontology_type="disease",
                data_path=ontology_path,
                session_factory=SessionLocal,
                embedding_service=get_embedding_service(),
                embedding_model=settings.embedding_model_name,
            )
        )

    return pipeline


__all__ = ["get_unified_pipeline"]
