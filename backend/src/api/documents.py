"""Documents API endpoints for Weaviate Control Panel.

Task: T026 - Add Security() dependency injection to document endpoints
All endpoints now require valid AWS Cognito JWT token via Security(auth.get_user).
"""

from fastapi import APIRouter, HTTPException, Query, Path, UploadFile, File, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from typing import Dict, Any
from typing import Optional, List
from datetime import datetime, timezone
from pathlib import Path as FilePath
import logging
import asyncio
import json
import uuid
import os
import shutil
import time
import base64

import httpx

from .auth import get_auth_dependency

from ..services.user_service import principal_from_claims, provision_user
from ..lib.weaviate_helpers import get_tenant_name

from ..models.api_schemas import (
    DocumentListResponse,
    DocumentFilter,
    SortBy,
    SortOrder,
    OperationResult,
    DocumentResponse
)
from ..models.document import EmbeddingStatus, ProcessingStatus
from ..lib.weaviate_client.documents import (
    async_list_documents as list_documents,
    get_document,
    delete_document,
)
from ..lib.pipeline.tracker import PipelineTracker
from ..lib.pdf_jobs import service as pdf_job_service
from ..lib.pdf_jobs.upload_execution_service import (
    UploadExecutionService,
)
from ..lib.document_cleanup import cleanup_document_curation_dependencies
from ..lib.pdf_jobs.upload_intake_service import (
    UploadIntakeDuplicateError,
    UploadIntakeService,
    UploadIntakeValidationError,
)
from ..services.processing_status_policy import (
    ACTIVE_PDF_JOB_STATUSES as _ACTIVE_PDF_JOB_STATUSES,
    ACTIVE_PROCESSING_STATUSES as _ACTIVE_PROCESSING_STATUSES,
    PDF_JOB_STATUS_TO_PROCESSING_STATUS as _PDF_JOB_STATUS_TO_PROCESSING_STATUS,
    PIPELINE_STAGE_TO_PROCESSING_STATUS as _PIPELINE_STAGE_TO_PROCESSING_STATUS,
    TERMINAL_PDF_JOB_STATUSES as _TERMINAL_PDF_JOB_STATUSES,
    is_pipeline_status_active as _is_pipeline_status_active,
    is_pipeline_status_terminal as _is_pipeline_status_terminal,
    normalize_processing_status as _normalize_processing_status,
    pipeline_stage_value as _pipeline_stage_value,
    processing_status_for_pipeline_stage,
)
from ..config import get_pdf_storage_path
from ..models.sql.database import SessionLocal
from ..models.sql.pdf_document import PDFDocument as ViewerPDFDocument
from ..models.sql.pdf_processing_job import PdfJobStatus
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..schemas.documents import DocumentUpdateRequest, DocumentUpdateResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/weaviate")
pipeline_tracker = PipelineTracker()
upload_execution_service = UploadExecutionService(pipeline_tracker=pipeline_tracker)
upload_intake_service = UploadIntakeService(upload_execution_service=upload_execution_service)

_pdf_extraction_service_token: Optional[str] = None
_pdf_extraction_service_token_expires_at: float = 0.0


def _effective_processing_status(raw_document_status: Any, pipeline_status: Any) -> str:
    """Compute effective status with pipeline tracker precedence when present."""
    effective = _normalize_processing_status(raw_document_status)
    if not pipeline_status:
        return effective

    stage_value = _pipeline_stage_value(pipeline_status)
    return _PIPELINE_STAGE_TO_PROCESSING_STATUS.get(stage_value, effective)


def _extract_document_processing_status(doc_payload: Dict[str, Any]) -> str:
    """Extract and normalize processing status from mixed payload key styles."""
    raw_status = doc_payload.get("processing_status", doc_payload.get("processingStatus"))
    return _normalize_processing_status(raw_status)


def _pipeline_status_payload_with_job_precedence(*, pipeline_status: Any, job: Any) -> Optional[Dict[str, Any]]:
    """Build status payload with durable-job precedence over stale tracker terminals."""
    if job:
        if job.status in _ACTIVE_PDF_JOB_STATUSES and pipeline_status and _is_pipeline_status_active(pipeline_status):
            return pipeline_status.model_dump()
        return _pipeline_payload_from_job(job)
    if pipeline_status:
        return pipeline_status.model_dump()
    return None


def _canonical_processing_status(
    *,
    sql_processing_status: Any,
    weaviate_processing_status: Any,
    pipeline_status: Any,
    job: Any,
) -> str:
    """Resolve effective processing status using durable job precedence."""
    raw_sql_status = str(sql_processing_status or "").strip()
    fallback_status = _normalize_processing_status(
        raw_sql_status if raw_sql_status else weaviate_processing_status
    )

    if job:
        return _PDF_JOB_STATUS_TO_PROCESSING_STATUS.get(job.status, fallback_status)
    if pipeline_status:
        return _effective_processing_status(fallback_status, pipeline_status)
    return fallback_status


def _status_snapshot_from_pipeline(pipeline_status: Any) -> Dict[str, Any]:
    payload = pipeline_status.model_dump()
    stage_str = _pipeline_stage_value(pipeline_status)
    progress_value = payload.get("progress_percentage", 0)
    normalized_status = processing_status_for_pipeline_stage(stage_str)
    if normalized_status == ProcessingStatus.COMPLETED.value:
        message_value = payload.get("message") or "Processing completed successfully"
    elif normalized_status == ProcessingStatus.FAILED.value:
        message_value = payload.get("message") or "Processing failed"
    else:
        message_value = payload.get("message") or "Processing document..."
    updated_at = payload.get("updated_at")
    if isinstance(updated_at, datetime):
        timestamp_value = updated_at.isoformat()
    else:
        timestamp_value = updated_at or datetime.now(timezone.utc).isoformat()

    return {
        "source": "pipeline",
        "stage": stage_str,
        "progress": progress_value,
        "message": message_value,
        "status": normalized_status,
        "updated_at": timestamp_value,
        "is_terminal": _is_pipeline_status_terminal(pipeline_status),
    }


def _status_snapshot_from_job(job: Any) -> Dict[str, Any]:
    mapped_status = _PDF_JOB_STATUS_TO_PROCESSING_STATUS.get(job.status, ProcessingStatus.PENDING.value)
    stage_value = job.current_stage or mapped_status
    if job.status in _TERMINAL_PDF_JOB_STATUSES:
        stage_value = mapped_status

    if job.status == PdfJobStatus.COMPLETED.value:
        message_value = "Processing completed successfully"
    elif job.status == PdfJobStatus.CANCELLED.value:
        message_value = job.message or "Processing cancelled"
    elif job.status == PdfJobStatus.FAILED.value:
        message_value = job.error_message or job.message or "Processing failed"
    else:
        message_value = job.message or "Processing document..."

    progress_value = int(getattr(job, "progress_percentage", 0) or 0)
    if job.status == PdfJobStatus.COMPLETED.value:
        progress_value = 100

    return {
        "source": "job",
        "stage": stage_value,
        "progress": progress_value,
        "message": message_value,
        "status": mapped_status,
        "updated_at": job.updated_at.isoformat() if job.updated_at else datetime.now(timezone.utc).isoformat(),
        "is_terminal": job.status in _TERMINAL_PDF_JOB_STATUSES,
    }


