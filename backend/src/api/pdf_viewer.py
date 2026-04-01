"""API endpoints for PDF viewer metadata, access, and evidence localization."""

from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from src.lib.pdf_viewer.rapidfuzz_matcher import (
    MatchRange,
    PdfPageText,
    match_quote_to_pdf_pages,
)
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


class PdfViewerFuzzyMatchPage(BaseModel):
    page_number: int = Field(..., ge=1, description="1-based PDF page number")
    text: str = Field(..., description="Raw PDF.js page text extracted from the viewer")


class PdfViewerFuzzyMatchRequest(BaseModel):
    quote: str = Field(..., min_length=1, description="Quote-like text to localize against PDF.js page text")
    pages: list[PdfViewerFuzzyMatchPage] = Field(
        ...,
        min_length=1,
        description="Ordered PDF.js page text corpus for the current document",
    )
    page_hints: list[int] = Field(
        default_factory=list,
        description="Preferred 1-based page hints used as a tie-breaker when scores are close",
    )
    min_score: float = Field(
        default=70.0,
        ge=0.0,
        le=100.0,
        description="Minimum RapidFuzz score required to accept the best candidate",
    )


class PdfViewerFuzzyMatchRange(BaseModel):
    page_number: int = Field(..., ge=1)
    raw_start: int = Field(..., ge=0)
    raw_end_exclusive: int = Field(..., ge=0)
    query: str


class PdfViewerFuzzyMatchResponse(BaseModel):
    found: bool
    strategy: str
    score: float = Field(..., ge=0.0, le=100.0)
    matched_page: int | None = Field(default=None, ge=1)
    matched_query: str | None = None
    matched_range: PdfViewerFuzzyMatchRange | None = None
    full_query: str | None = None
    page_ranges: list[PdfViewerFuzzyMatchRange]
    cross_page: bool
    note: str


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


def _serialize_match_range(match_range: MatchRange | None) -> PdfViewerFuzzyMatchRange | None:
    if match_range is None:
        return None

    return PdfViewerFuzzyMatchRange(
        page_number=match_range.page_number,
        raw_start=match_range.raw_start,
        raw_end_exclusive=match_range.raw_end_exclusive,
        query=match_range.query,
    )


@router.post("/evidence/fuzzy-match", response_model=PdfViewerFuzzyMatchResponse)
def fuzzy_match_pdf_evidence_quote(
    request: PdfViewerFuzzyMatchRequest,
) -> PdfViewerFuzzyMatchResponse:
    result = match_quote_to_pdf_pages(
        request.quote,
        [
            PdfPageText(page_number=page.page_number, raw_text=page.text)
            for page in request.pages
        ],
        page_hints=request.page_hints,
        min_score=request.min_score,
    )
    page_ranges = [
        serialized_range
        for serialized_range in (
            _serialize_match_range(page_range)
            for page_range in result.page_ranges
        )
        if serialized_range is not None
    ]

    return PdfViewerFuzzyMatchResponse(
        found=result.found,
        strategy=result.strategy,
        score=result.score,
        matched_page=result.matched_page,
        matched_query=result.matched_query,
        matched_range=_serialize_match_range(result.matched_range),
        full_query=result.full_query,
        page_ranges=page_ranges,
        cross_page=result.cross_page,
        note=result.note,
    )
