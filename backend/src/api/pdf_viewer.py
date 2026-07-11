"""API endpoints for PDF viewer metadata, access, and evidence localization."""

from datetime import datetime, timezone
from typing import List, NamedTuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from src.lib.pdf_viewer.rapidfuzz_matcher import (
    MatchRange,
    PdfPageText,
    match_quote_to_pdf_pages,
)
from src.lib.pdf_limits import MAX_PDF_FILE_SIZE_BYTES
from src.config import get_pdf_storage_path
from src.api.auth import get_auth_dependency
from src.api.documents import validate_user_file_path
from src.models.sql.database import get_db
from src.models.sql.pdf_document import PDFDocument as PdfDocumentModel
from src.services.document_access import (
    owned_documents_select,
    protected_pdf_url,
    require_owned_document,
)
from src.services.user_service import principal_from_claims, provision_user

router = APIRouter(prefix="/api/pdf-viewer", tags=["PDF Viewer"])


class PDFDocumentSummary(BaseModel):
    id: UUID = Field(..., description="Unique identifier for the document")
    filename: str = Field(..., min_length=1, max_length=255)
    page_count: int = Field(..., ge=1)
    file_size: int = Field(
        ...,
        gt=0,
        le=MAX_PDF_FILE_SIZE_BYTES,
        description="File size in bytes",
    )
    upload_timestamp: datetime
    viewer_url: str | None = Field(
        ...,
        pattern=r"^/api/pdf-viewer/documents/[0-9a-f-]+/content$",
    )
    viewer_mode: str | None = None


class PDFDocumentDetail(PDFDocumentSummary):
    last_accessed: datetime
    file_hash: str = Field(..., min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


class ViewerURLResponse(BaseModel):
    viewer_url: str | None = Field(
        ...,
        pattern=r"^/api/pdf-viewer/documents/[0-9a-f-]+/content$",
    )
    viewer_mode: str | None = None


class DocumentListResponse(BaseModel):
    documents: List[PDFDocumentSummary]
    total: int
    limit: int
    offset: int


class _OwnedDocumentAccess(NamedTuple):
    record: PdfDocumentModel
    owner_auth_sub: str


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


def _viewer_mode(record: PdfDocumentModel) -> str:
    return str(record.viewer_mode or "local_pdf").strip().lower() or "local_pdf"


def _document_viewer_url(record: PdfDocumentModel) -> str | None:
    if _viewer_mode(record) == "text_only":
        return None
    return protected_pdf_url(record.id)


def _document_select(owner_user_id: int) -> Select[tuple[PdfDocumentModel]]:
    return owned_documents_select(owner_user_id).order_by(
        PdfDocumentModel.upload_timestamp.desc()
    )


@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: dict = get_auth_dependency(),
) -> DocumentListResponse:
    db_user = provision_user(db, principal_from_claims(user))
    owner_filter = PdfDocumentModel.user_id == db_user.id
    total = db.execute(
        select(func.count()).select_from(PdfDocumentModel).where(owner_filter)
    ).scalar_one()

    records = (
        db.execute(_document_select(db_user.id).offset(offset).limit(limit))
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
            viewer_url=_document_viewer_url(record),
            viewer_mode=_viewer_mode(record),
        )
        for record in records
    ]

    return DocumentListResponse(
        documents=documents,
        total=total,
        limit=limit,
        offset=offset,
    )


def _get_document(
    db: Session,
    document_id: UUID,
    user: dict,
) -> _OwnedDocumentAccess:
    db_user = provision_user(db, principal_from_claims(user))
    return _OwnedDocumentAccess(
        record=require_owned_document(db, document_id, db_user.id),
        owner_auth_sub=db_user.auth_sub,
    )


@router.get("/documents/{document_id}", response_model=PDFDocumentDetail)
def get_document_detail(
    document_id: UUID = Path(..., description="UUID of the PDF document"),
    db: Session = Depends(get_db),
    user: dict = get_auth_dependency(),
) -> PDFDocumentDetail:
    record = _get_document(db, document_id, user).record

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
        viewer_url=_document_viewer_url(record),
        viewer_mode=_viewer_mode(record),
        file_hash=record.file_hash,
    )


@router.get("/documents/{document_id}/url", response_model=ViewerURLResponse)
def get_document_viewer_url(
    document_id: UUID = Path(..., description="UUID of the PDF document"),
    db: Session = Depends(get_db),
    user: dict = get_auth_dependency(),
) -> ViewerURLResponse:
    record = _get_document(db, document_id, user).record

    record.last_accessed = datetime.now(timezone.utc)
    db.commit()

    return ViewerURLResponse(
        viewer_url=_document_viewer_url(record),
        viewer_mode=_viewer_mode(record),
    )


@router.get("/documents/{document_id}/content", response_class=FileResponse)
@router.head("/documents/{document_id}/content", include_in_schema=False)
def get_document_pdf_content(
    document_id: UUID = Path(..., description="UUID of the PDF document"),
    db: Session = Depends(get_db),
    user: dict = get_auth_dependency(),
) -> FileResponse:
    """Serve owned PDF bytes without exposing the tenant storage path."""
    access = _get_document(db, document_id, user)
    record = access.record
    if _viewer_mode(record) == "text_only":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PDF file not available for document {document_id}",
        )

    storage_root = get_pdf_storage_path()
    file_path = validate_user_file_path(
        storage_root / record.file_path,
        storage_root,
        access.owner_auth_sub,
    )
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PDF file not available for document {document_id}",
        )

    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=record.filename,
        content_disposition_type="inline",
    )


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
