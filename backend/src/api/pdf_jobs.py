"""PDF processing jobs API endpoints."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import StreamingResponse

from .auth import get_auth_dependency
from ..lib.pdf_jobs import service as job_service
from ..models.sql.pdf_processing_job import PdfJobStatus
from ..models.sql.database import SessionLocal
from ..schemas.pdf_jobs import CancelPdfJobResponse, PdfJobListResponse, PdfJobResponse, PdfJobsStreamPayload
from ..services.user_service import principal_from_claims, provision_user

router = APIRouter(prefix="/weaviate")

_ALLOWED_STATUS_VALUES = {
    PdfJobStatus.PENDING.value,
    PdfJobStatus.RUNNING.value,
    PdfJobStatus.COMPLETED.value,
    PdfJobStatus.FAILED.value,
    PdfJobStatus.CANCEL_REQUESTED.value,
    PdfJobStatus.CANCELLED.value,
}


def _parse_job_uuid(job_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(job_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid job ID format: {job_id}") from exc


def _resolve_db_user_id(auth_user: Dict[str, Any]) -> int:
    session = SessionLocal()
    try:
        db_user = provision_user(session, principal_from_claims(auth_user))
        return db_user.id
    finally:
        session.close()


def _normalize_status_filters(statuses: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    for raw in statuses or []:
        value = str(raw).strip().lower()
        if not value:
            continue
        if value not in _ALLOWED_STATUS_VALUES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status filter '{raw}'. Allowed: {sorted(_ALLOWED_STATUS_VALUES)}",
            )
        normalized.append(value)
    return normalized


@router.get("/pdf-jobs", response_model=PdfJobListResponse)
async def list_pdf_jobs(
    user: Dict[str, Any] = get_auth_dependency(),
    status: Optional[List[str]] = Query(default=None, description="Filter by one or more statuses"),
    window_days: int = Query(default=7, ge=1, le=90, description="History window in days"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List durable background PDF processing jobs for the authenticated user."""
    user_id = _resolve_db_user_id(user)
    statuses = _normalize_status_filters(status)
    return job_service.list_jobs(
        user_id=user_id,
        window_days=window_days,
        statuses=statuses,
        limit=limit,
        offset=offset,
    )


@router.get("/pdf-jobs/stream")
async def stream_pdf_jobs(
    user: Dict[str, Any] = get_auth_dependency(),
    window_days: int = Query(default=7, ge=1, le=90, description="History window in days"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Stream job snapshots as SSE for live Jobs panel updates."""
    user_id = _resolve_db_user_id(user)

    poll_interval_raw = os.getenv("PDF_JOBS_STREAM_POLL_INTERVAL_SECONDS", "2")
    timeout_raw = os.getenv("PDF_JOBS_STREAM_TIMEOUT_SECONDS", "3600")

    try:
        poll_interval_seconds = max(1, int(poll_interval_raw))
    except (TypeError, ValueError):
        poll_interval_seconds = 2

    try:
        timeout_seconds = max(30, int(timeout_raw))
    except (TypeError, ValueError):
        timeout_seconds = 3600

    async def generate():
        last_signature = ""
        deadline = asyncio.get_event_loop().time() + timeout_seconds

        while asyncio.get_event_loop().time() < deadline:
            jobs = job_service.list_jobs(
                user_id=user_id,
                window_days=window_days,
                limit=limit,
                offset=0,
            ).jobs
            signature = "|".join(
                f"{job.job_id}:{job.status}:{job.progress_percentage}:{job.updated_at.isoformat()}:{int(job.cancel_requested)}"
                for job in jobs
            )

            if signature != last_signature:
                payload = PdfJobsStreamPayload(
                    timestamp=datetime.now(timezone.utc),
                    jobs=jobs,
                )
                yield f"data: {payload.model_dump_json()}\n\n"
                last_signature = signature

            await asyncio.sleep(poll_interval_seconds)

        timeout_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "final": True,
            "message": "PDF jobs stream timed out",
        }
        yield f"data: {json.dumps(timeout_payload)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/pdf-jobs/{job_id}", response_model=PdfJobResponse)
async def get_pdf_job(
    job_id: str = Path(..., description="PDF processing job UUID"),
    user: Dict[str, Any] = get_auth_dependency(),
):
    """Get a single PDF processing job by ID."""
    parsed_job_id = _parse_job_uuid(job_id)
    user_id = _resolve_db_user_id(user)
    job = job_service.get_job(job_id=parsed_job_id, user_id=user_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"PDF job {job_id} not found")
    return job


@router.post("/pdf-jobs/{job_id}/cancel", response_model=CancelPdfJobResponse)
async def cancel_pdf_job(
    job_id: str = Path(..., description="PDF processing job UUID"),
    user: Dict[str, Any] = get_auth_dependency(),
):
    """Request best-effort cancellation for an active PDF processing job."""
    parsed_job_id = _parse_job_uuid(job_id)
    user_id = _resolve_db_user_id(user)

    existing = job_service.get_job(job_id=parsed_job_id, user_id=user_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"PDF job {job_id} not found")

    updated = job_service.request_cancel(job_id=parsed_job_id, user_id=user_id)
    if not updated:
        raise HTTPException(status_code=404, detail=f"PDF job {job_id} not found")

    if updated.status in {PdfJobStatus.COMPLETED.value, PdfJobStatus.FAILED.value, PdfJobStatus.CANCELLED.value}:
        message = f"Job already terminal ({updated.status}); no cancellation needed"
    else:
        message = "Cancellation requested; remote extraction termination is best-effort"

    return CancelPdfJobResponse(success=True, message=message, job=updated)
