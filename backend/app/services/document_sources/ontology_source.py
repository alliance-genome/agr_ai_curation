"""Ontology adapter for Disease/Gene/FlyBase vocabularies."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models import IngestionState, IngestionStatus, UnifiedChunk
from lib.pipelines.document_source import (
    DocumentSource,
    IndexStatus,
    SourceRegistration,
)
from app.jobs.ingest_ontology import ingest_ontology


class OntologyDocumentSource(DocumentSource):
    """Persist ontology terms into the shared unified chunk store."""

    def __init__(
        self,
        *,
        ontology_type: str,
        data_path: Path,
        session_factory,
        embedding_service,
        embedding_model: str,
    ) -> None:
        self._ontology_type = ontology_type
        self._data_path = Path(data_path)
        self._session_factory = session_factory
        self._embedding_service = embedding_service
        self._embedding_model = embedding_model

    def registration(self) -> SourceRegistration:
        return SourceRegistration(
            source_type=f"ontology_{self._ontology_type}",
            default_source_id="all",
        )

    async def ingest(self, *, source_id: str) -> IndexStatus:
        ingest_ontology(
            ontology_type=self._ontology_type,
            source_id=source_id,
            obo_path=self._data_path,
            session_factory=self._session_factory,
            embedding_service=self._embedding_service,
        )
        return IndexStatus.READY

    async def index_status(self, *, source_id: str) -> IndexStatus:
        with self._session_factory() as session:
            status = (
                session.query(IngestionStatus)
                .filter(
                    IngestionStatus.source_type == f"ontology_{self._ontology_type}",
                    IngestionStatus.source_id == source_id,
                )
                .first()
            )
            if status is None:
                return IndexStatus.NOT_INDEXED
            if status.status == IngestionState.READY:
                if not self._chunk_exists(session, source_id):
                    return IndexStatus.NOT_INDEXED
            return IndexStatus(status.status.value)

    def _chunk_exists(self, session: Session, source_id: str) -> bool:
        return (
            session.query(UnifiedChunk.id)
            .filter(
                UnifiedChunk.source_type == f"ontology_{self._ontology_type}",
                UnifiedChunk.source_id == source_id,
            )
            .limit(1)
            .first()
            is not None
        )

    def format_citation(self, chunk_payload: Dict[str, Any]) -> Dict[str, Any]:
        metadata = chunk_payload.get("chunk_metadata", {}) or {}
        term_id = metadata.get("term_id") or chunk_payload.get("term_id")
        term_name = metadata.get("name") or chunk_payload.get("name")
        return {
            "type": "ontology",
            "ontology": self._ontology_type,
            "term_id": term_id,
            "term_name": term_name,
        }


__all__ = ["OntologyDocumentSource"]
