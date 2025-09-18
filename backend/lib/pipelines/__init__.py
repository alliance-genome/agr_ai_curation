"""Pipeline factories and helpers for the RAG system."""

from .general_pipeline import GeneralPipeline, build_general_pipeline
from .query_embedder import OpenAIQueryEmbedder, QueryEmbedderProtocol
from .document_source import DocumentSource, IndexStatus, SourceRegistration
from .unified_pipeline import (
    UnifiedRAGPipeline,
    UnifiedPipelineChunk,
    UnifiedPipelineOutput,
)

__all__ = [
    "GeneralPipeline",
    "build_general_pipeline",
    "OpenAIQueryEmbedder",
    "QueryEmbedderProtocol",
    "DocumentSource",
    "IndexStatus",
    "SourceRegistration",
    "UnifiedRAGPipeline",
    "UnifiedPipelineChunk",
    "UnifiedPipelineOutput",
]
