"""Pydantic schemas for batch processing API."""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.models.sql.batch import BatchStatus, BatchDocumentStatus

MAX_BATCH_DOCUMENTS = 10


class BatchCreateRequest(BaseModel):
    """Request to create and start a new batch."""
    flow_id: UUID
    document_ids: List[UUID] = Field(..., min_length=1, max_length=MAX_BATCH_DOCUMENTS)


class BatchResultFile(BaseModel):
    """One formatter artifact produced for a batch document."""

    file_id: Optional[str] = None
    filename: Optional[str] = None
    download_url: str
    format: Optional[str] = None
    formatter_node_id: Optional[str] = None
    source_node_id: Optional[str] = None
    formatter_label: Optional[str] = None
    source_label: Optional[str] = None
    source_extraction_result_ids: List[str] = Field(default_factory=list)
    source_keys: List[str] = Field(default_factory=list)
    source_envelope_ids: List[str] = Field(default_factory=list)


class BatchOutputBranch(BaseModel):
    """Durable outcome for one configured output attachment."""

    edge_id: Optional[str] = None
    source_node_id: str
    output_node_id: str
    agent_id: Optional[str] = None
    formatter_label: Optional[str] = None
    source_label: Optional[str] = None
    status: Literal["completed", "missing"]
    output: Optional[dict[str, Any]] = None
    failure_reason: Optional[str] = None


class BatchDocumentResponse(BaseModel):
    """Per-document status in a batch."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    document_title: Optional[str] = None  # Populated from Weaviate lookup
    position: int
    status: BatchDocumentStatus
    result_file_path: Optional[str] = None
    result_files: List[BatchResultFile] = Field(default_factory=list)
    output_status: Optional[Literal["complete", "partial", "none", "failed"]] = None
    output_branches: List[BatchOutputBranch] = Field(default_factory=list)
    review_session_ids: Optional[List[str]] = None
    adapter_keys: List[str] = Field(default_factory=list)
    extraction_result_ids: List[str] = Field(default_factory=list)
    extraction_result_refs: List[Dict[str, Any]] = Field(default_factory=list)
    flow_run_id: Optional[str] = None
    origin_session_id: Optional[str] = None
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
