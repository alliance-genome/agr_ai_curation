"""API endpoints for PDF viewer metadata, access, and evidence localization."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import List, NamedTuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from src.lib.pdf_viewer.display_blocks import (
    UnsupportedDisplayElementError,
    build_display_blocks,
)
from src.lib.pdf_viewer.files import (
    find_paper_file,
    serves_pdf,
    serves_text,
    viewer_file_refs,
)
from src.lib.pdf_viewer.rapidfuzz_matcher import (
    MatchRange,
    PdfPageText,
    match_quote_to_pdf_pages,
)
from src.config import get_pdf_storage_path
from src.api.auth import get_auth_dependency
from src.api.documents import validate_user_file_path
from src.models.sql.database import get_db
from src.models.sql.pdf_document import (
    DISPLAY_MODE_TEXT,
    PDFDocument as PdfDocumentModel,
    PDFDocumentFile,
)
from src.schemas.viewer_files import DisplayBlock, ViewerFileRef
from src.services.document_access import (
    owned_documents_select,
    protected_pdf_url,
    require_owned_document,
)
from src.services.user_service import principal_from_claims, provision_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pdf-viewer", tags=["PDF Viewer"])


class PDFDocumentSummary(BaseModel):
    id: UUID = Field(..., description="Unique identifier for the document")
    filename: str = Field(..., min_length=1, max_length=255)
    page_count: int | None = Field(
        ..., ge=1, description="Pages of the main file; none for a text file or a paper without a main file"
    )
    file_size: int | None = Field(
        ...,
        gt=0,
        description="Main file size in bytes; none for a paper without a main file",
    )
    upload_timestamp: datetime
    viewer_url: str | None = Field(
        ...,
        pattern=r"^/api/pdf-viewer/documents/[0-9a-f-]+/content$",
    )
    viewer_mode: str | None = None
    has_main_file: bool = Field(
        ...,
        description=(
            "False for a paper that holds supplements only; its viewer_mode, "
            "viewer_url, page_count and file_size are then null"
        ),
    )


class PDFDocumentDetail(PDFDocumentSummary):
    last_accessed: datetime
    file_hash: str | None = Field(
        ..., min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$",
        description="Main file hash; none for a paper without a main file",
    )


class ViewerURLResponse(BaseModel):
    viewer_url: str | None = Field(
        ...,
        pattern=r"^/api/pdf-viewer/documents/[0-9a-f-]+/content$",
    )
    viewer_mode: str | None = None
    has_main_file: bool = Field(
        ...,
        description="False for a paper that holds supplements only; viewer_mode is then null",
    )


class DocumentFilesResponse(BaseModel):
    document_id: UUID
    files: List[ViewerFileRef] = Field(..., description="Every file of the paper, in tab order")


class FileTextResponse(BaseModel):
    document_id: UUID
    file_id: UUID
    display_name: str
    blocks: List[DisplayBlock] = Field(..., description="The file's text as it was cut into pieces")


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


def _viewer_mode(record: PdfDocumentModel) -> str | None:
    """The main file's display mode, which the viewer reads until it shows one tab per file.

    None for a paper without a main file (its supplements only).
    """
    main_file = record.main_file_or_none
    return main_file.display_mode if main_file is not None else None


def _document_viewer_url(record: PdfDocumentModel) -> str | None:
    viewer_mode = _viewer_mode(record)
    if viewer_mode is None or viewer_mode == DISPLAY_MODE_TEXT:
        return None
    return protected_pdf_url(record.id)


def _document_summary(record: PdfDocumentModel) -> PDFDocumentSummary:
    main_file = record.main_file_or_none
    return PDFDocumentSummary(
        id=record.id,
        filename=record.filename,
        page_count=main_file.page_count if main_file is not None else None,
        file_size=main_file.file_size if main_file is not None else None,
        upload_timestamp=record.upload_timestamp,
        viewer_url=_document_viewer_url(record),
        viewer_mode=_viewer_mode(record),
        has_main_file=main_file is not None,
    )


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

    documents = [_document_summary(record) for record in records]

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

    main_file = record.main_file_or_none
    return PDFDocumentDetail(
        **_document_summary(record).model_dump(),
        last_accessed=record.last_accessed,
        file_hash=main_file.file_hash if main_file is not None else None,
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
        has_main_file=record.main_file_or_none is not None,
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
    main_file = record.main_file_or_none
    if main_file is None or main_file.display_mode == DISPLAY_MODE_TEXT:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PDF file not available for document {document_id}",
        )

    storage_root = get_pdf_storage_path()
    file_path = validate_user_file_path(
        storage_root / main_file.file_path,
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


def _owned_paper_file(
    db: Session, document_id: UUID, file_id: UUID, user: dict
) -> tuple[_OwnedDocumentAccess, PDFDocumentFile]:
    """The caller's paper and one of its own files; a file of another paper is not found."""

    access = _get_document(db, document_id, user)
    file_row = find_paper_file(access.record, file_id)
    if file_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} not found in document {document_id}",
        )
    return access, file_row


