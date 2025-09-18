"""PDF adapter for the unified pipeline."""

from __future__ import annotations

from typing import Dict, Any

from sqlalchemy.orm import Session

from app.models import UnifiedChunk
from lib.pipelines.document_source import (
    DocumentSource,
    IndexStatus,
    SourceRegistration,
)


class PDFDocumentSource(DocumentSource):
    """Thin adapter that exposes existing PDF data through the unified contract."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def registration(self) -> SourceRegistration:
        return SourceRegistration(source_type="pdf", default_source_id="*")

    async def ingest(self, *, source_id: str) -> IndexStatus:
        # PDF ingestion is handled by the existing upload pipeline.
        return IndexStatus.READY

    async def index_status(self, *, source_id: str) -> IndexStatus:
        with self._session_factory() as session:
            exists = self._chunk_exists(session, source_id)
        return IndexStatus.READY if exists else IndexStatus.NOT_INDEXED

    def _chunk_exists(self, session: Session, source_id: str) -> bool:
        return (
            session.query(UnifiedChunk.id)
            .filter(
                UnifiedChunk.source_type == "pdf",
                UnifiedChunk.source_id == source_id,
            )
            .limit(1)
            .first()
            is not None
        )

    def format_citation(self, chunk_payload: Dict[str, Any]) -> Dict[str, Any]:
        metadata = chunk_payload.get("chunk_metadata", {}) or {}
        page = chunk_payload.get("page") or metadata.get("page")
        section = chunk_payload.get("section") or metadata.get("section")
        return {"type": "pdf", "page": page, "section": section}


__all__ = ["PDFDocumentSource"]
