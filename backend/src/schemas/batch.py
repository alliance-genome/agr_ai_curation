"""Pydantic schemas for batch processing API."""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.models.sql.batch import BatchStatus, BatchDocumentStatus


class BatchCreateRequest(BaseModel):
    """Request to create and start a new batch."""
    flow_id: UUID
    document_ids: List[UUID] = Field(..., min_length=1, max_length=100)


class BatchDocumentResponse(BaseModel):
    """Per-document status in a batch."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    document_title: Optional[str] = None  # Populated from Weaviate lookup
    position: int
    status: BatchDocumentStatus
    result_file_path: Optional[str] = None
    error_message: Optional[str] = None
    processing_time_ms: Optional[int] = None
    processed_at: Optional[datetime] = None


class BatchResponse(BaseModel):
    """Batch details response."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    flow_id: UUID
    flow_name: Optional[str] = None  # Populated from flow lookup
    status: BatchStatus
    total_documents: int
    completed_documents: int
    failed_documents: int
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    documents: List[BatchDocumentResponse] = []


class BatchListResponse(BaseModel):
    """List of batches for history view."""
    batches: List[BatchResponse]
    total: int


class BatchValidationResponse(BaseModel):
    """Flow validation result for batch compatibility."""
    valid: bool
    errors: List[str] = []
