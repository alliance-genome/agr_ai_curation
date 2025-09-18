"""Shared query embedding helpers for RAG pipelines."""

from __future__ import annotations

from typing import Protocol, Sequence

from openai import OpenAI

from app.config import get_settings


class QueryEmbedderProtocol(Protocol):
    """Lightweight protocol describing a callable query embedder."""

    def __call__(self, query: str) -> Sequence[float]: ...


class OpenAIQueryEmbedder:
    """Embed queries using the configured OpenAI embeddings model."""

    def __init__(self, *, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        resolved_api_key = api_key or settings.openai_api_key
        resolved_model = model or settings.embedding_model_name

        if not resolved_api_key:
            raise RuntimeError("OPENAI_API_KEY must be configured to embed queries.")

        self._client = OpenAI(api_key=resolved_api_key)
        self._model = resolved_model

    def __call__(self, query: str) -> Sequence[float]:
        response = self._client.embeddings.create(model=self._model, input=[query])
        return response.data[0].embedding


__all__ = ["QueryEmbedderProtocol", "OpenAIQueryEmbedder"]