def _select_progress_snapshot(*, pipeline_status: Any, job: Any) -> Optional[Dict[str, Any]]:
    """Choose stream snapshot with durable-job lifecycle precedence."""
    if job:
        if job.status in _TERMINAL_PDF_JOB_STATUSES:
            return _status_snapshot_from_job(job)
        if job.status in _ACTIVE_PDF_JOB_STATUSES and pipeline_status and _is_pipeline_status_active(pipeline_status):
            return _status_snapshot_from_pipeline(pipeline_status)
        return _status_snapshot_from_job(job)

    if pipeline_status:
        return _status_snapshot_from_pipeline(pipeline_status)
    return None


async def cleanup_phantom_documents(user: Dict[str, Any]) -> int:
    """Clean up phantom documents - records in PostgreSQL that don't exist in Weaviate.

    This prevents the "invisible documents" issue where a user has documents in the
    database but they don't appear in the UI because the Weaviate records are missing.

    Args:
        user: Authenticated user dict with 'sub' key

    Returns:
        Number of phantom documents cleaned up
    """
    from ..lib.weaviate_helpers import get_connection, get_user_collections
    from ..models.sql.user import User

    user_id = user["sub"]
    cleaned_count = 0

    logger.info('[Phantom Check] Starting phantom document check for user %s...', user_id[:8])

    try:
        # Step 1: Get user's database ID
        session = SessionLocal()
        try:
            db_user = session.query(User).filter(User.auth_sub == user_id).first()
            if not db_user:
                logger.info('[Phantom Check] User not provisioned yet, skipping')
                return 0  # User not provisioned yet

            # Step 2: Get all document IDs from PostgreSQL for this user
            pg_docs = session.query(ViewerPDFDocument).filter(
                ViewerPDFDocument.user_id == db_user.id
            ).all()

            if not pg_docs:
                logger.info('[Phantom Check] No documents in PostgreSQL, nothing to check')
                return 0  # No documents to check

            pg_doc_ids = {str(doc.id) for doc in pg_docs}
            logger.info('[Phantom Check] Found %s documents in PostgreSQL', len(pg_doc_ids))

            # Step 3: Get all document IDs from Weaviate for this user's tenant
            connection = get_connection()
            if not connection:
                logger.warning("No Weaviate connection for phantom cleanup")
                return 0

            weaviate_doc_ids = set()
            with connection.session() as client:
                try:
                    _, pdf_collection = get_user_collections(client, user_id)

                    # Fetch all documents in the tenant (just IDs)
                    response = pdf_collection.query.fetch_objects(
                        limit=1000,  # Should be enough for most users
                        include_vector=False
                    )

                    weaviate_doc_ids = {str(obj.uuid) for obj in response.objects}
                    logger.info('[Phantom Check] Found %s documents in Weaviate', len(weaviate_doc_ids))

                except Exception as e:
                    logger.warning('[Phantom Check] Error fetching Weaviate documents: %s', e)
                    return 0

            # Step 4: Find inconsistencies in both directions
            # Phantom = in PostgreSQL but NOT in Weaviate (user can't see their doc)
            # Orphan = in Weaviate but NOT in PostgreSQL (leftover data)
            phantom_ids = pg_doc_ids - weaviate_doc_ids
            orphan_ids = weaviate_doc_ids - pg_doc_ids

            if not phantom_ids and not orphan_ids:
                logger.info('[Phantom Check] ✓ All clean - %s documents in sync', len(pg_doc_ids))
                return 0

            # Step 5a: Delete phantom records from PostgreSQL
            if phantom_ids:
                logger.warning('[Phantom Check] Found %s phantom documents (in PG, not in Weaviate)', len(phantom_ids))

                for phantom_id in phantom_ids:
                    phantom_doc = session.query(ViewerPDFDocument).filter(
                        ViewerPDFDocument.id == uuid.UUID(phantom_id)
                    ).first()

                    if phantom_doc:
                        # Also clean up filesystem artifacts if they exist
                        try:
                            base_storage = get_pdf_storage_path()
                            if phantom_doc.file_path:
                                file_path = FilePath(base_storage) / phantom_doc.file_path
                                doc_dir = file_path.parent
                                if doc_dir.exists():
                                    shutil.rmtree(doc_dir)
                                    logger.info('[Phantom Check] Cleaned up filesystem for phantom doc %s', phantom_id)
                        except Exception as fs_err:
                            logger.warning('[Phantom Check] Failed to cleanup files for phantom %s: %s', phantom_id, fs_err)

                        cleanup_document_curation_dependencies(session, phantom_doc.id)
                        session.delete(phantom_doc)
                        cleaned_count += 1
                        logger.info('[Phantom Check] Deleted phantom PG record %s (%s)', phantom_id, phantom_doc.filename)

                session.commit()

            # Step 5b: Delete orphan records from Weaviate
            if orphan_ids:
                logger.warning('[Phantom Check] Found %s orphan documents (in Weaviate, not in PG)', len(orphan_ids))

                with connection.session() as client:
                    try:
                        _, pdf_collection = get_user_collections(client, user_id)

                        for orphan_id in orphan_ids:
                            try:
                                pdf_collection.data.delete_by_id(uuid.UUID(orphan_id))
                                cleaned_count += 1
                                logger.info('[Phantom Check] Deleted orphan Weaviate doc %s', orphan_id)
                            except Exception as del_err:
                                logger.warning('[Phantom Check] Failed to delete orphan %s: %s', orphan_id, del_err)

                    except Exception as e:
                        logger.error('[Phantom Check] Error cleaning orphan Weaviate docs: %s', e)

            logger.info('[Phantom Check] Cleaned up %s inconsistent documents for user %s', cleaned_count, user_id[:8])

        finally:
            session.close()

    except Exception as e:
        logger.error('Error during phantom document cleanup: %s', e)

    return cleaned_count


def verify_document_ownership(
    db: Session,
    document_id: str,
    auth_user: Dict[str, Any]
) -> ViewerPDFDocument:
    """Verify document ownership and return document if authorized.

    Args:
        db: Database session
        document_id: Document UUID to check
        auth_user: Authenticated user from AWS Cognito JWT

    Returns:
        ViewerPDFDocument if user owns the document

    Raises:
        HTTPException: 404 if document not found, 403 if not owned by user

    Requirements: FR-014 (cross-user access prevention with 403)
    """
    # Get database user
    db_user = provision_user(db, principal_from_claims(auth_user))

    # Query document from PostgreSQL
    document_uuid = _parse_document_uuid(document_id)
    doc = db.query(ViewerPDFDocument).filter(
        ViewerPDFDocument.id == document_uuid
    ).first()

    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"Document with ID {document_id} not found"
        )

    # Verify ownership - return 403 for cross-user access (FR-014)
    if doc.user_id != db_user.id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this document"
        )

    return doc


