"""API endpoints for PDF viewer metadata and access."""

from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from src.models.sql.database import get_db
from src.models.sql.pdf_document import PDFDocument as PdfDocumentModel

router = APIRouter(prefix="/api/pdf-viewer", tags=["PDF Viewer"])


class PDFDocumentSummary(BaseModel):
    id: UUID = Field(..., description="Unique identifier for the document")
    filename: str = Field(..., min_length=1, max_length=255)
    page_count: int = Field(..., ge=1, le=50)
    file_size: int = Field(..., gt=0, le=52_428_800, description="File size in bytes")
    upload_timestamp: datetime
    viewer_url: str = Field(..., pattern=r"^/uploads/.*")


class PDFDocumentDetail(PDFDocumentSummary):
    last_accessed: datetime
    file_hash: str = Field(..., min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


class ViewerURLResponse(BaseModel):
    viewer_url: str = Field(..., pattern=r"^/uploads/.*")


class DocumentListResponse(BaseModel):
    documents: List[PDFDocumentSummary]
    total: int
    limit: int
    offset: int


def _viewer_url(file_path: str) -> str:
    """Return a viewer URL rooted at /uploads/ for the stored file path."""
    normalized = file_path.lstrip("/")
    return f"/uploads/{normalized}"


def _document_select() -> Select[tuple[PdfDocumentModel]]:
    return select(PdfDocumentModel).order_by(PdfDocumentModel.upload_timestamp.desc())


@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    total = db.execute(select(func.count()).select_from(PdfDocumentModel)).scalar_one()

    records = (
        db.execute(_document_select().offset(offset).limit(limit))
        .scalars()
        .all()
    )

    documents = [
        PDFDocumentSummary(
            id=record.id,
            filename=record.filename,
            page_count=record.page_count,
            file_size=record.file_size,
            upload_timestamp=record.upload_timestamp,
            viewer_url=_viewer_url(record.file_path),
        )
        for record in records
    ]

    return DocumentListResponse(
        documents=documents,
        total=total,
        limit=limit,
        offset=offset,
    )


def _get_document(db: Session, document_id: UUID) -> PdfDocumentModel:
    record = db.get(PdfDocumentModel, document_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PDF document {document_id} not found",
        )
    return record


@router.get("/documents/{document_id}", response_model=PDFDocumentDetail)
def get_document_detail(
    document_id: UUID = Path(..., description="UUID of the PDF document"),
    db: Session = Depends(get_db),
) -> PDFDocumentDetail:
    record = _get_document(db, document_id)

    record.last_accessed = datetime.now(timezone.utc)
    db.commit()
    db.refresh(record)

    return PDFDocumentDetail(
        id=record.id,
        filename=record.filename,
        page_count=record.page_count,
        file_size=record.file_size,
        upload_timestamp=record.upload_timestamp,
        last_accessed=record.last_accessed,
        viewer_url=_viewer_url(record.file_path),
        file_hash=record.file_hash,
    )


@router.get("/documents/{document_id}/url", response_model=ViewerURLResponse)
def get_document_viewer_url(
    document_id: UUID = Path(..., description="UUID of the PDF document"),
    db: Session = Depends(get_db),
) -> ViewerURLResponse:
    record = _get_document(db, document_id)

    record.last_accessed = datetime.now(timezone.utc)
    db.commit()

    return ViewerURLResponse(viewer_url=_viewer_url(record.file_path))
