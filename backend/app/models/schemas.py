"""
Simple Pydantic schemas for API responses and SQLAlchemy model serialization

This module provides basic Pydantic models that can serialize SQLAlchemy models
for API responses. No complex streaming or event handling - just simple data transfer.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


# ============================================================================
# Simple API Schemas for Database Models
# ============================================================================


class PDFDocumentResponse(BaseModel):
    """Simple response schema for PDF documents"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    file_hash: str
    page_count: int
    chunk_count: int = 0
    embeddings_generated: bool = False
    upload_timestamp: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChatSessionResponse(BaseModel):
    """Simple response schema for chat sessions"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_token: str
    pdf_document_id: Optional[UUID] = None
    created_at: datetime
    is_active: bool = True
    rag_config: Dict[str, Any] = Field(default_factory=dict)


class MessageResponse(BaseModel):
    """Simple response schema for messages"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    message_type: str
    content: str
    confidence_score: Optional[float] = None
    timestamp: datetime
    sequence_number: int


# ============================================================================
# Simple Request Schemas
# ============================================================================


class ChatRequest(BaseModel):
    """Simple chat request"""

    message: str
    session_id: Optional[str] = None
    stream: bool = False


class ChatResponse(BaseModel):
    """Simple chat response"""

    response: str
    session_id: str
    confidence: Optional[float] = None
    entities_extracted: Optional[int] = (
        None  # Just a count, not streaming individual entities
    )


class PDFUploadResponse(BaseModel):
    """Response after PDF upload"""

    document_id: UUID
    filename: str
    page_count: int
    status: str = "processing"
    message: str = "Document uploaded successfully"


# ============================================================================
# Simplified Streaming Update (only what's necessary)
# ============================================================================


class StreamingUpdate(BaseModel):
    """Simplified streaming update - only for text and status"""

    type: str = Field(description="Update type: text_delta, status, complete")
    content: str = Field(description="Content of the update")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Optional metadata")

    class Config:
        json_schema_extra = {
            "examples": [
                {"type": "text_delta", "content": "I'm analyzing the document..."},
                {
                    "type": "status",
                    "content": "Extracting entities",
                    "metadata": {"tool": "entity_extraction"},
                },
                {"type": "complete", "content": "", "metadata": {"entities_count": 5}},
            ]
        }
