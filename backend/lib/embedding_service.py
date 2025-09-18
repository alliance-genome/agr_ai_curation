"""Embedding service with model versioning and persistence to Postgres."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import PDFDocument, PDFChunk, PDFEmbedding, UnifiedChunk


@dataclass(frozen=True)
class EmbeddingModelConfig:
    """Configuration describing an embedding model."""

    name: str
    dimensions: int
    default_version: str
    max_batch_size: int
    default_batch_size: int


class EmbeddingService:
    """Generates and persists embeddings for PDF chunks with versioning support."""

    def __init__(
        self,
        *,
        session_factory,
        embedding_client,
        models: Dict[str, EmbeddingModelConfig],
    ) -> None:
        if not models:
            raise ValueError("At least one embedding model must be configured")
        self._session_factory = session_factory
        self._client = embedding_client
        self._models = models

    def list_models(self):
        """Return configured model metadata."""
        return list(self._models.values())

    def embed_unified_chunks(
        self,
        *,
        source_type: str,
        source_id: str,
        model_name: str,
        batch_size: int | None = None,
        force: bool = False,
    ) -> Dict[str, int | str]:
        config = self._models.get(model_name)
        if config is None:
            raise ValueError(f"Unknown embedding model '{model_name}'")

        if batch_size is not None:
            if batch_size <= 0:
                raise ValueError("batch_size must be positive")
            if batch_size > config.max_batch_size:
                raise ValueError("batch_size exceeds configured max_batch_size")
            effective_batch_size = batch_size
        else:
            effective_batch_size = config.default_batch_size

        if effective_batch_size <= 0:
            effective_batch_size = config.max_batch_size

        effective_batch_size = min(effective_batch_size, config.max_batch_size)

        with self._session_factory() as session:
            chunks: List[UnifiedChunk] = (
                session.query(UnifiedChunk)
                .filter(
                    UnifiedChunk.source_type == source_type,
                    UnifiedChunk.source_id == source_id,
                )
                .order_by(UnifiedChunk.created_at.asc())
                .all()
            )

            if not chunks:
                return {
                    "embedded": 0,
                    "skipped": 0,
                    "model": model_name,
                    "source_type": source_type,
                    "source_id": source_id,
                }

            target_chunks = [
                chunk for chunk in chunks if force or chunk.embedding is None
            ]

            if not target_chunks:
                return {
                    "embedded": 0,
                    "skipped": len(chunks),
                    "model": model_name,
                    "source_type": source_type,
                    "source_id": source_id,
                }

            texts = [chunk.chunk_text for chunk in target_chunks]
            vectors = self._embed_in_batches(texts, model_name, effective_batch_size)

            if len(vectors) != len(target_chunks):
                raise ValueError(
                    "Embedding client returned unexpected number of vectors"
                )

            for chunk, vector in zip(target_chunks, vectors, strict=True):
                chunk.embedding = vector

            session.commit()

            return {
                "embedded": len(target_chunks),
                "skipped": len(chunks) - len(target_chunks),
                "model": model_name,
                "source_type": source_type,
                "source_id": source_id,
            }

    def embed_pdf(
        self,
        *,
        pdf_id: UUID,
        model_name: str,
        version: str | None = None,
        batch_size: int | None = None,
        force: bool = False,
    ) -> Dict[str, int | str]:
        config = self._models.get(model_name)
        if config is None:
            raise ValueError(f"Unknown embedding model '{model_name}'")

        target_version = version or config.default_version
        if batch_size is not None:
            if batch_size <= 0:
                raise ValueError("batch_size must be positive")
            if batch_size > config.max_batch_size:
                raise ValueError("batch_size exceeds configured max_batch_size")
            effective_batch_size = batch_size
        else:
            effective_batch_size = config.default_batch_size

        if effective_batch_size <= 0:
            effective_batch_size = config.max_batch_size

        effective_batch_size = min(effective_batch_size, config.max_batch_size)

        with self._session_factory() as session:
            document = session.get(PDFDocument, pdf_id)
            if document is None:
                raise ValueError(f"PDF document {pdf_id} not found")

            chunks: List[PDFChunk] = (
                session.query(PDFChunk)
                .filter(PDFChunk.pdf_id == pdf_id)
                .order_by(PDFChunk.chunk_index.asc())
                .all()
            )

            if not chunks:
                return {
                    "embedded": 0,
                    "skipped": 0,
                    "model": model_name,
                    "version": target_version,
                }

            existing: Dict[UUID, PDFEmbedding] = {
                row.chunk_id: row
                for row in session.query(PDFEmbedding)
                .filter(
                    PDFEmbedding.pdf_id == pdf_id,
                    PDFEmbedding.model_name == model_name,
                )
                .all()
            }

            if (
                not force
                and existing
                and len(existing) == len(chunks)
                and all(
                    row.model_version == target_version for row in existing.values()
                )
            ):
                return {
                    "embedded": 0,
                    "skipped": len(chunks),
                    "model": model_name,
                    "version": target_version,
                }

            if existing:
                (
                    session.query(PDFEmbedding)
                    .filter(
                        PDFEmbedding.pdf_id == pdf_id,
                        PDFEmbedding.model_name == model_name,
                    )
                    .delete(synchronize_session=False)
                )
                session.flush()

            texts = [chunk.text for chunk in chunks]
            vectors = self._embed_in_batches(texts, model_name, effective_batch_size)

            if len(vectors) != len(chunks):
                raise ValueError(
                    "Embedding client returned unexpected number of vectors"
                )

            chunk_vector_pairs = list(zip(chunks, vectors, strict=True))
            chunk_vector_pairs.sort(key=lambda pair: str(pair[0].id))

            for chunk, vector in chunk_vector_pairs:
                session.add(
                    PDFEmbedding(
                        pdf_id=pdf_id,
                        chunk_id=chunk.id,
                        embedding=vector,
                        model_name=model_name,
                        model_version=target_version,
                        dimensions=config.dimensions,
                    )
                )

            document.embeddings_generated = True
            document.embedding_models = _upsert_model_entry(
                document.embedding_models, model_name, target_version
            )

            session.commit()

            return {
                "embedded": len(chunks),
                "skipped": 0,
                "model": model_name,
                "version": target_version,
            }

    def _embed_in_batches(
        self,
        texts: Sequence[str],
        model_name: str,
        batch_size: int,
    ) -> List[Sequence[float]]:
        vectors: List[Sequence[float]] = []
        if batch_size <= 0:
            batch_size = len(texts)

        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            embeddings = self._client.embed_texts(batch, model=model_name)
            vectors.extend(embeddings)

        return vectors


def _upsert_model_entry(
    payload: Iterable[Dict[str, str]] | None,
    model_name: str,
    version: str,
) -> List[Dict[str, str]]:
    entries = list(payload or [])
    for entry in entries:
        if entry.get("model") == model_name:
            entry["version"] = version
            return entries

    entries.append({"model": model_name, "version": version})
    return entries


__all__ = ["EmbeddingModelConfig", "EmbeddingService"]
