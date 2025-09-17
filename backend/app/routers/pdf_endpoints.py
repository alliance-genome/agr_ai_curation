"""Endpoints for PDF upload and ingestion."""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.config import get_settings
from app.services.pdf_ingest_service import PDFIngestService, get_pdf_ingest_service

router = APIRouter(prefix="/api/pdf", tags=["pdf"])

UPLOAD_DIR = Path(get_settings().uploads_dir)


@router.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    ingest_service: PDFIngestService = Depends(get_pdf_ingest_service),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    destination = UPLOAD_DIR / file.filename
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        pdf_id: UUID = ingest_service.ingest(
            file_path=destination, original_filename=file.filename
        )
    except Exception as exc:  # pragma: no cover - unexpected runtime issues
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        await file.close()

    viewer_url = f"/uploads/{file.filename}"
    return {
        "pdf_id": str(pdf_id),
        "filename": file.filename,
        "viewer_url": viewer_url,
    }


__all__ = ["router"]
