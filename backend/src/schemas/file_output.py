"""Pydantic schemas for file output operations."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class FileOutputCreate(BaseModel):
    """Request to record a newly generated file."""

    filename: str = Field(..., min_length=1, max_length=512)
    file_path: str = Field(...)
    file_type: str = Field(..., pattern="^(csv|tsv|json)$")
    file_size: int = Field(..., gt=0)
    file_hash: str | None = Field(None, min_length=64, max_length=64)

    curator_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=32, max_length=32, pattern="^[a-f0-9]{32}$")

    agent_name: str | None = None
    generation_model: str | None = None
    file_metadata: dict[str, Any] | None = None


class FileOutputResponse(BaseModel):
    """Response with file output details."""

    id: UUID
    filename: str
    file_type: str
    file_size: int
    curator_id: str
    session_id: str
    trace_id: str
    download_count: int
    created_at: datetime
    download_url: str | None = None

    model_config = {"from_attributes": True}


class FileOutputListResponse(BaseModel):
    """Paginated list of file outputs."""

    items: list[FileOutputResponse]
    total_count: int
    page: int
    page_size: int