def _parse_document_uuid(document_id: str) -> uuid.UUID:
    """Parse document_id and raise a client error for malformed UUID values."""
    try:
        return uuid.UUID(document_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid document ID format: {document_id}",
        ) from exc


def validate_user_file_path(
    file_path: FilePath,
    storage_root: FilePath,
    user_id: str
) -> FilePath:
    """Validate that a resolved file path stays within the user's storage directory.

    Args:
        file_path: The resolved absolute file path to validate
        storage_root: The base storage directory (e.g., pdf_storage/)
        user_id: User's user ID for user-specific path validation

    Returns:
        The validated absolute file path

    Raises:
        HTTPException: 403 if path escapes user's storage directory

    Requirements:
        - T032: User-specific file paths with validation
        - FR-014: Prevent path traversal attacks

    Example:
        >>> storage = Path("/runtime/state/pdf_storage")
        >>> user_path = storage / "00u1abc2" / "doc.pdf"
        >>> validate_user_file_path(user_path, storage, "00u1abc2")
        Path("/runtime/state/pdf_storage/00u1abc2/doc.pdf")
    """
    try:
        # Resolve to absolute path (eliminates ../, symlinks, etc.)
        resolved_path = file_path.resolve()
        storage_root_resolved = storage_root.resolve()

        # User's storage must be under: storage_root / user_id /
        user_storage_root = (storage_root_resolved / user_id).resolve()

        # Check if resolved path is within user's storage directory
        # Using relative_to() - raises ValueError if path is outside
        try:
            resolved_path.relative_to(user_storage_root)
        except ValueError:
            logger.warning(
                'Path traversal attempt detected: %s resolves outside user storage for %s', file_path, user_id)
            raise HTTPException(
                status_code=403,
                detail="Access denied: file path validation failed"
            )

        return resolved_path

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Error validating file path %s: %s', file_path, e)
        raise HTTPException(
            status_code=500,
            detail="File path validation error"
        )


def _pipeline_payload_from_job(job: Any) -> Optional[Dict[str, Any]]:
    """Build pipeline-like status payload from durable PDF job."""
    if not job:
        return None

    if hasattr(job, "model_dump"):
        payload = job.model_dump()
    elif isinstance(job, dict):
        payload = job
    else:
        payload = {
            "document_id": getattr(job, "document_id", None),
            "current_stage": getattr(job, "current_stage", None),
            "started_at": getattr(job, "started_at", None),
            "updated_at": getattr(job, "updated_at", None),
            "completed_at": getattr(job, "completed_at", None),
            "progress_percentage": getattr(job, "progress_percentage", 0),
            "message": getattr(job, "message", None),
            "status": getattr(job, "status", None),
        }

    updated_at = payload.get("updated_at")
    started_at = payload.get("started_at")
    completed_at = payload.get("completed_at")

    return {
        "document_id": payload.get("document_id"),
        "current_stage": payload.get("current_stage") or "pending",
        "started_at": started_at,
        "updated_at": updated_at,
        "completed_at": completed_at,
        "progress_percentage": payload.get("progress_percentage", 0),
        "message": payload.get("message"),
        "error_count": 1 if payload.get("status") == PdfJobStatus.FAILED.value else 0,
        "stage_results": [],
    }


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents_endpoint(
    user: Dict[str, Any] = get_auth_dependency(),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search term for filename"),
    embedding_status: Optional[List[EmbeddingStatus]] = Query(None, description="Filter by embedding status"),
    sort_by: SortBy = Query(SortBy.CREATION_DATE, description="Sort field"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort direction"),
    date_from: Optional[datetime] = Query(None, description="Filter by creation date start"),
    date_to: Optional[datetime] = Query(None, description="Filter by creation date end"),
    min_vector_count: Optional[int] = Query(None, ge=0, description="Minimum vector count"),
    max_vector_count: Optional[int] = Query(None, ge=0, description="Maximum vector count")
):
    """
    List all PDF documents stored in Weaviate with pagination and filtering.

    Returns paginated list of documents with their metadata and processing status.

    Note: This endpoint automatically cleans up "phantom" documents - records that
    exist in PostgreSQL but are missing from Weaviate. This ensures users always
    see an accurate document list.
    """
    try:
        # Clean up any phantom documents before listing
        # This handles cases where documents exist in PostgreSQL but not Weaviate
        phantoms_cleaned = await cleanup_phantom_documents(user)
        if phantoms_cleaned > 0:
            logger.info('Cleaned up %s phantom documents before listing', phantoms_cleaned)

        doc_filter = DocumentFilter(
            search_term=search,
            embedding_status=embedding_status,
            date_from=date_from,
            date_to=date_to,
            min_vector_count=min_vector_count,
            max_vector_count=max_vector_count
        )

        if not doc_filter.is_date_range_valid:
            raise HTTPException(
                status_code=400,
                detail="Invalid date range: date_from must be before date_to"
            )

        if not doc_filter.is_vector_range_valid:
            raise HTTPException(
                status_code=400,
                detail="Invalid vector range: min_vector_count must be less than max_vector_count"
            )

        pagination = {
            "page": page,
            "page_size": page_size,
            "sort_by": sort_by.value,
            "sort_order": sort_order.value
        }

        # Pass user["sub"] for tenant scoping (FR-011, FR-014)
        result = await list_documents(user["sub"], doc_filter, pagination)

        # T030: Return contract-compliant flat structure
        return DocumentListResponse(
            documents=result["documents"],
            total=result["total"],
            limit=result["limit"],
            offset=result["offset"]
        )

    except Exception as e:
        logger.error('Error listing documents: %s', e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve documents: {str(e)}"
        )


async def _build_pdf_extraction_service_headers() -> Dict[str, str]:
    """Build service-auth headers for proxy endpoints that require auth (status/wake)."""
    global _pdf_extraction_service_token, _pdf_extraction_service_token_expires_at

    auth_mode = os.getenv("PDF_EXTRACTION_AUTH_MODE", "none").strip().lower()
    if auth_mode == "none":
        return {}

    if auth_mode == "static_bearer":
        token = os.getenv("PDF_EXTRACTION_BEARER_TOKEN", "").strip()
        if not token:
            raise HTTPException(
                status_code=500,
                detail="PDF_EXTRACTION_BEARER_TOKEN is required when PDF_EXTRACTION_AUTH_MODE=static_bearer",
            )
        return {"Authorization": f"Bearer {token}"}

    if auth_mode != "cognito_client_credentials":
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported PDF_EXTRACTION_AUTH_MODE: {auth_mode}",
        )

    now = time.monotonic()
    if _pdf_extraction_service_token and now < (_pdf_extraction_service_token_expires_at - 30):
        return {"Authorization": f"Bearer {_pdf_extraction_service_token}"}

    token_url = os.getenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "").strip()
    if not token_url:
        cognito_domain = os.getenv("COGNITO_DOMAIN", "").strip().rstrip("/")
        if not cognito_domain:
            raise HTTPException(
                status_code=500,
                detail="Set PDF_EXTRACTION_COGNITO_TOKEN_URL or COGNITO_DOMAIN for PDF extraction service auth",
            )
        token_url = f"{cognito_domain}/oauth2/token"

    client_id = os.getenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "").strip()
    client_secret = os.getenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "").strip()
    scope = os.getenv("PDF_EXTRACTION_COGNITO_SCOPE", "").strip()
    if not client_id or not client_secret or not scope:
        raise HTTPException(
            status_code=500,
            detail=(
                "PDF_EXTRACTION_COGNITO_CLIENT_ID, PDF_EXTRACTION_COGNITO_CLIENT_SECRET, "
                "and PDF_EXTRACTION_COGNITO_SCOPE are required for cognito_client_credentials auth mode"
            ),
        )

    auth_basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {auth_basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = {"grant_type": "client_credentials", "scope": scope}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(token_url, data=body, headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch PDF extraction service token: {exc}",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch PDF extraction service token ({response.status_code})",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Token endpoint response was not valid JSON") from exc
    access_token = str(payload.get("access_token", "")).strip()
    if not access_token:
        raise HTTPException(status_code=502, detail="Token endpoint response missing access_token")

    expires_in_raw = payload.get("expires_in", 3600)
    try:
        expires_in = int(expires_in_raw)
    except (TypeError, ValueError):
        expires_in = 3600

    _pdf_extraction_service_token = access_token
    _pdf_extraction_service_token_expires_at = time.monotonic() + max(60, expires_in)
    return {"Authorization": f"Bearer {_pdf_extraction_service_token}"}


async def _require_pdf_extraction_worker_ready() -> None:
    """Fail fast when proxy worker is sleeping/stopped."""
    service_url = os.getenv("PDF_EXTRACTION_SERVICE_URL", "").rstrip("/")
    if not service_url:
        return

    status_endpoint = f"{service_url}/api/v1/status"
    health_endpoint = f"{service_url}/api/v1/health"
    timeout_seconds = float(os.getenv("PDF_EXTRACTION_HEALTH_TIMEOUT", "5"))
    headers = await _build_pdf_extraction_service_headers()

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            if headers:
                status_response = await client.get(status_endpoint, headers=headers)
            else:
                status_response = await client.get(health_endpoint)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Unable to reach PDF extraction worker status endpoint: {exc}",
        ) from exc

    if status_response.status_code >= 400:
        raise HTTPException(
            status_code=503,
            detail=f"PDF extraction worker status check failed ({status_response.status_code})",
        )

    if status_response.content:
        try:
            payload = status_response.json()
        except ValueError:
            payload = {}
    else:
        payload = {}
    if isinstance(payload, dict):
        state = str(payload.get("state", "") or payload.get("ec2", "")).strip().lower()
    else:
        state = ""
    if state not in {"ready", "busy"}:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "pdf_extraction_worker_not_ready",
                "worker_state": state or "unknown",
                "message": "PDF extraction worker is sleeping or starting. Wake worker before uploading.",
            },
        )


