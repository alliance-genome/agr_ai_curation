"""Read-only access utilities for PDF metadata and chunks."""

from __future__ import annotations

from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.database import SessionLocal
from app.models import (
    PDFDocument,
    PDFChunk,
    LangGraphRun,
    LangGraphNodeRun,
    PDFEmbedding,
)


class PDFDataRepository:
    """Repository exposing read-only PDF data views."""

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
                )
                .where(PDFEmbedding.pdf_id == pdf_id)
                .group_by(PDFEmbedding.model_name)
            )
            results = session.execute(stmt).all()
            return [
                {
                    "model_name": row.model_name,
                    "count": row.count,
                    "latest_created_at": row.latest_created_at,
                }
                for row in results
            ]
        finally:
            session.close()


__all__ = [
    "PDFDataRepository",
]