def _owned_stored_path(access: _OwnedDocumentAccess, relative_path: str) -> FilePath:
    storage_root = get_pdf_storage_path()
    return validate_user_file_path(
        storage_root / relative_path,
        storage_root,
        access.owner_auth_sub,
    )


@router.get("/documents/{document_id}/files", response_model=DocumentFilesResponse)
def list_document_files(
    document_id: UUID = Path(..., description="UUID of the paper"),
    db: Session = Depends(get_db),
    user: dict = get_auth_dependency(),
) -> DocumentFilesResponse:
    """Every file of the paper, in tab order, with URLs for the files the viewer can show."""

    record = _get_document(db, document_id, user).record
    return DocumentFilesResponse(document_id=record.id, files=viewer_file_refs(record))


@router.get("/documents/{document_id}/files/{file_id}/content", response_class=FileResponse)
@router.head("/documents/{document_id}/files/{file_id}/content", include_in_schema=False)
def get_document_file_content(
    document_id: UUID = Path(..., description="UUID of the paper"),
    file_id: UUID = Path(..., description="UUID of one of the paper's files"),
    db: Session = Depends(get_db),
    user: dict = get_auth_dependency(),
) -> FileResponse:
    """Serve one ready PDF file's stored bytes, with byte ranges, without exposing its storage path."""

    access, file_row = _owned_paper_file(db, document_id, file_id, user)
    not_available = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"PDF not available for file {file_id}",
    )
    if not serves_pdf(file_row):
        raise not_available
    file_path = _owned_stored_path(access, file_row.file_path)
    if not file_path.is_file():
        logger.warning(
            "Stored PDF missing for a ready file",
            extra={"document_id": str(document_id), "file_id": str(file_id)},
        )
        raise not_available

    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=file_row.display_name,
        content_disposition_type="inline",
    )


@router.get("/documents/{document_id}/files/{file_id}/text", response_model=FileTextResponse)
def get_document_file_text(
    document_id: UUID = Path(..., description="UUID of the paper"),
    file_id: UUID = Path(..., description="UUID of one of the paper's files"),
    db: Session = Depends(get_db),
    user: dict = get_auth_dependency(),
) -> FileTextResponse:
    """One ready text file's display blocks, built from its stored processed elements."""

    access, file_row = _owned_paper_file(db, document_id, file_id, user)
    not_available = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Text not available for file {file_id}",
    )
    if not serves_text(file_row):
        raise not_available
    if file_row.processed_json_path is None:
        logger.warning(
            "Processed elements not recorded for a ready text file",
            extra={"document_id": str(document_id), "file_id": str(file_id)},
        )
        raise not_available
    elements_path = _owned_stored_path(access, file_row.processed_json_path)
    if not elements_path.is_file():
        logger.warning(
            "Processed elements missing for a ready text file",
            extra={"document_id": str(document_id), "file_id": str(file_id)},
        )
        raise not_available

    elements = json.loads(elements_path.read_text(encoding="utf-8"))
    try:
        blocks = build_display_blocks(elements)
    except UnsupportedDisplayElementError as exc:
        logger.warning(
            "Processed elements of a text file cannot be shown: %s",
            exc,
            extra={"document_id": str(document_id), "file_id": str(file_id)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"File text could not be shown for file {file_id}",
        ) from exc

    return FileTextResponse(
        document_id=access.record.id,
        file_id=file_row.id,
        display_name=file_row.display_name,
        blocks=blocks,
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
    user: dict = get_auth_dependency(),
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