@router.get("/documents/pdf-extraction-health")
async def get_pdf_extraction_health(user: Dict[str, Any] = get_auth_dependency()):
    """Report health status for the PDF extraction service."""
    del user

    service_url = os.getenv("PDF_EXTRACTION_SERVICE_URL", "").rstrip("/")
    checked_at = datetime.now(timezone.utc).isoformat()

    if not service_url:
        return {
            "status": "misconfigured",
            "service_url": "",
            "last_checked": checked_at,
            "error": "PDF_EXTRACTION_SERVICE_URL is not configured",
        }

    health_endpoint = f"{service_url}/api/v1/health"
    deep_health_endpoint = f"{service_url}/api/v1/health/deep"
    status_endpoint = f"{service_url}/api/v1/status"
    timeout_seconds = float(os.getenv("PDF_EXTRACTION_HEALTH_TIMEOUT", "5"))
    service_headers: Dict[str, str] = {}
    auth_header_error: Optional[str] = None

    try:
        service_headers = await _build_pdf_extraction_service_headers()
    except Exception as exc:
        auth_header_error = str(getattr(exc, "detail", exc))

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            if service_headers:
                response = await client.get(health_endpoint, headers=service_headers)
                deep_response = await client.get(deep_health_endpoint, headers=service_headers)
            else:
                response = await client.get(health_endpoint)
                deep_response = await client.get(deep_health_endpoint)

            worker_state = "unknown"
            status_payload = None
            status_code = None
            status_error = None
            try:
                if service_headers:
                    status_resp = await client.get(status_endpoint, headers=service_headers)
                else:
                    status_resp = await client.get(status_endpoint)
                status_code = status_resp.status_code
                if status_resp.status_code >= 400:
                    status_error = f"Status endpoint returned {status_resp.status_code}"
                elif status_resp.content:
                    status_payload = status_resp.json()
                    if isinstance(status_payload, dict):
                        worker_state = str(status_payload.get("state", "")).strip().lower() or "unknown"
            except Exception as exc:
                status_error = str(exc)
            if auth_header_error and not status_error:
                status_error = auth_header_error

        try:
            payload = response.json()
        except ValueError:
            payload = None

        try:
            deep_payload = deep_response.json()
        except ValueError:
            deep_payload = None

        if worker_state == "unknown" and (status_code is None or status_code < 400):
            worker_state = str((payload or {}).get("ec2", "")).strip().lower() or "unknown"

        worker_available = worker_state in {"ready", "busy"}
        auth_ok = not auth_header_error
        proxy_status = str((payload or {}).get("status", "")).strip().lower()
        proxy_ok = response.status_code == 200 and proxy_status in {"healthy", "degraded", "ok"}
        deep_ok = deep_response.status_code == 200 and str((deep_payload or {}).get("status", "")).strip().lower() == "healthy"

        # Deep health validates auth contract + downstream extract roundtrip and is
        # less susceptible to transient downstream health-flap noise.
        # The proxy being reachable with valid auth is sufficient for "healthy".
        # Worker sleep/wake lifecycle is managed by the extraction service itself
        # and should not surface as degraded to end users.
        status = "healthy" if auth_ok and (proxy_ok or deep_ok) else "degraded"
        error_message = None
        if auth_header_error:
            error_message = auth_header_error
        elif status == "degraded":
            if not proxy_ok and not deep_ok:
                error_message = "All connection attempts failed"
            elif not worker_available:
                error_message = f"Worker {worker_state or 'unknown'}"
            elif status_error:
                error_message = status_error

        return {
            "status": status,
            "service_url": service_url,
            "last_checked": checked_at,
            "response_code": response.status_code,
            "details": payload,
            "deep_details": deep_payload,
            "deep_response_code": deep_response.status_code,
            "worker_state": worker_state,
            "worker_available": worker_available,
            "wake_required": not worker_available,
            "status_details": status_payload,
            "status_response_code": status_code,
            "status_error": status_error,
            "error": error_message,
        }

    except httpx.RequestError as exc:
        logger.warning("PDF extraction health check failed: %s", exc)
        return {
            "status": "unreachable",
            "service_url": service_url,
            "last_checked": checked_at,
            "error": str(exc),
            "worker_state": "unknown",
            "worker_available": False,
            "wake_required": False,
        }


