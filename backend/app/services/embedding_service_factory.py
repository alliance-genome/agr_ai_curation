"""Factory for creating embedding service instances."""

from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from app.config import get_settings
from app.database import SessionLocal
from app.services.settings_lookup import get_setting_value
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

    base_config = EmbeddingModelConfig(
        name=get_setting_value(
            "embedding_model_name", settings.embedding_model_name, cast=str
        ),
        dimensions=get_setting_value(
            "embedding_dimensions", settings.embedding_dimensions, cast=int
        ),
        default_version=get_setting_value(
            "embedding_model_version", settings.embedding_model_version, cast=str
        ),
        max_batch_size=get_setting_value(
            "embedding_max_batch_size", settings.embedding_max_batch_size, cast=int
        ),
        default_batch_size=get_setting_value(
            "embedding_default_batch_size",
            settings.embedding_default_batch_size,
            cast=int,
        ),
    )

    models = {base_config.name: base_config}

    ontology_model_name = get_setting_value(
        "ontology_embedding_model_name",
        settings.ontology_embedding_model_name or base_config.name,
        cast=str,
    )

    ontology_config = EmbeddingModelConfig(
        name=ontology_model_name or base_config.name,
        dimensions=get_setting_value(
            "ontology_embedding_dimensions",
            settings.ontology_embedding_dimensions or base_config.dimensions,
            cast=int,
        ),
        default_version=get_setting_value(
            "ontology_embedding_model_version",
            settings.ontology_embedding_model_version or base_config.default_version,
            cast=str,
        ),
        max_batch_size=get_setting_value(
            "ontology_embedding_max_batch_size",
            settings.ontology_embedding_max_batch_size or base_config.max_batch_size,
            cast=int,
        ),
        default_batch_size=get_setting_value(
            "ontology_embedding_batch_size",
            settings.ontology_embedding_batch_size or base_config.default_batch_size,
            cast=int,
        ),
    )

    models[ontology_config.name] = ontology_config

    return EmbeddingService(
        session_factory=SessionLocal,
        embedding_client=client,
        models=models,
    )


__all__ = ["get_embedding_service", "OpenAIEmbeddingClient"]
