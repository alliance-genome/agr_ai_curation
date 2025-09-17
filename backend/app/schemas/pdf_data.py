"""Pydantic schemas for PDF data browser responses."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PDFDocumentSummary(BaseModel):
    id: UUID
    filename: str
    upload_timestamp: datetime
    last_accessed: Optional[datetime]
    page_count: Optional[int]
    chunk_count: Optional[int]
    table_count: Optional[int]
    figure_count: Optional[int]
    embeddings_generated: Optional[bool]


class PDFDocumentDetail(PDFDocumentSummary):
    file_size: Optional[int]
    extraction_method: Optional[str]
    preproc_version: Optional[str]
    meta_data: dict = Field(default_factory=dict)


class PDFChunkRow(BaseModel):
    id: UUID
    chunk_index: int
    text_preview: str
    page_start: Optional[int]
    page_end: Optional[int]
    section_path: Optional[str]
    element_type: Optional[str]
    is_reference: Optional[bool]
    is_caption: Optional[bool]
    is_table: Optional[bool]
    is_figure: Optional[bool]
    token_count: Optional[int]


class LangGraphRunRow(BaseModel):
    id: UUID
    workflow_name: str
    input_query: str
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    latency_ms: Optional[int]
    specialists_invoked: List[str] = Field(default_factory=list)


class LangGraphNodeRow(BaseModel):
    id: UUID
    graph_run_id: UUID
    node_key: str
    node_type: str
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    latency_ms: Optional[int]
    error: Optional[str]


class EmbeddingSummaryRow(BaseModel):
    model_name: str
    count: int
    latest_created_at: Optional[datetime]


__all__ = [
    "PDFDocumentSummary",
    "PDFDocumentDetail",
    "PDFChunkRow",
    "LangGraphRunRow",
    "LangGraphNodeRow",
    "EmbeddingSummaryRow",
]