@router.post("/documents/pdf-extraction-wake")
async def wake_pdf_extraction_worker(user: Dict[str, Any] = get_auth_dependency()):
    """Wake PDF extraction worker and return resulting state."""
    del user

    service_url = os.getenv("PDF_EXTRACTION_SERVICE_URL", "").rstrip("/")
    if not service_url:
        raise HTTPException(
            status_code=500,
            detail="PDF_EXTRACTION_SERVICE_URL is not configured",
        )

    wake_endpoint = f"{service_url}/api/v1/wake"
    status_endpoint = f"{service_url}/api/v1/status"
    timeout_seconds = float(os.getenv("PDF_EXTRACTION_HEALTH_TIMEOUT", "5"))
    headers = await _build_pdf_extraction_service_headers()

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            wake_response = await client.post(wake_endpoint, headers=headers)
            if wake_response.content:
                try:
                    wake_payload = wake_response.json()
                except ValueError as exc:
                    raise HTTPException(
                        status_code=502,
                        detail="Wake endpoint returned non-JSON response",
                    ) from exc
            else:
                wake_payload = {}
            status_response = await client.get(status_endpoint, headers=headers)
            if status_response.content:
                try:
                    status_payload = status_response.json()
                except ValueError:
                    status_payload = {}
            else:
                status_payload = {}
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to wake PDF extraction worker: {exc}") from exc

    if wake_response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Wake request failed ({wake_response.status_code}): {wake_payload}",
        )

    worker_state = str(status_payload.get("state", "")).strip().lower() if isinstance(status_payload, dict) else "unknown"
    worker_available = worker_state in {"ready", "busy"}

    return {
        "service_url": service_url,
        "wake_response_code": wake_response.status_code,
        "wake_details": wake_payload,
        "status_response_code": status_response.status_code,
        "status_details": status_payload,
        "worker_state": worker_state or "unknown",
        "worker_available": worker_available,
        "wake_required": not worker_available,
    }


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document_endpoint(
    document_id: str = Path(..., description="Document ID"),
    user: Dict[str, Any] = get_auth_dependency()
):
    """
    Get detailed information about a specific document.

    Returns Document schema with ownership metadata per contract.

    Requirements:
        - FR-014: Verify document ownership, return 403 for cross-user access
        - T031: Return Document schema (not legacy DocumentDetailResponse)
    """
    # T031: Verify ownership from PostgreSQL FIRST (FR-014)
    # Returns 404 if not found, 403 if not owned by user
    session = SessionLocal()
    try:
        pg_doc = verify_document_ownership(session, document_id, user)
        db_user = provision_user(session, principal_from_claims(user))
    finally:
        session.close()

    # Get Weaviate document for chunk_count and status
    try:
        weaviate_doc = await get_document(user["sub"], document_id)

        if not weaviate_doc:
            raise HTTPException(
                status_code=404,
                detail=f"Document with ID {document_id} not found"
            )

        # Extract document data from nested structure
        doc_data = weaviate_doc.get("document", {})
        tenant_name = get_tenant_name(user["sub"])
        latest_job = pdf_job_service.get_latest_job_for_document(
            document_id=document_id,
            user_id=db_user.id,
            reconcile_stale=True,
        )

        # T031: Return contract Document schema (document_endpoints.yaml)
        return DocumentResponse(
            document_id=document_id,
            job_id=latest_job.job_id if latest_job else None,
            user_id=db_user.id,
            filename=pg_doc.filename,
            status=doc_data.get("processing_status", "pending").upper(),  # Contract requires uppercase enum
            upload_timestamp=pg_doc.upload_timestamp,
            processing_started_at=None,  # TODO: track in PostgreSQL
            processing_completed_at=None,  # TODO: track in PostgreSQL
            file_size_bytes=pg_doc.file_size,
            weaviate_tenant=tenant_name,
            chunk_count=doc_data.get("chunk_count"),
            error_message=None  # TODO: track processing errors
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Error retrieving document %s: %s', document_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve document: {str(e)}"
        )


@router.patch("/documents/{document_id}", response_model=DocumentUpdateResponse)
async def update_document_endpoint(
    request: DocumentUpdateRequest,
    document_id: str = Path(..., description="Document ID"),
    user: Dict[str, Any] = get_auth_dependency(),
):
    """
    Update document metadata (e.g., title).

    Allows users to set a custom title for batch processing clarity.

    Requirements:
        - FR-014: Verify document ownership, return 403 for cross-user access
    """
    # Verify ownership from PostgreSQL FIRST (FR-014)
    session = SessionLocal()
    try:
        pg_doc = verify_document_ownership(session, document_id, user)

        # Update title if provided
        if request.title is not None:
            pg_doc.title = request.title
            session.commit()
            logger.debug("Updated document %s title to: %s", document_id, request.title)

        return DocumentUpdateResponse(
            document_id=document_id,
            title=pg_doc.title,
        )
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error('Error updating document %s: %s', document_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update document: {str(e)}"
        )
    finally:
        session.close()


