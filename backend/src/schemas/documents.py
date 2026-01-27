"""Pydantic schemas for document operations."""
from typing import Optional
from pydantic import BaseModel, Field


class DocumentUpdateRequest(BaseModel):
    """Request schema for updating document metadata."""

    title: Optional[str] = Field(
        default=None,
        max_length=255,
        description="User-defined title for the document"
    )


class DocumentUpdateResponse(BaseModel):
    """Response schema for document update operation."""

    document_id: str = Field(..., description="UUID of the updated document")
    title: Optional[str] = Field(None, description="Updated title value")
