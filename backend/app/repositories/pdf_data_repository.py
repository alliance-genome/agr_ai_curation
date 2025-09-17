"""Access utilities for PDF metadata, chunks, and lifecycle management."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import select, func, delete

from app.database import SessionLocal
from app.models import (
    PDFDocument,
    PDFChunk,
    LangGraphRun,
    LangGraphNodeRun,
    PDFEmbedding,
    EmbeddingJob,
)


EMBEDDING_PRICING_PER_1K_TOKENS = {
    "text-embedding-3-small": 0.00002,
    "text-embedding-3-large": 0.00013,
    "text-embedding-ada-002": 0.0001,
}


class PDFDataRepository:
    """Repository exposing PDF data views and lifecycle operations."""

    def __init__(self, session_factory=SessionLocal) -> None:
        self._session_factory = session_factory

    def list_documents(self, limit: int = 50) -> Iterable[PDFDocument]:
        session: Session = self._session_factory()
        try:
            stmt = (
                select(PDFDocument)
                .order_by(PDFDocument.upload_timestamp.desc())
                .limit(limit)
            )
            return session.scalars(stmt).all()
        finally:
            session.close()

    def get_document(self, pdf_id: UUID) -> Optional[PDFDocument]:
        session: Session = self._session_factory()
        try:
            return session.get(PDFDocument, pdf_id)
        finally:
            session.close()

    def list_chunks(self, pdf_id: UUID, limit: int = 1000) -> Iterable[PDFChunk]:
        session: Session = self._session_factory()
        try:
            stmt = (
                select(PDFChunk)
                .where(PDFChunk.pdf_id == pdf_id)
                .order_by(PDFChunk.chunk_index.asc())
                .limit(limit)
            )
            return session.scalars(stmt).all()
        finally:
            session.close()

    def list_langgraph_runs(self, pdf_id: UUID) -> Iterable[LangGraphRun]:
        session: Session = self._session_factory()
        try:
            stmt = (
                select(LangGraphRun)
                .where(LangGraphRun.pdf_id == pdf_id)
                .order_by(LangGraphRun.started_at.desc())
            )
            return session.scalars(stmt).all()
        finally:
            session.close()

    def list_langgraph_node_runs(
        self, graph_run_id: UUID
    ) -> Iterable[LangGraphNodeRun]:
        session: Session = self._session_factory()
        try:
            stmt = (
                select(LangGraphNodeRun)
                .where(LangGraphNodeRun.graph_run_id == graph_run_id)
                .order_by(LangGraphNodeRun.started_at.asc())
            )
            return session.scalars(stmt).all()
        finally:
            session.close()

    def list_embedding_summary(self, pdf_id: UUID) -> Iterable[dict]:
        session: Session = self._session_factory()
        try:
            stmt = (
                select(
                    PDFEmbedding.model_name,
                    func.count(PDFEmbedding.id).label("count"),
                    func.max(PDFEmbedding.created_at).label("latest_created_at"),
                    func.max(PDFEmbedding.model_version).label("model_version"),
                    func.max(PDFEmbedding.dimensions).label("dimensions"),
                    func.coalesce(func.sum(PDFChunk.token_count), 0).label(
                        "total_tokens"
                    ),
                    func.coalesce(func.avg(PDFEmbedding.processing_time_ms), 0.0).label(
                        "avg_processing_time_ms"
                    ),
                )
                .where(PDFEmbedding.pdf_id == pdf_id)
                .join(PDFChunk, PDFEmbedding.chunk_id == PDFChunk.id, isouter=True)
                .group_by(PDFEmbedding.model_name)
            )
            results = session.execute(stmt).all()
            return [self._serialize_embedding_row(row) for row in results]
        finally:
            session.close()

    def _serialize_embedding_row(self, row) -> dict:
        total_tokens = int(row.total_tokens or 0)
        dimensions = int(row.dimensions) if row.dimensions else None
        count = int(row.count or 0)
        vector_memory_bytes = None
        if dimensions and count:
            vector_memory_bytes = count * dimensions * 4  # float32 storage

        estimated_cost = self._estimate_cost(row.model_name, total_tokens)

        avg_processing_ms = (
            float(row.avg_processing_time_ms)
            if row.avg_processing_time_ms not in (None, 0)
            else None
        )

        return {
            "model_name": row.model_name,
            "count": count,
            "latest_created_at": row.latest_created_at,
            "model_version": row.model_version,
            "dimensions": dimensions,
            "total_tokens": total_tokens,
            "vector_memory_bytes": vector_memory_bytes,
            "estimated_cost_usd": estimated_cost,
            "avg_processing_time_ms": avg_processing_ms,
        }

    @staticmethod
    def _estimate_cost(model_name: str, total_tokens: int) -> float | None:
        if not total_tokens:
            return None
        price = EMBEDDING_PRICING_PER_1K_TOKENS.get(model_name)
        if price is None:
            return None
        cost = (total_tokens / 1000) * price
        return round(cost, 6)

    def delete_document(self, pdf_id: UUID) -> bool:
        """Delete a PDF document and all associated artifacts."""

        session: Session = self._session_factory()
        file_paths: list[Path] = []
        try:
            document = session.get(PDFDocument, pdf_id)
            if not document:
                return False

            if document.file_path:
                file_paths.append(Path(document.file_path))

            # Collect figure asset paths before deletion to remove from disk later.
            file_paths.extend(
                Path(figure.image_path)
                for figure in list(document.figures)
                if getattr(figure, "image_path", None)
            )

            # Remove any outstanding jobs tied to this document.
            session.execute(delete(EmbeddingJob).where(EmbeddingJob.pdf_id == pdf_id))

            session.delete(document)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        for path in file_paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                # Swallow filesystem errors so delete endpoint still succeeds.
                continue

        return True


__all__ = [
    "PDFDataRepository",
]