@router.delete("/documents/{document_id}", response_model=OperationResult)
async def delete_document_endpoint(
    document_id: str = Path(..., description="Document ID"),
    user: Dict[str, Any] = get_auth_dependency()
):
    """
    Delete a document and all its associated chunks from Weaviate.

    This operation is cascading and will remove all chunks associated with the document.
    Documents currently being processed cannot be deleted.

    Requirements:
        - FR-014: Verify document ownership, return 403 for cross-user access
    """
    # T031: Verify ownership from PostgreSQL FIRST (FR-014)
    # Returns 404 if not found, 403 if not owned by user
    session = SessionLocal()
    try:
        pg_doc = verify_document_ownership(session, document_id, user)
    finally:
        session.close()

    # Only proceed with Weaviate query if ownership verified
    try:
        latest_job = pdf_job_service.get_latest_job_for_document(
            document_id=document_id,
            user_id=pg_doc.user_id,
            reconcile_stale=True,
        )
        pipeline_status = await pipeline_tracker.get_pipeline_status(document_id)
        active_job_status = latest_job.status if latest_job else None
        pipeline_is_active = _is_pipeline_status_active(pipeline_status)

        document = None
        document_processing_status = "missing"
        document_missing_in_weaviate = False
        try:
            document = await get_document(user["sub"], document_id)
        except ValueError as exc:
            if "not found" in str(exc).lower():
                document_missing_in_weaviate = True
                logger.warning(
                    "Document %s exists in PostgreSQL but is missing in Weaviate; proceeding with stale-record delete checks.",
                    document_id,
                )
            else:
                raise

        if document:
            doc_payload = document.get("document", {})
            document_processing_status = _extract_document_processing_status(doc_payload)

        if active_job_status in _ACTIVE_PDF_JOB_STATUSES:
            job_hint = f" and job status '{active_job_status}'" if active_job_status else ""
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot delete document while it is being processed "
                    f"(status '{document_processing_status}'{job_hint})"
                ),
            )
        if pipeline_is_active:
            stage_value = getattr(getattr(pipeline_status, "current_stage", None), "value", getattr(pipeline_status, "current_stage", None))
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete document while pipeline is actively processing (stage '{stage_value}')",
            )
        if document_processing_status in _ACTIVE_PROCESSING_STATUSES:
            if latest_job and latest_job.status in _TERMINAL_PDF_JOB_STATUSES:
                logger.warning(
                    "Allowing delete for stale document processing status=%s with terminal job status=%s",
                    document_processing_status,
                    latest_job.status,
                )
            else:
                job_hint = f" and job status '{active_job_status}'" if active_job_status else ""
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cannot delete document while it is being processed "
                        f"(status '{document_processing_status}'{job_hint})"
                    ),
                )

        result = {"success": True, "chunks_deleted": 0}
        if not document_missing_in_weaviate:
            result = await delete_document(user["sub"], document_id)

        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=result.get("message", "Failed to delete document")
            )

        # Cleanup PostgreSQL and Filesystem (Phase 1 Fix)
        cleanup_session = SessionLocal()
        try:
            # Find record to delete
            doc_to_delete = cleanup_session.execute(
                select(ViewerPDFDocument).where(ViewerPDFDocument.id == uuid.UUID(document_id))
            ).scalars().first()

            if doc_to_delete:
                cleanup_document_curation_dependencies(cleanup_session, doc_to_delete.id)
                # Delete physical files
                try:
                    from ..config import get_pdf_storage_path
                    base_storage = get_pdf_storage_path()

                    # 1. Delete PDF Directory: {user_id}/{doc_id}/
                    if doc_to_delete.file_path:
                        file_path_obj = FilePath(base_storage) / doc_to_delete.file_path
                        doc_dir = file_path_obj.parent

                        # Sanity check: Ensure we are deleting a subdirectory of storage
                        if doc_dir.exists() and base_storage in doc_dir.parents:
                            shutil.rmtree(doc_dir)
                            logger.info('Deleted filesystem artifacts for document %s', document_id)

                    # 2. Delete PDFX JSON: {user_id}/pdfx_json/{doc_id}.json
                    if doc_to_delete.pdfx_json_path:
                        pdfx_path = FilePath(base_storage) / doc_to_delete.pdfx_json_path
                        if pdfx_path.exists() and base_storage in pdfx_path.parents:
                            pdfx_path.unlink()
                            logger.info('Deleted PDFX JSON for %s', document_id)

                    # 3. Delete Processed JSON: {user_id}/processed_json/{doc_id}.json
                    if doc_to_delete.processed_json_path:
                        processed_path = FilePath(base_storage) / doc_to_delete.processed_json_path
                        if processed_path.exists() and base_storage in processed_path.parents:
                            processed_path.unlink()
                            logger.info('Deleted Processed JSON for %s', document_id)

                except Exception as fs_error:
                    logger.error('Failed to clean up files for %s: %s', document_id, fs_error)

                # Delete DB record
                cleanup_session.delete(doc_to_delete)
                cleanup_session.commit()
                logger.info('Deleted PostgreSQL record for %s', document_id)

                # Invalidate document metadata cache to prevent stale cache hits
                from src.lib.document_cache import invalidate_cache
                invalidate_cache(user["sub"], document_id)
        except Exception as db_error:
            logger.error('Failed to cleanup PostgreSQL for %s: %s', document_id, db_error)
        finally:
            cleanup_session.close()

        return OperationResult(
            success=True,
            message=f"Document {document_id} and {result['chunks_deleted']} chunks deleted successfully",
            operation="delete_document",
            document_id=document_id
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Error deleting document %s: %s', document_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete document: {str(e)}"
        )


@router.post("/documents/upload", response_model=DocumentResponse, status_code=201)
async def upload_document_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="PDF file to upload"),
    user: Dict[str, Any] = get_auth_dependency()
):
    """
    Upload a PDF document for processing.

    This endpoint accepts a PDF file upload, validates it, saves it,
    and initiates the processing pipeline (parsing, chunking, embedding, storing).
    Processing happens in the background.

    Requirements:
        - FR-012: Store in user-specific storage location
        - FR-013: Associate embeddings with uploading user
        - FR-016: Track ownership through document lifecycle
        - T029: Return Document schema with ownership metadata (user_id, weaviate_tenant)
    """
    try:
        intake_result = await upload_intake_service.intake_upload(
            background_tasks=background_tasks,
            file=file,
            user=user,
        )
        return DocumentResponse(
            document_id=intake_result.document_id,
            job_id=intake_result.job_id,
            user_id=intake_result.user_id,
            filename=intake_result.filename,
            status=intake_result.status,
            upload_timestamp=intake_result.upload_timestamp,
            processing_started_at=intake_result.processing_started_at,
            processing_completed_at=intake_result.processing_completed_at,
            file_size_bytes=intake_result.file_size_bytes,
            weaviate_tenant=intake_result.weaviate_tenant,
            chunk_count=intake_result.chunk_count,
            error_message=intake_result.error_message,
        )
    except UploadIntakeValidationError as validation_error:
        raise HTTPException(status_code=400, detail=str(validation_error)) from validation_error
    except UploadIntakeDuplicateError as duplicate_error:
        raise HTTPException(status_code=409, detail=duplicate_error.detail) from duplicate_error
    except Exception as e:
        logger.error('Error uploading document: %s', e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload document: {str(e)}"
        )


