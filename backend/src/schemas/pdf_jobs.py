"""Pydantic schemas for PDF processing job APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PdfJobResponse(BaseModel):
    """Public API representation of a background PDF processing job."""

    job_id: str
    document_id: str
    user_id: int
    filename: Optional[str] = None
    status: str
    current_stage: Optional[str] = None
    progress_percentage: int = Field(default=0, ge=0, le=100)
    message: Optional[str] = None
    process_id: Optional[str] = None
    cancel_requested: bool = False
    error_message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    updated_at: datetime
    completed_at: Optional[datetime] = None


class PdfJobListResponse(BaseModel):
    """Paginated PDF job list response."""

    jobs: List[PdfJobResponse]
    total: int
    limit: int
    offset: int


class CancelPdfJobResponse(BaseModel):
    """Cancel-request API response."""

    success: bool = True
    message: str
    job: PdfJobResponse


class PdfJobsStreamPayload(BaseModel):
    """Server-sent event payload for PDF jobs stream."""

    timestamp: datetime
    jobs: List[PdfJobResponse]
