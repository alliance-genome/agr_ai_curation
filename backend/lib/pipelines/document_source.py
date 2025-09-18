"""Abstractions for registering external knowledge sources with the unified pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class IndexStatus(str, Enum):
    """Lifecycle states returned by ingestion and status checks."""

    NOT_INDEXED = "not_indexed"
    INDEXING = "indexing"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True)
class SourceRegistration:
    """Descriptor used when registering a document source with the pipeline."""

    source_type: str
    default_source_id: str


class DocumentSource(ABC):
    """Contract implemented by each knowledge source adapter."""

    @abstractmethod
    def registration(self) -> SourceRegistration:
        """Return metadata describing this source."""

    @abstractmethod
    async def ingest(self, *, source_id: str) -> IndexStatus:
        """Ingest the provided source identifier into the unified store."""

    @abstractmethod
    async def index_status(self, *, source_id: str) -> IndexStatus:
        """Return the current ingestion/indexing status for the identifier."""

    @abstractmethod
    def format_citation(self, chunk_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Convert chunk metadata into a citation payload suitable for the UI."""


__all__ = ["DocumentSource", "SourceRegistration", "IndexStatus"]
