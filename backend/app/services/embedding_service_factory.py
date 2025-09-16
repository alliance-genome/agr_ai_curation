"""Factory for creating embedding service instances."""

from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from app.config import get_settings
from app.database import SessionLocal
from lib.embedding_service import EmbeddingModelConfig, EmbeddingService


class OpenAIEmbeddingClient:
    """Thin wrapper around OpenAI embeddings API."""

    def __init__(self, *, api_key: str) -> None:
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY must be configured to generate embeddings."
            )
        self._client = OpenAI(api_key=api_key)

    def embed_texts(self, texts, *, model: str):
        if not texts:
            return []
        response = self._client.embeddings.create(model=model, input=list(texts))
        return [item.embedding for item in response.data]


@lru_cache
def get_embedding_service() -> EmbeddingService:
    settings = get_settings()
    client = OpenAIEmbeddingClient(api_key=settings.openai_api_key)

    model_config = EmbeddingModelConfig(
        name=settings.embedding_model_name,
        dimensions=settings.embedding_dimensions,
        default_version=settings.embedding_model_version,
        max_batch_size=settings.embedding_max_batch_size,
        default_batch_size=settings.embedding_default_batch_size,
    )

    return EmbeddingService(
        session_factory=SessionLocal,
        embedding_client=client,
        models={model_config.name: model_config},
    )


__all__ = ["get_embedding_service", "OpenAIEmbeddingClient"]
