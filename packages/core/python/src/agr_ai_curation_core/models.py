"""Minimal structured output models used by the core package tools."""

from datetime import datetime

from pydantic import BaseModel, Field


class FileInfo(BaseModel):
    """Metadata returned by file output tools."""

    file_id: str = Field(..., description="UUID for tracking and API endpoints")
    filename: str = Field(..., description="Full filename with extension")
    format: str = Field(..., description="File format: csv, tsv, or json")
    size_bytes: int = Field(..., description="File size in bytes")
    hash_sha256: str = Field(..., description="SHA-256 hash for integrity")
    mime_type: str = Field(..., description="MIME type for download")
    download_url: str = Field(..., description="API endpoint for download")
    created_at: datetime = Field(..., description="Generation timestamp (UTC)")
    trace_id: str | None = Field(None, description="Langfuse trace ID")
    session_id: str | None = Field(None, description="Chat session ID")
    curator_id: str | None = Field(None, description="User who requested the file")
