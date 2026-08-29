"""Pydantic schemas for document operations."""

import unicodedata
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class DocumentUpdateRequest(BaseModel):
    """Request schema for updating document metadata."""

    title: Optional[str] = Field(
        default=None,
        max_length=255,
        description="User-defined title for the document"
    )
    filename: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Source PDF filename",
    )

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str | None) -> str | None:
        """Reject filenames that are unsafe or cannot identify a PDF."""
        if value is None:
            return None
        if not value.strip():
            raise ValueError("Filename must not be empty")
        if not value.lower().endswith(".pdf"):
            raise ValueError("Filename must use the .pdf extension")
        if "/" in value or "\\" in value:
            raise ValueError("Filename must not contain path separators")
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError("Filename must not contain control characters")
        return value


class DocumentUpdateResponse(BaseModel):
    """Response schema for document update operation."""

    document_id: str = Field(..., description="UUID of the updated document")
    title: Optional[str] = Field(None, description="Updated title value")
    filename: str = Field(..., description="Updated source PDF filename")
