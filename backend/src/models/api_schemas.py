"""API request/response schemas for Weaviate control panel."""

from datetime import datetime
from typing import List, Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict

from .document import PDFDocument, EmbeddingStatus
from .chunk import DocumentChunk


class SortOrder(str, Enum):
    """Sort direction options."""
    ASC = "asc"
    DESC = "desc"


class SortBy(str, Enum):
    """Fields available for sorting."""
    FILENAME = "filename"
    CREATION_DATE = "creationDate"
    FILE_SIZE = "fileSize"
    VECTOR_COUNT = "vectorCount"


class DocumentFilter(BaseModel):
    """Filter criteria for document list pagination."""

    search_term: Optional[str] = Field(None, description="Text search in filename/metadata")
    embedding_status: Optional[List[EmbeddingStatus]] = Field(None, description="Filter by status")
    date_from: Optional[datetime] = Field(None, description="Creation date start")
    date_to: Optional[datetime] = Field(None, description="Creation date end")
    min_vector_count: Optional[int] = Field(None, ge=0, description="Minimum vectors")
    max_vector_count: Optional[int] = Field(None, ge=0, description="Maximum vectors")

    @property
    def is_date_range_valid(self) -> bool:
        """Check if date range is valid."""
        if self.date_from and self.date_to:
            return self.date_from <= self.date_to
        return True

    @property
    def is_vector_range_valid(self) -> bool:
        """Check if vector count range is valid."""
        if self.min_vector_count and self.max_vector_count:
            return self.min_vector_count <= self.max_vector_count
        return True


class PaginationParams(BaseModel):
    """Pagination control for document lists."""

    page: int = Field(default=1, gt=0, description="Current page (1-indexed)")
    page_size: int = Field(default=20, ge=10, le=100, description="Items per page")
    sort_by: SortBy = Field(default=SortBy.CREATION_DATE, description="Field to sort by")
    sort_order: SortOrder = Field(default=SortOrder.DESC, description="Sort direction")


class DocumentListRequest(BaseModel):
    """Request for document list endpoint."""

    filters: Optional[DocumentFilter] = None
    pagination: PaginationParams = Field(default_factory=PaginationParams)


class PaginationInfo(BaseModel):
    """Pagination information for responses."""

    current_page: int
    total_pages: int
    total_items: int
    page_size: int


class DocumentListResponse(BaseModel):
    """Response for document list endpoint.

    Updated for T030: Returns contract-compliant flat structure.
    Contract: document_endpoints.yaml lines 60-72
    """

    documents: List[Dict[str, Any]]  # List of Document schema dicts
    total: int  # Total number of documents
    limit: int  # Page size (items per page)
    offset: int  # Number of items skipped


class EmbeddingModelBreakdown(BaseModel):
    """Summary usage information for an embedding model."""

    model: str
    chunk_count: int = Field(..., ge=0)


class EmbeddingSummary(BaseModel):
    """Aggregate embedding statistics for a document."""

    total_chunks: int = Field(default=0, ge=0)
    embedded_chunks: int = Field(default=0, ge=0)
    coverage_percentage: Optional[float] = Field(default=None, ge=0)
    last_embedded_at: Optional[datetime] = None
    primary_model: Optional[str] = None
    models: List[EmbeddingModelBreakdown] = Field(default_factory=list)


class ChunkPreview(BaseModel):
    """Minimal chunk representation for previews."""

    model_config = ConfigDict(extra='allow')

    id: Optional[str] = None
    document_id: Optional[str] = None
    chunk_index: Optional[int] = Field(default=None, ge=0)
    content: Optional[str] = None
    element_type: Optional[str] = None
    page_number: Optional[int] = Field(default=None, ge=0)
    section_title: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    embedding_model: Optional[str] = None
    embedding_timestamp: Optional[datetime] = None