@router.get("/documents/{document_id}/status")
async def get_document_processing_status(
    document_id: str = Path(..., description="Document ID"),
    user: Dict[str, Any] = get_auth_dependency()
):
    """
    Get the current processing status of a document.

    Returns the document's current processing stage and progress information.

    Requirements:
        - FR-014: Verify document ownership, return 403 for cross-user access
    """
    # T031: Verify ownership from PostgreSQL FIRST (FR-014)
    # Returns 404 if not found, 403 if not owned by user
    session = SessionLocal()
    try:
        pg_doc = verify_document_ownership(session, document_id, user)
        db_user = provision_user(session, principal_from_claims(user))
    finally:
        session.close()

    # Only proceed with Weaviate query if ownership verified
    try:
        # Get document
        document = await get_document(user["sub"], document_id)
        if not document:
            raise HTTPException(
                status_code=404,
                detail=f"Document with ID {document_id} not found"
            )

        # Get pipeline status (in-memory) and durable job fallback.
        pipeline_status = await pipeline_tracker.get_pipeline_status(document_id)
        job = pdf_job_service.get_latest_job_for_document(
            document_id=document_id,
            user_id=db_user.id,
            reconcile_stale=True,
        )
        doc_payload = document.get("document", {})
        raw_processing_status = doc_payload.get("processing_status", doc_payload.get("processingStatus"))
        processing_status = _canonical_processing_status(
            sql_processing_status=getattr(pg_doc, "status", None),
            weaviate_processing_status=raw_processing_status,
            pipeline_status=pipeline_status,
            job=job,
        )
        pipeline_payload = _pipeline_status_payload_with_job_precedence(
            pipeline_status=pipeline_status,
            job=job,
        )

        return {
            "document_id": document_id,
            "processing_status": processing_status,
            "embedding_status": document["document"].get("embedding_status", "pending"),
            "pipeline_status": pipeline_payload,
            "job_id": job.job_id if job else None,
            "job_status": job.status if job else None,
            "cancel_requested": bool(job.cancel_requested) if job else False,
            "chunk_count": document.get("total_chunks", 0),
            "vector_count": document["document"].get("vector_count", 0)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Error getting document status: %s', e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get document status: {str(e)}"
        )


@router.get("/documents/{document_id}/progress/stream")
async def stream_document_progress(
    document_id: str = Path(..., description="Document ID"),
    user: Dict[str, Any] = get_auth_dependency()
):
    """
    Stream real-time processing progress via Server-Sent Events (SSE).

    This endpoint provides real-time updates on document processing status,
    allowing CLI tools and UI components to monitor progress without polling.

    Returns:
        StreamingResponse: SSE stream with progress updates

    Event Format:
        data: {
            "stage": "parsing",
            "progress": 45,
            "message": "Extracting text from PDF",
            "timestamp": "2025-01-24T10:30:00Z"
        }
    """
    session = SessionLocal()
    try:
        try:
            verify_document_ownership(session, document_id, user)
        except HTTPException as exc:
            # Keep SSE behavior backward-compatible for malformed/missing IDs:
            # emit terminal stream events instead of failing the HTTP handshake.
            if exc.status_code not in {400, 404}:
                raise
        db_user = provision_user(session, principal_from_claims(user))
    finally:
        session.close()

    async def generate():
        """Generate SSE events for document processing progress."""
        last_status_snapshot = None
        retry_count = 0
        poll_interval_raw = os.getenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
        timeout_raw = os.getenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS") or os.getenv("PDF_EXTRACTION_TIMEOUT", "3600")

        try:
            poll_interval_seconds = int(poll_interval_raw)
            if poll_interval_seconds <= 0:
                raise ValueError
        except (TypeError, ValueError):
            logger.warning(
                "Invalid PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS=%r; defaulting to 1",
                poll_interval_raw,
            )
            poll_interval_seconds = 1

        try:
            timeout_seconds = int(timeout_raw)
            if timeout_seconds <= 0:
                raise ValueError
        except (TypeError, ValueError):
            logger.warning(
                "Invalid PDF_PROCESSING_SSE_TIMEOUT_SECONDS/PDF_EXTRACTION_TIMEOUT=%r; defaulting to 3600",
                timeout_raw,
            )
            timeout_seconds = 3600

        max_retries = max(1, timeout_seconds // poll_interval_seconds)
        timeout_minutes = timeout_seconds / 60
        timeout_deadline = time.monotonic() + timeout_seconds

        try:
            # First, check if document exists
            document = await get_document(user["sub"], document_id)
            if not document:
                error_data = {
                    'error': 'Document not found',
                    'document_id': document_id
                }
                yield f"data: {json.dumps(error_data)}\n\n"
                return

            while retry_count < max_retries:
                # Get current pipeline status
                pipeline_status = await pipeline_tracker.get_pipeline_status(document_id)
                try:
                    job = pdf_job_service.get_latest_job_for_document(
                        document_id=document_id,
                        user_id=db_user.id,
                        reconcile_stale=True,
                    )
                except (TypeError, ValueError):
                    job = None

                status_snapshot = _select_progress_snapshot(
                    pipeline_status=pipeline_status,
                    job=job,
                )
                if status_snapshot:
                    if status_snapshot != last_status_snapshot:
                        event_data = {
                            'stage': status_snapshot["stage"],
                            'progress': status_snapshot["progress"],
                            'message': status_snapshot["message"],
                            'timestamp': status_snapshot["updated_at"],
                            'source': status_snapshot["source"],
                        }

                        yield f"data: {json.dumps(event_data)}\n\n"
                        last_status_snapshot = status_snapshot

                        if status_snapshot["is_terminal"]:
                            final_data = {
                                'stage': status_snapshot["stage"],
                                'progress': status_snapshot["progress"],
                                'message': status_snapshot["message"],
                                'timestamp': status_snapshot["updated_at"],
                                'source': status_snapshot["source"],
                                'final': True,
                            }
                            yield f"data: {json.dumps(final_data)}\n\n"
                            break
                else:
                    # No status yet, send initial waiting message
                    if retry_count == 0:
                        waiting_data = {
                            'stage': 'waiting',
                            'progress': 0,
                            'message': 'Waiting for processing to start...',
                            'timestamp': datetime.now().isoformat()
                        }
                        yield f"data: {json.dumps(waiting_data)}\n\n"

                remaining_seconds = timeout_deadline - time.monotonic()
                if remaining_seconds <= 0:
                    break

                await asyncio.sleep(min(poll_interval_seconds, remaining_seconds))
                retry_count += 1

            # If we've exceeded max retries, send timeout message
            if retry_count >= max_retries or time.monotonic() >= timeout_deadline:
                timeout_data = {
                    'stage': 'timeout',
                    'progress': 0,
                    'message': f'Progress monitoring timed out after {timeout_minutes:g} minutes',
                    'timestamp': datetime.now().isoformat(),
                    'final': True
                }
                yield f"data: {json.dumps(timeout_data)}\n\n"

        except Exception as e:
            logger.error('Error in SSE stream for document %s: %s', document_id, e)
            error_data = {
                'error': str(e),
                'document_id': document_id,
                'timestamp': datetime.now().isoformat()
            }
            yield f"data: {json.dumps(error_data)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.get("/documents/{document_id}/download-info")
async def get_download_info(
    document_id: str = Path(..., description="Document ID"),
    user: Dict[str, Any] = get_auth_dependency()
):
    """
    Get information about downloadable files for a document.

    Returns availability and sizes of PDF, PDFX JSON, and processed JSON files.

    Requirements:
        - FR-014: Verify document ownership before serving download info
        - Return 403 Forbidden for cross-user access attempts
    """
    try:
        from ..config import get_pdf_storage_path
        document_uuid = _parse_document_uuid(document_id)

        # Get document from database to check file paths
        session = SessionLocal()
        try:
            # T031: Get database user for ownership verification (FR-014)
            db_user = provision_user(session, principal_from_claims(user))

            doc = session.query(ViewerPDFDocument).filter(
                ViewerPDFDocument.id == document_uuid
            ).first()

            if not doc:
                raise HTTPException(
                    status_code=404,
                    detail=f"Document with ID {document_id} not found"
                )

            # T031: Ownership check (FR-014) - Return 403 for cross-user access
            if doc.user_id != db_user.id:
                raise HTTPException(
                    status_code=403,
                    detail="You do not have permission to access this document"
                )

            # Check PDF file with path validation (T032)
            pdf_storage = get_pdf_storage_path()
            pdf_path = None
            pdf_available = False
            pdf_size = None
            if doc.file_path:
                pdf_path = FilePath(pdf_storage) / doc.file_path
                # Validate path stays within user's storage directory
                pdf_path = validate_user_file_path(pdf_path, FilePath(pdf_storage), user["sub"])
                pdf_available = pdf_path.exists()
                pdf_size = pdf_path.stat().st_size if pdf_available else None

            # Check PDFX JSON file with path validation (T032)
            # Note: pdfx_json_path is stored relative to pdf_storage: {user_id}/pdfx_json/{doc_id}.json
            pdfx_json_available = False
            pdfx_json_size = None
            if doc.pdfx_json_path:
                # Construct full path from pdf_storage (paths are relative to pdf_storage root)
                pdfx_path = FilePath(pdf_storage) / doc.pdfx_json_path
                # Validate path stays within user's storage directory (pdf_storage/{user_id})
                pdfx_path = validate_user_file_path(pdfx_path, FilePath(pdf_storage), user["sub"])
                pdfx_json_available = pdfx_path.exists()
                pdfx_json_size = pdfx_path.stat().st_size if pdfx_json_available else None

            # Check processed JSON file with path validation (T032)
            # Note: processed_json_path is stored relative to pdf_storage: {user_id}/processed_json/{doc_id}.json
            processed_json_available = False
            processed_json_size = None
            if doc.processed_json_path:
                # Construct full path from pdf_storage (paths are relative to pdf_storage root)
                processed_path = FilePath(pdf_storage) / doc.processed_json_path
                # Validate path stays within user's storage directory (pdf_storage/{user_id})
                processed_path = validate_user_file_path(processed_path, FilePath(pdf_storage), user["sub"])
                processed_json_available = processed_path.exists()
                processed_json_size = processed_path.stat().st_size if processed_json_available else None

            return {
                "pdf_available": pdf_available,
                "pdf_size": pdf_size,
                "pdfx_json_available": pdfx_json_available,
                "pdfx_json_size": pdfx_json_size,
                "processed_json_available": processed_json_available,
                "processed_json_size": processed_json_size,
                "filename": doc.filename
            }
        finally:
            session.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Error getting download info for document %s: %s', document_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get download info: {str(e)}"
        )


