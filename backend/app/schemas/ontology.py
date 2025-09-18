"""Pydantic models for ontology ingestion management endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models import IngestionState


class OntologyStatusResponse(BaseModel):
    ontology_type: str = Field(..., min_length=1)
    source_id: str = Field(..., min_length=1)
    state: IngestionState
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    message: Dict[str, Any] | List[Any] | str | None = None
    term_count: int
    relation_count: int
    chunk_count: int
    embedded_count: Optional[int] = None


class OntologyIngestionRequest(BaseModel):
    ontology_type: str = Field(..., min_length=1)
    source_id: str = Field("all", min_length=1)
    obo_path: Optional[str] = Field(
        default=None,
        description="Optional path override for ontology source file",
    )


class OntologyIngestionSummary(BaseModel):
    inserted: int
    relations: int
    deleted_chunks: int
    deleted_terms: int
    deleted_relations: int
    embedded: int
    file_info: Dict[str, Any]
    embedding_summary: Dict[str, Any]
    insertion_summary: Dict[str, int]
    deletion_summary: Dict[str, int]


class OntologyIngestionResponse(BaseModel):
    ontology_type: str
    source_id: str
    summary: OntologyIngestionSummary
    status: Optional[OntologyStatusResponse] = None


class OntologyEmbeddingResponse(BaseModel):
    ontology_type: str
    source_id: str
    summary: Dict[str, Any]
    status: Optional[OntologyStatusResponse] = None
