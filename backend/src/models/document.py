"""PDFDocument model for Weaviate database control panel."""

from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator, ConfigDict, ValidationInfo


class ProcessingStatus(str, Enum):
    """Processing pipeline status values."""
    PENDING = "pending"
    PROCESSING = "processing"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    STORING = "storing"
    COMPLETED = "completed"
    FAILED = "failed"


class EmbeddingStatus(str, Enum):
    """Embedding completion status values."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class DocumentMetadata(BaseModel):
    """Additional document metadata."""
    page_count: int = Field(..., gt=0)
    author: Optional[str] = None
    title: Optional[str] = None
    checksum: str
    document_type: str
    last_processed_stage: str


class PDFDocument(BaseModel):
    """Represents a PDF document stored in Weaviate database."""

    model_config = ConfigDict(use_enum_values=True, arbitrary_types_allowed=True)

    id: str = Field(..., description="UUID from Weaviate")
    filename: str = Field(..., min_length=1, max_length=255)
    file_size: int = Field(..., gt=0, description="File size in bytes")
    creation_date: datetime
    last_accessed_date: datetime
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    embedding_status: EmbeddingStatus = EmbeddingStatus.PENDING
    chunk_count: int = Field(default=0, ge=0)
    vector_count: int = Field(default=0, ge=0)
    metadata: DocumentMetadata

    @field_validator('filename')
    @classmethod
    def validate_filename(cls, v: str) -> str:
        """Validate filename is not empty and within length limits."""
        if not v.strip():
            raise ValueError("Filename must not be empty")
        if len(v) > 255:
            raise ValueError("Filename must not exceed 255 characters")
        return v

    @field_validator('embedding_status')
    @classmethod
    def validate_embedding_transition(cls, v: EmbeddingStatus, info: ValidationInfo) -> EmbeddingStatus:
        """Validate embedding status transitions."""
        # Allow any status during creation
        return v

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        data = self.model_dump()
        data['creation_date'] = self.creation_date.isoformat()
        data['last_accessed_date'] = self.last_accessed_date.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PDFDocument':
        """Create model from dictionary."""
        if isinstance(data.get('creation_date'), str):
            data['creation_date'] = datetime.fromisoformat(data['creation_date'])
        if isinstance(data.get('last_accessed_date'), str):
            data['last_accessed_date'] = datetime.fromisoformat(data['last_accessed_date'])
        return cls(**data)
