"""Data access helpers for ontology ingestion management."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    IngestionState,
    IngestionStatus,
    OntologyTerm,
    OntologyTermRelation,
    UnifiedChunk,
)


@dataclass
class OntologyStatusRow:
    """Serialized view of an ingestion status with aggregate counts."""

    ontology_type: str
    source_id: str
    state: IngestionState
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    message: Dict[str, Any] | str | None
    term_count: int
    relation_count: int
    chunk_count: int
    embedded_count: Optional[int]


class OntologyRepository:
    """Repository exposing ontology ingestion lifecycle information."""

    def __init__(self, session_factory=SessionLocal) -> None:
        self._session_factory = session_factory

    def list_statuses(self) -> Iterable[OntologyStatusRow]:
        session: Session = self._session_factory()
        try:
            stmt = (
                select(IngestionStatus)
                .where(IngestionStatus.source_type.like("ontology_%"))
                .order_by(IngestionStatus.updated_at.desc())
            )
            records = session.scalars(stmt).all()

            rows = []
            for record in records:
                ontology_type = self._extract_ontology_type(record.source_type)
                counts = self._counts_for(session, ontology_type, record.source_id)
                message_payload = self._parse_message(record.message)
                rows.append(
                    OntologyStatusRow(
                        ontology_type=ontology_type,
                        source_id=record.source_id,
                        state=record.status,
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                        message=message_payload,
                        term_count=counts["terms"],
                        relation_count=counts["relations"],
                        chunk_count=counts["chunks"],
                        embedded_count=counts["embedded"],
                    )
                )
            return rows
        finally:
            session.close()

    def get_status(
        self, ontology_type: str, source_id: str
    ) -> OntologyStatusRow | None:
        session: Session = self._session_factory()
        try:
            source_type = f"ontology_{ontology_type}"
            record = (
                session.query(IngestionStatus)
                .filter(
                    IngestionStatus.source_type == source_type,
                    IngestionStatus.source_id == source_id,
                )
                .first()
            )
            if record is None:
                return None

            counts = self._counts_for(session, ontology_type, source_id)
            message_payload = self._parse_message(record.message)
            return OntologyStatusRow(
                ontology_type=ontology_type,
                source_id=source_id,
                state=record.status,
                created_at=record.created_at,
                updated_at=record.updated_at,
                message=message_payload,
                term_count=counts["terms"],
                relation_count=counts["relations"],
                chunk_count=counts["chunks"],
                embedded_count=counts["embedded"],
            )
        finally:
            session.close()

    def _counts_for(
        self, session: Session, ontology_type: str, source_id: str
    ) -> Dict[str, int]:
        term_count = session.scalar(
            select(func.count(OntologyTerm.id)).where(
                OntologyTerm.ontology_type == ontology_type,
                OntologyTerm.source_id == source_id,
            )
        )
        relation_count = session.scalar(
            select(func.count(OntologyTermRelation.id)).where(
                OntologyTermRelation.ontology_type == ontology_type,
                OntologyTermRelation.source_id == source_id,
            )
        )
        chunk_query = select(func.count(UnifiedChunk.id)).where(
            UnifiedChunk.source_type == f"ontology_{ontology_type}",
            UnifiedChunk.source_id == source_id,
        )
        chunk_count = session.scalar(chunk_query)

        embedded_count = session.scalar(
            chunk_query.where(UnifiedChunk.embedding.isnot(None))
        )

        return {
            "terms": int(term_count or 0),
            "relations": int(relation_count or 0),
            "chunks": int(chunk_count or 0),
            "embedded": int(embedded_count or 0),
        }

    @staticmethod
    def _parse_message(message: Optional[str]) -> Dict[str, Any] | str | None:
        if not message:
            return None
        try:
            parsed = json.loads(message)
            if isinstance(parsed, dict):
                return parsed
            return parsed
        except (json.JSONDecodeError, TypeError):
            return message

    @staticmethod
    def _extract_ontology_type(source_type: str) -> str:
        prefix = "ontology_"
        if source_type.startswith(prefix):
            return source_type[len(prefix) :]
        return source_type


__all__ = ["OntologyRepository", "OntologyStatusRow"]