@router.get("/documents/{document_id}/download/{file_type}")
async def download_document_file(
    document_id: str = Path(..., description="Document ID"),
    file_type: str = Path(..., description="File type to download (pdf, pdfx_json, processed_json)"),
    user: Dict[str, Any] = get_auth_dependency()
):
    """
    Download a specific file associated with a document.

    Available file types:
    - pdf: Original PDF document
    - pdfx_json: Raw PDFX extraction output
    - processed_json: Cleaned document ready for embedding

    Requirements:
        - FR-014: Verify document ownership before serving files
        - Return 403 Forbidden for cross-user access attempts
    """
    try:
        from ..config import get_pdf_storage_path
        document_uuid = _parse_document_uuid(document_id)

        if file_type not in ['pdf', 'pdfx_json', 'processed_json']:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type: {file_type}"
            )

        # Get document from database
        session = SessionLocal()
        try:
            # T031: Get database user for ownership verification (FR-014)
            db_user = provision_user(session, principal_from_claims(user))

            doc = session.query(ViewerPDFDocument).filter(
                ViewerPDFDocument.id == document_uuid
            ).first()

            if not doc:
                raise HTTPException(
                    status_code=404,
                    detail=f"Document with ID {document_id} not found"
                )

            # T031: Ownership check (FR-014) - Return 403 for cross-user access
            if doc.user_id != db_user.id:
                raise HTTPException(
                    status_code=403,
                    detail="You do not have permission to access this document"
                )

            # Determine file path based on type with path validation (T032)
            file_path = None
            filename = None
            media_type = "application/octet-stream"

            if file_type == 'pdf':
                if doc.file_path:
                    pdf_storage = get_pdf_storage_path()
                    file_path = FilePath(pdf_storage) / doc.file_path
                    # Validate path stays within user's storage directory
                    file_path = validate_user_file_path(file_path, FilePath(pdf_storage), user["sub"])
                    filename = doc.filename
                    media_type = "application/pdf"

            elif file_type == 'pdfx_json':
                # Note: pdfx_json_path is stored relative to pdf_storage: {user_id}/pdfx_json/{doc_id}.json
                if doc.pdfx_json_path:
                    pdf_storage = get_pdf_storage_path()
                    # Construct full path from pdf_storage (paths are relative to pdf_storage root)
                    file_path = FilePath(pdf_storage) / doc.pdfx_json_path
                    # Validate path stays within user's storage directory (pdf_storage/{user_id})
                    file_path = validate_user_file_path(file_path, FilePath(pdf_storage), user["sub"])
                    filename = f"{doc.filename.rsplit('.', 1)[0]}_pdfx.json"
                    media_type = "application/json"

            elif file_type == 'processed_json':
                # Note: processed_json_path is stored relative to pdf_storage: {user_id}/processed_json/{doc_id}.json
                if doc.processed_json_path:
                    pdf_storage = get_pdf_storage_path()
                    # Construct full path from pdf_storage (paths are relative to pdf_storage root)
                    file_path = FilePath(pdf_storage) / doc.processed_json_path
                    # Validate path stays within user's storage directory (pdf_storage/{user_id})
                    file_path = validate_user_file_path(file_path, FilePath(pdf_storage), user["sub"])
                    filename = f"{doc.filename.rsplit('.', 1)[0]}_processed.json"
                    media_type = "application/json"

            if not file_path or not file_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"{file_type} file not available for document {document_id}"
                )

            return FileResponse(
                path=file_path,
                filename=filename,
                media_type=media_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"'
                }
            )

        finally:
            session.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Error downloading %s for document %s: %s', file_type, document_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to download file: {str(e)}"
        )