class DocumentDetailResponse(BaseModel):
    """Response shape for document detail endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    document: Dict[str, Any]
    chunks_preview: List[ChunkPreview] = Field(default_factory=list, description="First 10 chunks for preview")
    total_chunks: int = Field(default=0, ge=0)
    embedding_summary: Optional[EmbeddingSummary] = None
    pipeline_status: Optional[Dict[str, Any]] = None
    related_documents: List[Dict[str, Any]] = Field(default_factory=list, description="Similar documents by vector similarity")
    schema_version: str = Field(default="1.0.0")


class ChunkListResponse(BaseModel):
    """Response for chunks endpoint."""

    chunks: List[DocumentChunk]
    pagination: PaginationInfo
    document_id: str


class OperationResult(BaseModel):
    """Result of document operations."""

    success: bool
    message: str
    document_id: Optional[str] = None
    error: Optional[Dict[str, str]] = None

    @classmethod
    def success_result(cls, message: str, document_id: Optional[str] = None) -> 'OperationResult':
        """Create a success result."""
        return cls(success=True, message=message, document_id=document_id)

    @classmethod
    def error_result(cls, message: str, error_code: str, details: str) -> 'OperationResult':
        """Create an error result."""
        return cls(
            success=False,
            message=message,
            error={'code': error_code, 'details': details}
        )


class EmbeddingConfiguration(BaseModel):
    """Configuration for embedding model selection."""

    model_config = ConfigDict(use_enum_values=True)

    model_provider: str = Field(..., description="Provider of embedding model")
    model_name: str = Field(..., description="Specific model identifier")
    dimensions: int = Field(..., gt=0, description="Vector dimensions")
    batch_size: int = Field(default=10, ge=1, le=100, description="Batch size for operations")


class WeaviateSettings(BaseModel):
    """Database-level configuration settings."""

    collection_name: str
    schema_version: str
    replication_factor: int = Field(default=1, gt=0)
    consistency: str = Field(default="eventual")
    vector_index_type: str = Field(default="hnsw")


class AvailableModel(BaseModel):
    """Available embedding model."""

    name: str
    dimensions: int


class AvailableModelsResponse(BaseModel):
    """Response for available models endpoint."""

    provider: str
    models: List[AvailableModel]


class SettingsResponse(BaseModel):
    """Response for settings endpoints."""

    embedding: EmbeddingConfiguration
    database: WeaviateSettings
    available_models: List[AvailableModelsResponse]


class ReprocessRequest(BaseModel):
    """Request for document reprocessing."""

    strategy_name: str = Field(..., description="Chunking strategy to use")
    force_reparse: bool = Field(default=False, description="Force re-parsing from PDF")


class ReembedRequest(BaseModel):
    """Request for document re-embedding."""

    embedding_config: Optional[EmbeddingConfiguration] = None
    batch_size: int = Field(default=10, ge=1, le=100)


class DocumentResponse(BaseModel):
    """Response schema for document operations (upload, get).

    Matches the Document schema from document_endpoints.yaml contract.
    Requirements: FR-014, FR-016 - Include ownership metadata in all document responses.
    """

    document_id: str = Field(..., description="Unique document identifier")
    user_id: int = Field(..., description="Owner user ID from PostgreSQL users table")
    filename: str = Field(..., description="Original filename")
    status: str = Field(..., description="Processing status (PENDING, PROCESSING, COMPLETED, FAILED)")
    upload_timestamp: datetime = Field(..., description="When document was uploaded")
    processing_started_at: Optional[datetime] = Field(None, description="When processing began")
    processing_completed_at: Optional[datetime] = Field(None, description="When processing completed")
    file_size_bytes: int = Field(..., description="File size in bytes")
    weaviate_tenant: str = Field(..., description="Weaviate tenant name (user_id with underscores)")
    chunk_count: Optional[int] = Field(None, description="Number of chunks created")
    error_message: Optional[str] = Field(None, description="Error message if processing failed")
