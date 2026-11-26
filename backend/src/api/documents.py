"""Documents API endpoints for Weaviate Control Panel.

Task: T026 - Add Security() dependency injection to document endpoints
All endpoints now require valid AWS Cognito JWT token via Security(auth.get_user).
"""

# FastAPI parameter factories and boundary-level cleanup catches are intentional.
# ruff: noqa: B008, BLE001

import asyncio
import base64
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import Any

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Path,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_pdf_storage_path
from ..lib.document_cleanup import (
    cleanup_document_curation_dependencies,
    delete_drain_context,
    drain_shielded,
    is_cancelled_delete_drain,
    run_thread_drained,
)
from ..lib.document_sources.access import build_document_source_request_context
from ..lib.packages.document_source_provider_models import DevelopmentReaderUnavailable
from ..lib.document_sources.identifier_import import (
    IdentifierImportService,
    IdentifierImportValidationError,
    SupplementSelectionError,
)
from ..lib.document_sources.provenance import build_document_source_provenance
from ..lib.document_sources.registry import get_document_source_provider_metadata
from ..lib.http_errors import log_exception, raise_sanitized_http_exception
from ..lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
from ..lib.observability.sentry import hash_sentry_identifier
from ..lib.pdf_jobs import service as pdf_job_service
from ..lib.pdf_jobs.upload_execution_service import (
    UploadExecutionService,
)
from ..lib.pdf_jobs.upload_intake_service import (
    UploadIntakeDuplicateError,
    UploadIntakeProviderDecisionError,
    UploadIntakeService,
    UploadIntakeValidationError,
    external_document_source_import_enabled,
)
from ..lib.openai_agents.config import get_document_source_provider, get_pdf_extraction_queue_timeout_seconds
from ..lib.pipeline.tracker import PipelineTracker
from ..lib.weaviate_client.documents import (
    async_list_documents as list_documents,
)
from ..lib.weaviate_client.documents import (
    PAPER_DELETE_FAILURE_DETAIL,
    delete_paper_chunks,
    delete_paper_object,
    get_document,
    get_paper_status_for_delete,
    update_document_filename,
)
from ..lib.weaviate_helpers import get_tenant_name
from ..models.api_schemas import (
    DocumentFilter,
    DocumentListResponse,
    DocumentResponse,
    DocumentSourceIdentifierImportRequest,
    DocumentSourceIdentifierResolveRequest,
    DocumentSourceIdentifierImportResponse,
    DocumentSourceProviderConfiguration,
    DocumentSourceProvenance,
    OperationResult,
    SortBy,
    SortOrder,
)
from ..models.document import EmbeddingStatus, ProcessingStatus
from ..models.sql.database import SessionLocal
from ..lib.document_files.service import remove_stored_files
from ..models.sql.pdf_document import (
    DISPLAY_MODE_TEXT,
    FILE_ROLE_SUPPLEMENT,
    PDFDocument as ViewerPDFDocument,
    PDFDocumentFile,
)
from ..models.sql.pdf_processing_job import PdfJobStatus
from ..schemas.document_files import AddFilesRequest
from ..schemas.documents import DocumentUpdateRequest, DocumentUpdateResponse
from ..services.document_access import require_owned_document
from ..services.processing_status_policy import (
    ACTIVE_PDF_JOB_STATUSES as _ACTIVE_PDF_JOB_STATUSES,
)
from ..services.processing_status_policy import (
    ACTIVE_PROCESSING_STATUSES as _ACTIVE_PROCESSING_STATUSES,
)
from ..services.processing_status_policy import (
    PDF_JOB_STATUS_TO_PROCESSING_STATUS as _PDF_JOB_STATUS_TO_PROCESSING_STATUS,
)
from ..services.processing_status_policy import (
    PIPELINE_STAGE_TO_PROCESSING_STATUS as _PIPELINE_STAGE_TO_PROCESSING_STATUS,
)
from ..services.processing_status_policy import (
    TERMINAL_PDF_JOB_STATUSES as _TERMINAL_PDF_JOB_STATUSES,
)
from ..services.processing_status_policy import (
    is_pipeline_status_active as _is_pipeline_status_active,
)
from ..services.processing_status_policy import (
    is_pipeline_status_terminal as _is_pipeline_status_terminal,
)
from ..services.processing_status_policy import (
    normalize_processing_status as _normalize_processing_status,
)
from ..services.processing_status_policy import (
    pipeline_stage_value as _pipeline_stage_value,
)
from ..services.processing_status_policy import (
    processing_status_for_pipeline_stage,
)
from ..services.user_service import principal_from_claims, provision_user, provision_user_async
from .auth import get_auth_dependency

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/weaviate")
pipeline_tracker = PipelineTracker()
upload_execution_service = UploadExecutionService(pipeline_tracker=pipeline_tracker)
upload_intake_service = UploadIntakeService(upload_execution_service=upload_execution_service)
identifier_import_service = IdentifierImportService(upload_execution_service=upload_execution_service)

_pdf_extraction_service_token: str | None = None
_pdf_extraction_service_token_expires_at: float = 0.0


class _DocumentMetadataUpdateError(RuntimeError):
    """Sanitized metadata failure safe for logs and Sentry."""


async def _document_source_context_or_503(
    *,
    request: Request,
    user: dict[str, Any],
):
    """Resolve provider authorization before any document intake side effects."""

    try:
        return await build_document_source_request_context(
            request=request,
            user_claims=user,
        )
    except DevelopmentReaderUnavailable as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=503,
            detail={
                "error": "document_source_curator_token_unavailable",
                "message": "Document-source authentication is unavailable.",
                "suggestion": "Try again later or contact support if this persists.",
            },
            log_message="Development document-source reader unavailable",
            exc=exc,
            level=logging.WARNING,
        )


def _sanitized_document_metadata_error(exc: BaseException) -> _DocumentMetadataUpdateError:
    """Build a safe exception without retaining storage paths or SQL parameters."""
    try:
        raise _DocumentMetadataUpdateError(
            f"Document metadata update failed ({type(exc).__name__})"
        ) from None
    except _DocumentMetadataUpdateError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized


def _effective_processing_status(raw_document_status: Any, pipeline_status: Any) -> str:
    """Compute effective status with pipeline tracker precedence when present."""
    effective = _normalize_processing_status(raw_document_status)
    if not pipeline_status:
        return effective

    stage_value = _pipeline_stage_value(pipeline_status)
    return _PIPELINE_STAGE_TO_PROCESSING_STATUS.get(stage_value, effective)


def _extract_document_processing_status(doc_payload: dict[str, Any]) -> str:
    """Extract and normalize processing status from mixed payload key styles."""
    raw_status = doc_payload.get("processing_status", doc_payload.get("processingStatus"))
    return _normalize_processing_status(raw_status)


def _as_utc_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.astimezone(timezone.utc)


def _is_stale_pipeline_tracker(
    *,
    pipeline_status: Any,
    latest_job: Any,
) -> bool:
    """Return whether an active in-memory tracker predates a terminal job."""
    if (
        not pipeline_status
        or not latest_job
        or latest_job.status not in _TERMINAL_PDF_JOB_STATUSES
        or not _is_pipeline_status_active(pipeline_status)
    ):
        return False

    pipeline_updated_at = _as_utc_datetime(
        getattr(pipeline_status, "updated_at", None)
    )
    job_terminal_at = _as_utc_datetime(
        getattr(latest_job, "completed_at", None)
        or getattr(latest_job, "updated_at", None)
    )
    return bool(
        pipeline_updated_at
        and job_terminal_at
        and pipeline_updated_at <= job_terminal_at
    )


def _pipeline_status_payload_with_job_precedence(*, pipeline_status: Any, job: Any) -> dict[str, Any] | None:
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
    sql_status = _normalize_processing_status(raw_sql_status)
    indexed_status = _normalize_processing_status(weaviate_processing_status)
    fallback_status = sql_status if raw_sql_status else indexed_status

    if job and job.status in _ACTIVE_PDF_JOB_STATUSES:
        return _PDF_JOB_STATUS_TO_PROCESSING_STATUS.get(job.status, fallback_status)
    if job and job.status in {
        PdfJobStatus.FAILED.value,
        PdfJobStatus.CANCELLED.value,
    }:
        return _PDF_JOB_STATUS_TO_PROCESSING_STATUS.get(job.status, fallback_status)
    if pipeline_status and _is_pipeline_status_active(pipeline_status):
        return _effective_processing_status(fallback_status, pipeline_status)
    active_statuses = {
        ProcessingStatus.PROCESSING.value,
        ProcessingStatus.PARSING.value,
        ProcessingStatus.CHUNKING.value,
        ProcessingStatus.EMBEDDING.value,
        ProcessingStatus.STORING.value,
    }
    # A re-embed can update the indexed state before the SQL worker reaches
    # its first stage. Do not let an older completed job mask that current work.
    if indexed_status in active_statuses:
        return indexed_status
    if sql_status in active_statuses:
        return sql_status
    if job:
        return _PDF_JOB_STATUS_TO_PROCESSING_STATUS.get(job.status, fallback_status)
    if pipeline_status:
        return _effective_processing_status(fallback_status, pipeline_status)
    return fallback_status


def _status_snapshot_from_pipeline(pipeline_status: Any) -> dict[str, Any]:
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


def _status_snapshot_from_job(job: Any) -> dict[str, Any]:
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
        "metadata": getattr(job, "metadata", None),
    }


def _select_progress_snapshot(*, pipeline_status: Any, job: Any) -> dict[str, Any] | None:
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


def verify_document_ownership(
    db: Session,
    document_id: str,
    auth_user: dict[str, Any],
    *,
    for_update: bool = False,
    provisioned_user_id: int | None = None,
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
    owner_user_id = (
        provision_user(db, principal_from_claims(auth_user)).id
        if provisioned_user_id is None else provisioned_user_id
    )

    document_uuid = _parse_document_uuid(document_id)
    return require_owned_document(
        db,
        document_uuid,
        owner_user_id,
        for_update=for_update,
    )


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
        # One rule for every stored path (document_files/storage_paths.py).
        from src.lib.document_files.storage_paths import StoredPathOutsideOwnerStorage, owner_storage_path

        try:
            resolved_path = owner_storage_path(file_path, storage_root, user_id)
        except StoredPathOutsideOwnerStorage as exc:
            logger.warning(
                'Path traversal attempt detected: %s resolves outside user storage for %s', file_path, user_id)
            raise HTTPException(
                status_code=403,
                detail="Access denied: file path validation failed"
            ) from exc

        return resolved_path

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Error validating file path %s: %s', file_path, e)
        raise HTTPException(
            status_code=500,
            detail="File path validation error"
        ) from e


def _rename_source_pdf(
    *,
    document: ViewerPDFDocument,
    filename: str,
    user_sub: str,
) -> tuple[FilePath, FilePath, str]:
    """Hardlink an owned source PDF while retaining the recoverable old path."""
    storage_root = FilePath(get_pdf_storage_path())
    source_path = validate_user_file_path(
        storage_root / document.main_file.file_path,
        storage_root,
        user_sub,
    )
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="Source PDF file not found")

    destination_path = validate_user_file_path(
        source_path.with_name(filename),
        storage_root,
        user_sub,
    )
    try:
        destination_path.hardlink_to(source_path)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail="A document file with that filename already exists",
        ) from exc

    relative_destination = destination_path.relative_to(storage_root.resolve()).as_posix()
    return source_path, destination_path, relative_destination


async def _recover_renamed_document(
    *,
    session: Session,
    document: ViewerPDFDocument,
    document_id: str,
    user_sub: str,
    old_filename: str,
    old_file_path: str,
    new_filename: str,
    new_file_path: str,
    source_path: FilePath,
    destination_path: FilePath,
    index_updated: bool,
) -> _DocumentMetadataUpdateError | None:
    """Recover to one usable metadata state after a rename transaction fails.

    The old hardlink remains present until the metadata commit succeeds. If the
    index cannot be restored after a database failure, complete the database
    change forward to the new hardlink instead of leaving the index and source
    metadata split.
    """
    restored_old_index = not index_updated
    if index_updated:
        try:
            await update_document_filename(user_sub, document_id, old_filename)
            restored_old_index = True
        except Exception as exc:
            report_runtime_exception(
                _sanitized_document_metadata_error(exc),
                component="documents",
                operation="filename_index_rollback_failed",
                context={"document_id": document_id},
            )

    main_file = document.main_file
    if restored_old_index:
        document.filename = old_filename
        main_file.display_name = old_filename
        main_file.file_path = old_file_path
        try:
            destination_path.unlink(missing_ok=True)
        except Exception as exc:
            # The old path and all authoritative metadata remain usable. The
            # extra hardlink is reported for cleanup but cannot orphan the PDF.
            report_runtime_exception(
                _sanitized_document_metadata_error(exc),
                component="documents",
                operation="filename_destination_cleanup_failed",
                context={"document_id": document_id},
            )
        return

    document.filename = new_filename
    main_file.display_name = new_filename
    main_file.file_path = new_file_path
    forward_recovery_failure: _DocumentMetadataUpdateError | None = None
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        forward_recovery_failure = _sanitized_document_metadata_error(exc)
        report_runtime_exception(
            forward_recovery_failure,
            component="documents",
            operation="filename_forward_recovery_failed",
            context={"document_id": document_id},
        )
    if forward_recovery_failure is not None:
        return forward_recovery_failure

    try:
        source_path.unlink(missing_ok=True)
    except Exception as exc:
        # Both paths address the same inode; current metadata points to the new
        # usable path, so failure to remove the old hardlink is non-destructive.
        report_runtime_exception(
            _sanitized_document_metadata_error(exc),
            component="documents",
            operation="filename_source_cleanup_failed",
            context={"document_id": document_id},
        )


async def _update_owned_document_metadata(
    *,
    session: Session,
    document: ViewerPDFDocument,
    request: DocumentUpdateRequest,
    document_id: str,
    user_sub: str,
) -> DocumentUpdateResponse:
    """Coordinate indexed, filesystem, and PostgreSQL metadata updates."""
    moved_paths: tuple[FilePath, FilePath] | None = None
    old_filename: str | None = None
    old_file_path: str | None = None
    new_file_path: str | None = None
    index_updated = False
    sanitized_failure: _DocumentMetadataUpdateError | None = None
    try:
        metadata_changed = False
        if request.filename is not None and request.filename != document.filename:
            latest_job = pdf_job_service.get_latest_paper_job_for_document(
                document_id=document_id,
                user_id=document.user_id,
                reconcile_stale=True,
            )
            pipeline_status = await pipeline_tracker.get_pipeline_status(document_id)
            indexed_document = await get_document(user_sub, document_id)
            indexed_status = (
                indexed_document.get("document", {}).get("processing_status")
                if indexed_document
                else None
            )
            processing_status = _canonical_processing_status(
                sql_processing_status=document.status,
                weaviate_processing_status=indexed_status,
                pipeline_status=pipeline_status,
                job=latest_job,
            )
            if processing_status in _ACTIVE_PROCESSING_STATUSES:
                raise HTTPException(
                    status_code=409,
                    detail="Document filename cannot be changed while processing is active",
                )

            main_file = document.main_file_or_none
            if main_file is None:
                # A paper without a main file has no stored main PDF to rename;
                # only its Library name and the search index change.
                old_filename = document.filename
                await update_document_filename(user_sub, document_id, request.filename)
                index_updated = True
                document.filename = request.filename
                if request.title is not None:
                    document.title = request.title
                session.commit()
                logger.info("Updated metadata for document %s", document_id)
                return DocumentUpdateResponse(
                    document_id=document_id,
                    title=document.title,
                    filename=document.filename,
                )
            target_relative_path = FilePath(main_file.file_path).with_name(
                request.filename
            ).as_posix()
            path_owner = session.execute(
                select(PDFDocumentFile.id).where(
                    PDFDocumentFile.file_path == target_relative_path,
                    PDFDocumentFile.id != main_file.id,
                )
            ).scalar_one_or_none()
            if path_owner is not None:
                raise HTTPException(
                    status_code=409,
                    detail="A document file with that filename already exists",
                )

            old_filename = document.filename
            old_file_path = main_file.file_path
            source_path, destination_path, relative_destination = _rename_source_pdf(
                document=document,
                filename=request.filename,
                user_sub=user_sub,
            )
            moved_paths = (source_path, destination_path)
            new_file_path = relative_destination
            await update_document_filename(user_sub, document_id, request.filename)
            index_updated = True
            document.filename = request.filename
            main_file.display_name = request.filename
            main_file.file_path = relative_destination
            metadata_changed = True

        if request.title is not None:
            document.title = request.title
            metadata_changed = True

        if metadata_changed:
            session.commit()
            if moved_paths is not None:
                try:
                    moved_paths[0].unlink(missing_ok=True)
                except Exception as exc:
                    report_runtime_exception(
                        _sanitized_document_metadata_error(exc),
                        component="documents",
                        operation="filename_source_cleanup_failed",
                        context={"document_id": document_id},
                    )
            logger.info("Updated metadata for document %s", document_id)

        return DocumentUpdateResponse(
            document_id=document_id,
            title=document.title,
            filename=document.filename,
        )
    except Exception as exc:
        session.rollback()
        if (
            moved_paths is not None
            and old_filename is not None
            and old_file_path is not None
            and request.filename is not None
            and new_file_path is not None
        ):
            recovery_failure = await _recover_renamed_document(
                session=session,
                document=document,
                document_id=document_id,
                user_sub=user_sub,
                old_filename=old_filename,
                old_file_path=old_file_path,
                new_filename=request.filename,
                new_file_path=new_file_path,
                source_path=moved_paths[0],
                destination_path=moved_paths[1],
                index_updated=index_updated,
            )
            if recovery_failure is not None:
                sanitized_failure = recovery_failure
        elif moved_paths is None and index_updated and old_filename is not None:
            # A paper without a main file: put the index's name back.
            try:
                await update_document_filename(user_sub, document_id, old_filename)
            except Exception as rollback_exc:
                report_runtime_exception(
                    _sanitized_document_metadata_error(rollback_exc),
                    component="documents",
                    operation="filename_index_rollback_failed",
                    context={"document_id": document_id},
                )
        if sanitized_failure is None:
            if isinstance(exc, HTTPException):
                raise
            if isinstance(exc, _DocumentMetadataUpdateError):
                raise
            sanitized_failure = _sanitized_document_metadata_error(exc)

    if sanitized_failure is None:  # pragma: no cover - defensive invariant
        raise _DocumentMetadataUpdateError("Document metadata update failed")
    raise sanitized_failure


def _document_cleanup_snapshot(document: ViewerPDFDocument) -> dict[str, Any]:
    """Stored artifact paths of every file of a paper, taken before its rows are deleted."""
    return {
        "id": document.id,
        "files": [
            {
                "role": file_row.role,
                "display_mode": file_row.display_mode,
                "file_path": file_row.file_path,
                "pdfx_json_path": file_row.pdfx_json_path,
                "processed_json_path": file_row.processed_json_path,
                "source_markdown_path": file_row.source_markdown_path,
            }
            for file_row in document.files
        ],
    }


def _removed_paths(file_values: dict[str, Any]) -> tuple[str, ...]:
    """The stored paths a paper delete removes for one file row.

    A text file's ``file_path`` is a provider placeholder, never removed.
    """

    keys = ("pdfx_json_path", "processed_json_path", "source_markdown_path") + (
        () if file_values["display_mode"] == DISPLAY_MODE_TEXT else ("file_path",)
    )
    return tuple(file_values[key] for key in keys if file_values[key])


_PAPER_DELETE_STORED_FILE_REFUSAL_DETAIL = (
    "This paper could not be deleted: one of its stored files needs an administrator. Nothing was changed."
)


def _require_removable_paths(cleanup_values: dict[str, Any], base_storage: FilePath, user_sub: str) -> None:
    """Refuse (403) when owner storage or any path a paper delete would remove lies outside the curator's storage."""
    from ..lib.document_files.storage_paths import StoredPathOutsideOwnerStorage, owner_storage_path

    try:
        owner_storage_path(base_storage / user_sub, base_storage, user_sub)
    except StoredPathOutsideOwnerStorage as exc:
        logger.warning("Owner storage validation failed for %s", user_sub)
        raise HTTPException(status_code=403, detail="Access denied: file path validation failed") from exc

    for file_values in cleanup_values["files"]:
        for relative in _removed_paths(file_values):
            path = _removable_path(base_storage, relative, user_sub)
            # A stored path names a file; a real folder there only comes from a corrupted
            # row, and the delete must not start (it would fail half-way).
            if path.is_dir() and not path.is_symlink():
                logger.warning("A stored path names a folder, not a file: %s for %s", relative, user_sub)
                raise HTTPException(status_code=403, detail=_PAPER_DELETE_STORED_FILE_REFUSAL_DETAIL)


def _removable_path(base_storage: FilePath, relative: str, user_sub: str) -> FilePath:
    """The path a delete may remove for one stored path (else 403).

    The one rule (``storage_paths.owner_storage_entry``): the path's folder, resolved,
    lies inside the curator's storage, and the path's own name is not followed, so a
    link is removed as a link and its target is never touched.
    """

    from src.lib.document_files.storage_paths import StoredPathOutsideOwnerStorage, owner_storage_entry

    try:
        return owner_storage_entry(base_storage / relative, base_storage, user_sub)
    except StoredPathOutsideOwnerStorage as exc:
        logger.warning("Path traversal attempt detected: %s sits outside user storage for %s", relative, user_sub)
        raise HTTPException(status_code=403, detail=_PAPER_DELETE_STORED_FILE_REFUSAL_DETAIL) from exc


def _delete_user_document_directory_or_file_if_present(
    *,
    base_storage: FilePath,
    relative_path: str | None,
    user_sub: str,
    document_id: str,
) -> None:
    """Delete a main file's stored PDF: its paper folder only when the path proves it.

    The one rule for what a paper delete may remove (ALL-1460, P7c2): the folder
    ``{user}/{document_id}/`` is removed only when the stored path, resolved inside
    the user's storage, lies under exactly that folder and the folder is not a link.
    Any other layout (a file directly under the user's folder, a file in a shared
    subfolder) loses only the file itself; a link loses only the link, never its
    target. A path outside the user's storage is refused (403) before the delete
    starts (``_require_removable_paths``).
    """
    if not relative_path:
        return
    from ..lib.document_files.storage_paths import owner_storage_path

    file_path = _removable_path(base_storage, relative_path, user_sub)
    user_storage_root = owner_storage_path(base_storage / user_sub, base_storage, user_sub)
    paper_dir = user_storage_root / document_id
    owns_folder = (
        file_path.relative_to(user_storage_root).parts[:1] == (document_id,)
        and len(file_path.relative_to(user_storage_root).parts) >= 2
        and not paper_dir.is_symlink()
        and paper_dir.resolve() == paper_dir
    )
    if owns_folder and paper_dir.is_dir():
        shutil.rmtree(paper_dir)
        logger.info("Deleted filesystem artifacts for document %s", document_id)
        return
    if file_path.is_symlink() or file_path.exists():
        file_path.unlink()
        logger.info("Deleted PDF file for document %s", document_id)


def _remove_empty_paper_folders(base_storage: FilePath, user_sub: str, document_id: str) -> None:
    """Remove ``{user}/{paper}/supplement_pdf/`` and then ``{user}/{paper}/`` when they are empty.

    Only empty folders, with ``rmdir``: never a folder that still holds anything, and never
    through a symlinked owner or paper directory.
    """
    from ..lib.document_files.storage_paths import owner_storage_path

    user_storage_root = owner_storage_path(base_storage / user_sub, base_storage, user_sub)
    paper_dir = user_storage_root / document_id
    if paper_dir.is_symlink() or paper_dir.resolve() != paper_dir:
        return
    for folder in (paper_dir / "supplement_pdf", paper_dir):
        if folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
            folder.rmdir()


def _pipeline_payload_from_job(job: Any) -> dict[str, Any] | None:
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
            "metadata": getattr(job, "metadata", None),
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
        "metadata": payload.get("metadata"),
        "error_count": 1 if payload.get("status") == PdfJobStatus.FAILED.value else 0,
        "stage_results": [],
    }


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents_endpoint(
    user: dict[str, Any] = get_auth_dependency(),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    search: str | None = Query(None, description="Search term for filename"),
    embedding_status: list[EmbeddingStatus] | None = Query(None, description="Filter by embedding status"),
    sort_by: SortBy = Query(SortBy.CREATION_DATE, description="Sort field"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort direction"),
    date_from: datetime | None = Query(None, description="Filter by creation date start"),
    date_to: datetime | None = Query(None, description="Filter by creation date end"),
    min_vector_count: int | None = Query(None, ge=0, description="Minimum vector count"),
    max_vector_count: int | None = Query(None, ge=0, description="Maximum vector count")
):
    """
    List all PDF documents stored in Weaviate with pagination and filtering.

    Returns paginated list of documents with their metadata and processing status.

    Listing is read-only; missing vector records do not authorize cleanup.
    """
    try:
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

    except HTTPException:
        raise
    except Exception as e:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to retrieve documents",
            log_message="Error listing documents",
            exc=e,
        )


async def _build_pdf_extraction_service_headers() -> dict[str, str]:
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

    auth_basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
    headers = {
        "Authorization": f"Basic {auth_basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = {"grant_type": "client_credentials", "scope": scope}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(token_url, data=body, headers=headers)
    except httpx.RequestError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=502,
            detail="Failed to fetch PDF extraction service token",
            log_message="Failed to fetch PDF extraction service token",
            exc=exc,
        )

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
        raise_sanitized_http_exception(
            logger,
            status_code=503,
            detail="Unable to reach PDF extraction worker status endpoint",
            log_message="Unable to reach PDF extraction worker status endpoint",
            exc=exc,
            level=logging.WARNING,
        )

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


@router.get(
    "/documents/source-provider",
    response_model=DocumentSourceProviderConfiguration,
)
async def get_document_source_provider_configuration(
    user: dict[str, Any] = get_auth_dependency(),
):
    """Expose configured non-secret identifier guidance through the provider registry."""

    del user
    if not external_document_source_import_enabled():
        return DocumentSourceProviderConfiguration(
            provider=None,
            import_enabled=False,
            presentation=None,
        )

    provider = get_document_source_provider().strip().lower()
    return DocumentSourceProviderConfiguration(
        provider=provider,
        import_enabled=True,
        presentation=get_document_source_provider_metadata(provider),
    )


@router.get("/documents/pdf-extraction-health")
async def get_pdf_extraction_health(user: dict[str, Any] = get_auth_dependency()):
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
    service_headers: dict[str, str] = {}
    auth_header_error: str | None = None

    try:
        service_headers = await _build_pdf_extraction_service_headers()
    except Exception as exc:
        auth_header_error = "PDF extraction service authentication is unavailable"
        log_exception(
            logger,
            message="Failed to build PDF extraction service auth headers for health check",
            exc=exc,
            level=logging.WARNING,
        )

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
                    try:
                        status_payload = status_resp.json()
                    except ValueError as exc:
                        log_exception(
                            logger,
                            message="PDF extraction status endpoint returned invalid JSON",
                            exc=exc,
                            level=logging.WARNING,
                        )
                        status_error = "Status endpoint returned invalid JSON"
                    else:
                        if isinstance(status_payload, dict):
                            worker_state = str(status_payload.get("state", "")).strip().lower() or "unknown"
            except httpx.RequestError as exc:
                log_exception(
                    logger,
                    message="PDF extraction status endpoint request failed",
                    exc=exc,
                    level=logging.WARNING,
                )
                status_error = "Status endpoint request failed"
            except Exception as exc:
                log_exception(
                    logger,
                    message="PDF extraction status endpoint check failed",
                    exc=exc,
                    level=logging.WARNING,
                )
                status_error = "Status endpoint check failed"
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
        log_exception(
            logger,
            message="PDF extraction health check failed",
            exc=exc,
            level=logging.WARNING,
        )
        return {
            "status": "unreachable",
            "service_url": service_url,
            "last_checked": checked_at,
            "error": "Unable to reach PDF extraction service",
            "worker_state": "unknown",
            "worker_available": False,
            "wake_required": False,
        }


@router.post("/documents/pdf-extraction-wake")
async def wake_pdf_extraction_worker(user: dict[str, Any] = get_auth_dependency()):
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
        raise_sanitized_http_exception(
            logger,
            status_code=502,
            detail="Failed to wake PDF extraction worker",
            log_message="Failed to wake PDF extraction worker",
            exc=exc,
        )

    if wake_response.status_code >= 400:
        logger.warning(
            "Wake request failed with status %s and payload %r",
            wake_response.status_code,
            wake_payload,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Wake request failed ({wake_response.status_code})",
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
    user: dict[str, Any] = get_auth_dependency()
):
    """
    Get detailed information about a specific document.

    Returns Document schema with ownership metadata per contract.

    Requirements:
        - FR-014: Verify document ownership, return 403 for cross-user access
        - T031: Return the flat DocumentResponse canonicalized by ALL-789
    """
    from ..lib.document_files.service import ready_file_count

    # T031: Verify ownership from PostgreSQL FIRST (FR-014)
    # Returns 404 if not found, 403 if not owned by user
    session = SessionLocal()
    try:
        db_user = await provision_user_async(principal_from_claims(user))
        pg_doc = verify_document_ownership(session, document_id, user, provisioned_user_id=db_user.id)
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
        latest_job = pdf_job_service.get_latest_paper_job_for_document(
            document_id=document_id,
            user_id=db_user.id,
            reconcile_stale=True,
        )

        source_provenance = build_document_source_provenance(pg_doc)

        # T031: Return contract Document schema (document_endpoints.yaml)
        return DocumentResponse(
            document_id=document_id,
            job_id=latest_job.job_id if latest_job else None,
            user_id=db_user.id,
            filename=pg_doc.filename,
            title=getattr(pg_doc, "title", None),
            status=_canonical_processing_status(
                sql_processing_status=getattr(pg_doc, "status", None),
                weaviate_processing_status=doc_data.get(
                    "processing_status",
                    doc_data.get("processingStatus"),
                ),
                pipeline_status=None,
                job=latest_job,
            ).upper(),
            upload_timestamp=pg_doc.upload_timestamp,
            processing_started_at=pg_doc.processing_started_at,
            processing_completed_at=pg_doc.processing_completed_at,
            file_size_bytes=(
                pg_doc.main_file_or_none.file_size if pg_doc.main_file_or_none is not None else None
            ),
            weaviate_tenant=tenant_name,
            chunk_count=doc_data.get("chunk_count"),
            ready_file_count=ready_file_count(pg_doc),
            error_message=(
                latest_job.error_message
                if latest_job and latest_job.error_message
                else getattr(pg_doc, "error_message", None)
            ),
            source_provenance=(
                DocumentSourceProvenance(**source_provenance)
                if source_provenance
                else None
            ),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to retrieve document",
            log_message=f"Error retrieving document {document_id}",
            exc=e,
        )


@router.patch("/documents/{document_id}", response_model=DocumentUpdateResponse)
async def update_document_endpoint(
    request: DocumentUpdateRequest,
    document_id: str = Path(..., description="Document ID"),
    user: dict[str, Any] = get_auth_dependency(),
):
    """
    Update a document's display title and source PDF filename independently.

    Requirements:
        - FR-014: Verify document ownership, return 403 for cross-user access
    """
    # Verify ownership from PostgreSQL FIRST (FR-014)
    session = SessionLocal()
    try:
        db_user = await provision_user_async(principal_from_claims(user))
        pg_doc = verify_document_ownership(
            session,
            document_id,
            user,
            for_update=True,
            provisioned_user_id=db_user.id,
        )
        return await _update_owned_document_metadata(
            session=session,
            document=pg_doc,
            request=request,
            document_id=document_id,
            user_sub=user["sub"],
        )
    except HTTPException:
        raise
    except Exception as e:
        if not isinstance(e, _DocumentMetadataUpdateError):
            session.rollback()
        sanitized_error = (
            e if isinstance(e, _DocumentMetadataUpdateError)
            else _sanitized_document_metadata_error(e)
        )
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to update document",
            log_message=f"Error updating document {document_id}",
            exc=sanitized_error,
        )
    finally:
        session.close()


@router.delete("/documents/{document_id}", response_model=OperationResult)
async def delete_document_endpoint(
    document_id: str = Path(..., description="Document ID"),
    user: dict[str, Any] = get_auth_dependency()
):
    """
    Delete a document and all its associated chunks from Weaviate.

    This operation is cascading and will remove all chunks associated with the document.
    Documents currently being processed cannot be deleted.

    Requirements:
        - FR-014: Verify document ownership, return 403 for cross-user access
    """
    # One lock per paper orders this delete against an add to it: no add can create its job
    # between the active-job check below and the row delete (see document_files/paper_lock.py).
    # Nobody waits for it: when another delete or add holds it, this answers 409 at once.
    # Every database session of this delete is opened in a worker thread, so waiting for a
    # pooled connection never blocks the event loop; a pool timeout is a 503.
    from sqlalchemy.exc import TimeoutError as PoolTimeoutError

    from ..lib.document_files.paper_lock import (
        PaperOperationInProgress,
        open_paper_lock_session,
        paper_operation_lock,
    )

    canonical_document_id = document_id

    def report_delete_failure(operation: str, message: str, exc: Exception, *, step: str | None = None) -> None:
        exc_type = type(exc).__name__
        document_id_hash = hash_sentry_identifier(canonical_document_id)
        context = {"document_id_hash": document_id_hash, "exception_type": exc_type}
        fingerprint = [operation]
        if step is not None:
            context["step"] = step
            fingerprint.append(step)
        logger.warning(
            "%s step=%s document_id_hash=%s exception_type=%s",
            message, step, document_id_hash, exc_type, extra={"sentry_skip_event": True},
        )
        try:
            report_runtime_exception(
                sanitized_runtime_error(message),
                component="documents",
                operation=operation,
                context=context,
                fingerprint=[*fingerprint, exc_type],
                include_request=False,
            )
        except Exception:
            logger.warning("Failed to report paper delete failure", extra={"sentry_skip_event": True})

    try:
        # T031: Verify ownership from PostgreSQL FIRST (FR-014)
        # Returns 404 if not found, 403 if not owned by user
        db_user = await provision_user_async(principal_from_claims(user))

        def owned_paper():
            session = SessionLocal()
            try:
                return verify_document_ownership(session, document_id, user, provisioned_user_id=db_user.id)
            finally:
                session.close()

        pg_doc = await asyncio.to_thread(owned_paper)
        raw_pg_doc_id = str(pg_doc.id)
        try:
            canonical_document_id = str(uuid.UUID(raw_pg_doc_id))
        except ValueError:
            canonical_document_id = raw_pg_doc_id

        async with paper_operation_lock(canonical_document_id) as lock_conn:
            # Only proceed with Weaviate query if ownership verified.
            # Every SQL worker under paper_operation_lock reuses lock_conn explicitly
            # and drains before exiting so lock_conn is never released/closed concurrently
            # with an in-flight worker thread.
            latest_job = await run_thread_drained(
                lambda: pdf_job_service.get_latest_job_for_document(
                    document_id=canonical_document_id,
                    user_id=pg_doc.user_id,
                    reconcile_stale=True,
                    connection=lock_conn,
                )
            )
            # A new paper's supplement stage that only waits for the provider
            # ends with the delete (P7b); one that is writing files keeps it refused.
            if pdf_job_service.is_waiting_supplement_stage(latest_job) and await run_thread_drained(
                lambda: pdf_job_service.end_supplement_wait_for_delete(
                    document_id=canonical_document_id,
                    user_id=pg_doc.user_id,
                    connection=lock_conn,
                )
            ):
                latest_job = await run_thread_drained(
                    lambda: pdf_job_service.get_latest_job_for_document(
                        document_id=canonical_document_id,
                        user_id=pg_doc.user_id,
                        reconcile_stale=True,
                        connection=lock_conn,
                    )
                )
            pipeline_status = await pipeline_tracker.get_pipeline_status(canonical_document_id)
            active_job_status = latest_job.status if latest_job else None
            pipeline_is_active = _is_pipeline_status_active(pipeline_status)
            stale_pipeline_tracker = _is_stale_pipeline_tracker(
                pipeline_status=pipeline_status,
                latest_job=latest_job,
            )

            indexed_status = await get_paper_status_for_delete(
                user["sub"], canonical_document_id, connection=lock_conn,
            )
            document_missing_in_weaviate = indexed_status is None
            if indexed_status is None:
                document_processing_status = "missing"
                logger.warning(
                    "Paper missing from Weaviate during delete checks (document_id_hash=%s)",
                    hash_sentry_identifier(canonical_document_id),
                    extra={"sentry_skip_event": True},
                )
            else:
                document_processing_status = _extract_document_processing_status(indexed_status)

            if active_job_status in _ACTIVE_PDF_JOB_STATUSES:
                job_hint = f" and job status '{active_job_status}'" if active_job_status else ""
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cannot delete document while it is being processed "
                        f"(status '{document_processing_status}'{job_hint})"
                    ),
                )
            if pipeline_is_active and not stale_pipeline_tracker:
                stage_value = getattr(getattr(pipeline_status, "current_stage", None), "value", getattr(pipeline_status, "current_stage", None))
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot delete document while pipeline is actively processing (stage '{stage_value}')",
                )
            if stale_pipeline_tracker:
                logger.warning(
                    "Allowing delete despite stale pipeline tracker with terminal job status=%s document_id_hash=%s",
                    active_job_status,
                    hash_sentry_identifier(canonical_document_id),
                    extra={"sentry_skip_event": True},
                )
            if (
                document_processing_status in _ACTIVE_PROCESSING_STATUSES
                and active_job_status not in _TERMINAL_PDF_JOB_STATUSES
            ):
                job_hint = f" and job status '{active_job_status}'" if active_job_status else ""
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cannot delete document while it is being processed "
                        f"(status '{document_processing_status}'{job_hint})"
                    ),
                )
            if (
                document_processing_status in _ACTIVE_PROCESSING_STATUSES
                and active_job_status in _TERMINAL_PDF_JOB_STATUSES
            ):
                logger.warning(
                    "Allowing delete despite stale document processing status=%s with terminal job status=%s document_id_hash=%s",
                    document_processing_status,
                    active_job_status,
                    hash_sentry_identifier(canonical_document_id),
                    extra={"sentry_skip_event": True},
                )

            def snapshot_for_cleanup() -> dict[str, Any]:
                bound_session = open_paper_lock_session(lock_conn)
                snapshot_session = bound_session if bound_session is not None else SessionLocal()
                try:
                    doc_to_cleanup = require_owned_document(snapshot_session, pg_doc.id, pg_doc.user_id)
                    return _document_cleanup_snapshot(doc_to_cleanup)
                except Exception:
                    snapshot_session.rollback()
                    raise
                finally:
                    snapshot_session.close()

            cleanup_snapshot = await run_thread_drained(snapshot_for_cleanup)
            # Every path this delete would remove must stay inside the curator's storage,
            # checked before anything is deleted (pieces, index object, rows, files): one
            # outside refuses the whole delete (403) and the paper stays as it was.
            await asyncio.to_thread(
                _require_removable_paths, cleanup_snapshot, FilePath(get_pdf_storage_path()), str(user["sub"]),
            )

            failure_step = "chunk_wipe"
            failure_already_reported = False

            with delete_drain_context() as drain_state:
                # Remove physical files while holding a row lock so an in-flight rename
                # cannot race with the final path snapshot. Keep the Weaviate parent object
                # and SQL row intact until every stored file is gone so duplicate checks
                # cannot treat the retained row as a phantom and strand files on disk.
                def remove_paper_files() -> None:
                    bound_session = open_paper_lock_session(lock_conn)
                    file_session = bound_session if bound_session is not None else SessionLocal()
                    try:
                        doc_to_clean = require_owned_document(
                            file_session, pg_doc.id, pg_doc.user_id, for_update=True,
                        )
                        cleanup_values = _document_cleanup_snapshot(doc_to_clean)
                        base_storage = FilePath(get_pdf_storage_path())
                        user_sub = str(user["sub"])
                        _require_removable_paths(cleanup_values, base_storage, user_sub)
                        single_files: list[str] = []
                        for file_values in cleanup_values["files"]:
                            # A main stored PDF may take its paper folder with it (only
                            # when the path proves the folder is this paper's). Every
                            # other stored path, whatever the row's status, is removed
                            # one file at a time by the one shared helper, which never
                            # removes a folder and never follows a link (ALL-1460).
                            if (file_values["role"] != FILE_ROLE_SUPPLEMENT
                                    and file_values["display_mode"] != DISPLAY_MODE_TEXT):
                                _delete_user_document_directory_or_file_if_present(
                                    base_storage=base_storage,
                                    relative_path=file_values["file_path"],
                                    user_sub=user_sub,
                                    document_id=canonical_document_id,
                                )
                                single_files.extend(
                                    path for path in _removed_paths(file_values)
                                    if path != file_values["file_path"]
                                )
                            else:
                                single_files.extend(_removed_paths(file_values))
                        remove_stored_files(single_files, owner_subject=user_sub)
                        _remove_empty_paper_folders(base_storage, user_sub, canonical_document_id)
                    except Exception:
                        file_session.rollback()
                        raise
                    finally:
                        file_session.close()

                # Keep the owned SQL row available for retry until files and index entries are gone.
                def finish_delete() -> None:
                    bound_session = open_paper_lock_session(lock_conn)
                    cleanup_session = bound_session if bound_session is not None else SessionLocal()
                    try:
                        doc_to_delete = require_owned_document(
                            cleanup_session, pg_doc.id, pg_doc.user_id, for_update=True,
                        )
                        cleanup_document_curation_dependencies(cleanup_session, doc_to_delete.id)
                        cleanup_session.delete(doc_to_delete)
                        cleanup_session.commit()
                        logger.info(
                            "Deleted PostgreSQL record (document_id_hash=%s)", hash_sentry_identifier(canonical_document_id),
                        )

                        # Invalidate document metadata cache to prevent stale cache hits
                        from src.lib.document_cache import invalidate_cache
                        invalidate_cache(user["sub"], canonical_document_id)
                    except Exception:
                        cleanup_session.rollback()
                        raise
                    finally:
                        cleanup_session.close()

                async def complete_delete() -> OperationResult:
                    nonlocal failure_step, failure_already_reported
                    # Always run the tenant-scoped chunk wipe, even when the parent index object
                    # is already absent, so a retry clears any stray chunks before files and SQL.
                    # ``delete_paper_chunks`` flips ``drain_state.started`` via
                    # ``mark_destructive_delete_started()`` immediately upon submitting its
                    # executor worker (after its pre-submission cancellation checkpoint).
                    # Preflight checks above (including ending a waiting supplement stage)
                    # remain cancellable before the wipe starts.
                    failure_step = "chunk_wipe"
                    chunk_result = await delete_paper_chunks(user["sub"], canonical_document_id)
                    if not chunk_result.get("success"):
                        # The index helper owns both rejected and incomplete wipes.
                        failure_already_reported = True
                        raise HTTPException(
                            status_code=500,
                            detail=PAPER_DELETE_FAILURE_DETAIL,
                        )

                    failure_step = "stored_files"
                    try:
                        await run_thread_drained(
                            remove_paper_files,
                            cancellable_before_start=False,
                        )
                    except (HTTPException, PoolTimeoutError):
                        raise
                    except Exception as file_error:
                        if is_cancelled_delete_drain():
                            raise
                        failure_already_reported = True
                        report_delete_failure(
                            "paper_delete_cleanup_failed", "Paper delete cleanup failed", file_error, step="stored_files",
                        )
                        raise HTTPException(
                            status_code=500,
                            detail=PAPER_DELETE_FAILURE_DETAIL,
                        ) from None

                    if not document_missing_in_weaviate:
                        failure_step = "parent_object"
                        parent_result = await delete_paper_object(
                            user["sub"], canonical_document_id, allow_missing=True,
                        )
                        if not parent_result.get("success"):
                            failure_already_reported = True
                            raise HTTPException(
                                status_code=500,
                                detail=PAPER_DELETE_FAILURE_DETAIL,
                            )

                    failure_step = "sql_cleanup"
                    try:
                        await run_thread_drained(
                            finish_delete,
                            cancellable_before_start=False,
                        )
                    except (HTTPException, PoolTimeoutError):
                        raise
                    except Exception as db_error:
                        if is_cancelled_delete_drain():
                            raise
                        failure_already_reported = True
                        report_delete_failure(
                            "paper_delete_cleanup_failed", "Paper delete cleanup failed", db_error, step="sql_cleanup",
                        )
                        raise HTTPException(
                            status_code=500,
                            detail=PAPER_DELETE_FAILURE_DETAIL,
                        ) from None

                    return OperationResult(
                        success=True,
                        message=f"Document {canonical_document_id} and {chunk_result['chunks_deleted']} chunks deleted successfully",
                        operation="delete_document",
                        document_id=canonical_document_id,
                    )

                def _mark_delete_cancelled() -> None:
                    drain_state.cancelled = True

                def _report_cancelled_delete_failure(exc: Exception) -> None:
                    exc_type = type(exc).__name__
                    document_id_hash = hash_sentry_identifier(canonical_document_id)
                    logger.warning(
                        "Paper delete stopped with %s at %s while draining after cancellation (document_id_hash=%s)",
                        exc_type,
                        failure_step,
                        document_id_hash,
                        extra={"sentry_skip_event": True},
                    )
                    if failure_already_reported:
                        return
                    sanitized = sanitized_runtime_error("Paper delete failed while draining after cancellation")
                    report_runtime_exception(
                        sanitized,
                        component="documents",
                        operation="paper_delete_cancelled_drain_failed",
                        context={
                            "document_id_hash": document_id_hash,
                            "step": failure_step,
                            "exception_type": exc_type,
                        },
                        fingerprint=["paper_delete_cancelled_drain_failed", failure_step, exc_type],
                        include_request=False,
                    )

                # Once the first destructive write is submitted, keep ownership under the paper
                # lock through completion or a recorded failure before delivering cancellation.
                return await drain_shielded(
                    complete_delete(),
                    started=lambda: drain_state.started,
                    on_cancel=_mark_delete_cancelled,
                    on_cancelled_error=_report_cancelled_delete_failure,
                )
    except HTTPException:
        raise
    except PaperOperationInProgress:
        raise HTTPException(
            status_code=409,
            detail="Another operation on this paper is in progress; try again in a moment.",
        ) from None
    except PoolTimeoutError as exc:
        report_delete_failure(
            "paper_delete_db_pool_exhausted", "Paper delete refused: no database connection free", exc,
        )
        raise HTTPException(
            status_code=503,
            detail="The server is busy right now; try deleting the paper again in a moment.",
        ) from None
    except Exception as exc:
        report_delete_failure("paper_delete_failed", "Paper delete failed", exc)
        raise HTTPException(status_code=500, detail=PAPER_DELETE_FAILURE_DETAIL) from None


@router.post("/documents/upload", response_model=DocumentResponse, status_code=201)
async def upload_document_endpoint(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(..., description="PDF file to upload"),
    user: dict[str, Any] = get_auth_dependency()
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
    document_source_context = await _document_source_context_or_503(
        request=request,
        user=user,
    )
    try:
        intake_result = await upload_intake_service.intake_upload(
            background_tasks=background_tasks,
            file=file,
            user=user,
            document_source_context=document_source_context,
        )
        return DocumentResponse(
            document_id=intake_result.document_id,
            job_id=intake_result.job_id,
            user_id=intake_result.user_id,
            filename=intake_result.filename,
            title=None,
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
        raise_sanitized_http_exception(
            logger,
            status_code=400,
            detail=(
                validation_error.client_detail
                if validation_error.client_detail is not None
                else "Invalid document upload request"
            ),
            log_message="Document upload validation failed",
            exc=validation_error,
            level=logging.WARNING,
        )
    except UploadIntakeDuplicateError as duplicate_error:
        raise HTTPException(status_code=409, detail=duplicate_error.detail) from duplicate_error
    except UploadIntakeProviderDecisionError as provider_error:
        raise HTTPException(
            status_code=provider_error.status_code,
            detail=provider_error.detail,
        ) from provider_error
    except Exception as e:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to upload document",
            log_message="Error uploading document",
            exc=e,
        )


@router.post(
    "/documents/import/source-identifiers",
    response_model=DocumentSourceIdentifierImportResponse,
)
async def import_documents_by_source_identifiers(
    payload: DocumentSourceIdentifierImportRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    user: dict[str, Any] = get_auth_dependency(),
):
    """Import one or more documents by configured source-provider identifiers."""
    if not external_document_source_import_enabled():
        raise HTTPException(
            status_code=503,
            detail="Document-source import is disabled",
        )
    document_source_context = await _document_source_context_or_503(
        request=request,
        user=user,
    )
    try:
        result = await identifier_import_service.import_identifiers(
            background_tasks=background_tasks,
            identifiers=payload.identifiers,
            user=user,
            document_source_context=document_source_context,
            supplements=payload.supplements,
        )
        return result.to_dict()
    except SupplementSelectionError as selection_error:
        detail: dict[str, Any] = {"code": selection_error.code}
        if selection_error.limit_value is not None:
            detail["limit_value"] = selection_error.limit_value
        raise HTTPException(status_code=422, detail=detail) from None
    except IdentifierImportValidationError as validation_error:
        raise_sanitized_http_exception(
            logger,
            status_code=400,
            detail="Invalid document-source import request",
            log_message="Document-source identifier import validation failed",
            exc=validation_error,
            level=logging.WARNING,
        )
    except Exception as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to import document-source identifiers",
            log_message="Document-source identifier import failed",
            exc=exc,
        )


@router.post(
    "/documents/resolve/source-identifiers",
    response_model=DocumentSourceIdentifierImportResponse,
)
async def resolve_documents_by_source_identifiers(
    payload: DocumentSourceIdentifierResolveRequest,
    request: Request,
    user: dict[str, Any] = get_auth_dependency(),
):
    """Resolve source-provider identifiers without importing documents."""
    if not external_document_source_import_enabled():
        raise HTTPException(
            status_code=503,
            detail="Document-source import is disabled",
        )
    document_source_context = await _document_source_context_or_503(
        request=request,
        user=user,
    )
    try:
        result = await identifier_import_service.resolve_identifiers(
            identifiers=payload.identifiers,
            user=user,
            document_source_context=document_source_context,
        )
        return result.to_dict()
    except IdentifierImportValidationError as validation_error:
        raise_sanitized_http_exception(
            logger,
            status_code=400,
            detail="Invalid document-source resolve request",
            log_message="Document-source identifier resolve validation failed",
            exc=validation_error,
            level=logging.WARNING,
        )
    except Exception as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to resolve document-source identifiers",
            log_message="Document-source identifier resolve failed",
            exc=exc,
        )


async def _file_listing_response(list_call) -> dict[str, Any]:
    """Run a file listing and map its failures to codes (no names, no text)."""

    from ..lib.document_sources.file_listing import listing_to_payload
    from ..lib.document_sources.file_listing_service import FileListingUnavailable
    from ..lib.document_sources.models import DocumentSourceReferenceNotFound
    from ..lib.document_sources.registry import get_configured_document_source_provider

    provider = get_configured_document_source_provider()
    try:
        return listing_to_payload(await list_call(provider))
    except FileListingUnavailable as unavailable:
        status_code = 400 if unavailable.code == "invalid_identifier" else 409
        raise HTTPException(status_code=status_code, detail={"error": unavailable.code}) from None
    except DocumentSourceReferenceNotFound:
        raise HTTPException(status_code=404, detail={"error": "reference_not_found"}) from None
    except HTTPException:
        raise
    except Exception as exc:
        message = "Document-source file listing failed"
        logger.error(message, extra={"sentry_skip_event": True})
        try:
            report_runtime_exception(
                sanitized_runtime_error(message),
                component="document_file_listing",
                operation="file_listing_failed",
                context={"exception_type": type(exc).__name__},
                include_request=False,
            )
        except Exception:
            # A reporting failure must preserve the public HTTP error.
            pass
    finally:
        try:
            await provider.aclose()
        except Exception as cleanup_error:
            message = "Document-source file listing provider cleanup failed"
            logger.warning(message, extra={"sentry_skip_event": True})
            try:
                report_runtime_exception(
                    sanitized_runtime_error(message),
                    component="document_file_listing",
                    operation="provider_cleanup_failed",
                    level="warning",
                    context={"exception_type": type(cleanup_error).__name__},
                    include_request=False,
                )
            except Exception:
                # Reporting must preserve the listing response or mapped HTTP error.
                pass

    # Raise outside the handler so the provider error cannot become __context__.
    raise HTTPException(status_code=500, detail={"error": "file_listing_failed"}) from None


@router.get("/document-sources/files")
async def list_document_source_files_by_identifier(
    request: Request,
    identifier: str = Query(..., min_length=1, max_length=200),
    user: dict[str, Any] = get_auth_dependency(),
):
    """List a reference's provider files with a status and check result per file (codes only)."""
    from ..lib.document_sources.file_listing_service import list_files_for_identifier

    if not external_document_source_import_enabled():
        raise HTTPException(status_code=503, detail={"error": "document_source_import_disabled"})
    db_user = await provision_user_async(principal_from_claims(user))
    context = await _document_source_context_or_503(request=request, user=user)
    return await _file_listing_response(
        lambda provider: list_files_for_identifier(
            identifier, owner_user_id=db_user.id, provider=provider, context=context,
        )
    )


@router.get("/documents/{document_id}/source-files")
async def list_document_source_files_for_paper(
    request: Request,
    document_id: str = Path(..., description="Document ID"),
    user: dict[str, Any] = get_auth_dependency(),
):
    """List an imported paper's provider files; files already in the paper are marked."""
    from ..lib.document_sources.file_listing_service import list_files_for_paper

    if not external_document_source_import_enabled():
        raise HTTPException(status_code=503, detail={"error": "document_source_import_disabled"})
    document_uuid = _parse_document_uuid(document_id)
    db_user = await provision_user_async(principal_from_claims(user))
    context = await _document_source_context_or_503(request=request, user=user)
    return await _file_listing_response(
        lambda provider: list_files_for_paper(
            document_uuid, owner_user_id=db_user.id, provider=provider, context=context,
        )
    )


@router.post("/documents/{document_id}/files", status_code=202)
async def add_files_to_paper_endpoint(
    payload: AddFilesRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    document_id: str = Path(..., description="Document ID"),
    user: dict[str, Any] = get_auth_dependency(),
):
    """Add converted supplements from the paper's provider file listing (codes only).

    Starts one job; the files are checked and added in the background, and each
    file's outcome is in the job's ``metadata.supplements``. Owner only.
    """
    from ..lib.document_sources.supplement_add import (
        ChosenFile,
        FileRemovalUnfinished,
        SupplementAddJobRequest,
        SupplementAddRequestError,
        run_supplement_add_job,
        start_supplement_add,
    )
    from ..lib.observability.background_tasks import add_observed_background_task
    from ..lib.pdf_jobs.service import ActiveJobExistsError

    if not external_document_source_import_enabled():
        raise HTTPException(status_code=503, detail={"error": "document_source_import_disabled"})
    document_uuid = _parse_document_uuid(document_id)
    db_user = await provision_user_async(principal_from_claims(user))
    context = await _document_source_context_or_503(request=request, user=user)
    if not (context.curator_token or "").strip():
        raise HTTPException(status_code=409, detail={"error": "document_source_curator_token_unavailable"})
    files = tuple(
        ChosenFile(provider_file_id=item.provider_file_id, md5=item.md5, converted_md5=item.converted_md5)
        for item in payload.files
    )
    try:
        job = await asyncio.to_thread(start_supplement_add, document_uuid, db_user.id, files)
    except SupplementAddRequestError as refused:
        raise HTTPException(
            status_code=422, detail={"error": refused.code, "limit_value": refused.limit_value},
        ) from None
    except ActiveJobExistsError:
        raise HTTPException(status_code=409, detail={"error": "import_in_progress"}) from None
    except FileRemovalUnfinished as refused:
        raise HTTPException(status_code=409, detail={"error": refused.code}) from None
    add_observed_background_task(
        background_tasks,
        run_supplement_add_job,
        SupplementAddJobRequest(
            job_id=uuid.UUID(job.job_id), document_id=document_uuid, user_id=db_user.auth_sub,
            owner_user_id=db_user.id, context=context, files=files,
        ),
        task_name="supplement_add",
        tags={"document_id": str(document_uuid), "job_id": job.job_id},
    )
    return job


@router.get("/documents/{document_id}/files/{file_id}/removal-check")
async def removal_check_endpoint(
    document_id: str = Path(..., description="Document ID"),
    file_id: str = Path(..., description="The paper file the curator may remove (pdf_document_files id)"),
    user: dict[str, Any] = get_auth_dependency(),
):
    """Read-only: what removing this file would do now, for the removal confirmation (codes only).

    ``removable`` and ``error`` (plus ``session_ids``) are the removal's own checks, made
    without locks; ``newest_result_quotes_file`` says whether the result the Library's
    Review & Curate would replay quotes the file, so after the removal that button would
    answer ``result_quotes_removed_file`` until the extraction is run again. Owner only.
    """
    from ..lib.document_files.file_removal import removal_check

    document_uuid = _parse_document_uuid(document_id)
    try:
        file_uuid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"error": "file_not_in_paper"}) from None
    db_user = await provision_user_async(principal_from_claims(user))
    return await asyncio.to_thread(removal_check, document_uuid, db_user.id, file_uuid)


@router.delete("/documents/{document_id}/files/{file_id}")
async def remove_paper_file_endpoint(
    document_id: str = Path(..., description="Document ID"),
    file_id: str = Path(..., description="The paper file to remove (pdf_document_files id)"),
    user: dict[str, Any] = get_auth_dependency(),
):
    """Remove one supplement from a finished paper (codes only). Owner only; never the main file.

    Held under the paper's operation lock, never waited for: while an add, a paper
    delete or another removal holds it, this answers 409 at once. Every database
    call runs in a worker thread; a pool timeout is a 503.
    """
    from sqlalchemy.exc import TimeoutError as PoolTimeoutError

    from ..lib.document_files.file_removal import FileRemovalRefused, PaperRowsBusy, remove_paper_file
    from ..lib.document_files.paper_lock import PaperOperationInProgress, paper_operation_lock
    from ..lib.weaviate_client.connection import get_connection

    document_uuid = _parse_document_uuid(document_id)
    try:
        file_uuid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"error": "file_not_in_paper"}) from None
    try:
        db_user = await provision_user_async(principal_from_claims(user))
        async with paper_operation_lock(document_uuid) as lock_conn:
            removed = await remove_paper_file(
                document_uuid,
                db_user.id,
                file_uuid,
                weaviate_client=get_connection(),
                lock_connection=lock_conn,
            )
    except (PaperOperationInProgress, PaperRowsBusy):
        raise HTTPException(
            status_code=409,
            detail="Another operation on this paper is in progress; try again in a moment.",
        ) from None
    except PoolTimeoutError:
        logger.warning("File removal refused: no database connection free", extra={"document_id": document_id})
        raise HTTPException(
            status_code=503,
            detail="The server is busy right now; try removing the file again in a moment.",
        ) from None
    except FileRemovalRefused as refused:
        raise HTTPException(
            status_code=refused.status_code, detail={"error": refused.code, **refused.details},
        ) from None
    return {
        "document_id": str(removed.document_id),
        "file_id": str(removed.file_id),
        "removed": True,
        # False when part of the clean-up failed: the file is gone for every reader, and
        # removing it again (or deleting the paper) finishes the clean-up.
        "cleanup_finished": removed.cleanup_finished,
        "ready_file_count": removed.ready_file_count,
    }


@router.get("/documents/{document_id}/status")
async def get_document_processing_status(
    document_id: str = Path(..., description="Document ID"),
    user: dict[str, Any] = get_auth_dependency()
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
        db_user = await provision_user_async(principal_from_claims(user))
        pg_doc = verify_document_ownership(session, document_id, user, provisioned_user_id=db_user.id)
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
        job = pdf_job_service.get_latest_paper_job_for_document(
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
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to get document status",
            log_message=f"Error getting document status for {document_id}",
            exc=e,
        )


@router.get("/documents/{document_id}/progress/stream")
async def stream_document_progress(
    document_id: str = Path(..., description="Document ID"),
    user: dict[str, Any] = get_auth_dependency()
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
        db_user = await provision_user_async(principal_from_claims(user))
        try:
            verify_document_ownership(session, document_id, user, provisioned_user_id=db_user.id)
        except HTTPException as exc:
            # Keep SSE behavior backward-compatible for malformed/missing IDs:
            # emit terminal stream events instead of failing the HTTP handshake.
            if exc.status_code not in {400, 404}:
                raise
    finally:
        session.close()

    async def generate():
        """Generate SSE events for document processing progress."""
        last_status_snapshot = None
        retry_count = 0
        poll_interval_raw = os.getenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
        timeout_raw = os.getenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS")

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
            timeout_seconds = (
                int(timeout_raw) if timeout_raw else
                get_pdf_extraction_queue_timeout_seconds() + int(os.getenv("PDF_EXTRACTION_TIMEOUT", "3600"))
            )
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
                    job = pdf_job_service.get_latest_paper_job_for_document(
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
                        if status_snapshot.get("metadata") is not None:
                            event_data["metadata"] = status_snapshot["metadata"]

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
                            if status_snapshot.get("metadata") is not None:
                                final_data["metadata"] = status_snapshot["metadata"]
                            yield f"data: {json.dumps(final_data)}\n\n"
                            break
                else:
                    # No status yet, send initial waiting message
                    if retry_count == 0:
                        waiting_data = {
                            'stage': 'waiting',
                            'progress': 0,
                            'message': 'Waiting for processing to start...',
                            'timestamp': datetime.now(timezone.utc).isoformat()
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
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'final': True
                }
                yield f"data: {json.dumps(timeout_data)}\n\n"

        except Exception as e:
            try:
                report_runtime_exception(
                    sanitized_runtime_error("Document progress stream failed"),
                    component="documents",
                    operation="stream_document_progress",
                    tags={"phase": "progress_stream"},
                    context={"exception_type": type(e).__name__},
                )
            except Exception:
                # Reporting must not prevent the terminal, client-safe SSE event.
                logger.warning(
                    "Failed to report document progress stream failure",
                    extra={"sentry_skip_event": True},
                )
            log_exception(
                logger,
                message=f"Error in SSE stream for document {document_id}",
                exc=e,
            )
            error_data = {
                'error': "Failed to stream document progress",
                'document_id': document_id,
                'timestamp': datetime.now(timezone.utc).isoformat()
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
    user: dict[str, Any] = get_auth_dependency()
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
            db_user = await provision_user_async(principal_from_claims(user))

            doc = require_owned_document(session, document_uuid, db_user.id)

            main_file = doc.main_file_or_none
            if main_file is None:
                # Downloads are of the main file; a paper of supplements only has none.
                return {
                    "pdf_available": False,
                    "pdf_size": None,
                    "pdfx_json_available": False,
                    "pdfx_json_size": None,
                    "processed_json_available": False,
                    "processed_json_size": None,
                    "source_markdown_available": False,
                    "source_markdown_size": None,
                    "viewer_mode": None,
                    "has_main_file": False,
                    "source_provenance": build_document_source_provenance(doc),
                    "filename": doc.filename,
                }
            viewer_mode = main_file.display_mode
            is_text_only = viewer_mode == DISPLAY_MODE_TEXT

            # Check PDF file with path validation (T032)
            pdf_storage = get_pdf_storage_path()
            pdf_path = None
            pdf_available = False
            pdf_size = None
            if main_file.file_path and not is_text_only:
                pdf_path = FilePath(pdf_storage) / main_file.file_path
                # Validate path stays within user's storage directory
                pdf_path = validate_user_file_path(pdf_path, FilePath(pdf_storage), user["sub"])
                pdf_available = pdf_path.exists()
                pdf_size = pdf_path.stat().st_size if pdf_available else None

            # Check PDFX JSON file with path validation (T032)
            # Note: pdfx_json_path is stored relative to pdf_storage: {user_id}/pdfx_json/{doc_id}.json
            pdfx_json_available = False
            pdfx_json_size = None
            if main_file.pdfx_json_path:
                # Construct full path from pdf_storage (paths are relative to pdf_storage root)
                pdfx_path = FilePath(pdf_storage) / main_file.pdfx_json_path
                # Validate path stays within user's storage directory (pdf_storage/{user_id})
                pdfx_path = validate_user_file_path(pdfx_path, FilePath(pdf_storage), user["sub"])
                pdfx_json_available = pdfx_path.exists()
                pdfx_json_size = pdfx_path.stat().st_size if pdfx_json_available else None

            # Check processed JSON file with path validation (T032)
            # Note: processed_json_path is stored relative to pdf_storage ({user_id}/processed_json/..., named by paper id for older and PDF-processed files, by file id for provider imports)
            processed_json_available = False
            processed_json_size = None
            if main_file.processed_json_path:
                # Construct full path from pdf_storage (paths are relative to pdf_storage root)
                processed_path = FilePath(pdf_storage) / main_file.processed_json_path
                # Validate path stays within user's storage directory (pdf_storage/{user_id})
                processed_path = validate_user_file_path(processed_path, FilePath(pdf_storage), user["sub"])
                processed_json_available = processed_path.exists()
                processed_json_size = processed_path.stat().st_size if processed_json_available else None

            # Check provider/source Markdown artifact with path validation.
            source_markdown_available = False
            source_markdown_size = None
            source_markdown_relative_path = main_file.source_markdown_path
            if source_markdown_relative_path:
                source_markdown_path = FilePath(pdf_storage) / source_markdown_relative_path
                source_markdown_path = validate_user_file_path(
                    source_markdown_path,
                    FilePath(pdf_storage),
                    user["sub"],
                )
                source_markdown_available = source_markdown_path.exists()
                source_markdown_size = (
                    source_markdown_path.stat().st_size
                    if source_markdown_available
                    else None
                )

            return {
                "pdf_available": pdf_available,
                "pdf_size": pdf_size,
                "pdfx_json_available": pdfx_json_available,
                "pdfx_json_size": pdfx_json_size,
                "processed_json_available": processed_json_available,
                "processed_json_size": processed_json_size,
                "source_markdown_available": source_markdown_available,
                "source_markdown_size": source_markdown_size,
                "viewer_mode": viewer_mode,
                "has_main_file": True,
                "source_provenance": build_document_source_provenance(doc),
                "filename": doc.filename
            }
        finally:
            session.close()

    except HTTPException:
        raise
    except Exception as e:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to get download info",
            log_message=f"Error getting download info for document {document_id}",
            exc=e,
        )


@router.get("/documents/{document_id}/download/{file_type}")
async def download_document_file(
    document_id: str = Path(..., description="Document ID"),
    file_type: str = Path(..., description="File type to download (pdf, pdfx_json, processed_json, source_markdown)"),
    user: dict[str, Any] = get_auth_dependency()
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

        if file_type not in ['pdf', 'pdfx_json', 'processed_json', 'source_markdown']:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type: {file_type}"
            )

        # Get document from database
        session = SessionLocal()
        try:
            # T031: Get database user for ownership verification (FR-014)
            db_user = await provision_user_async(principal_from_claims(user))

            doc = require_owned_document(session, document_uuid, db_user.id)

            # Determine file path based on type with path validation (T032)
            file_path = None
            filename = None
            media_type = "application/octet-stream"
            main_file = doc.main_file_or_none
            if main_file is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"{file_type} file not available for document {document_id}"
                )
            viewer_mode = main_file.display_mode
            is_text_only = viewer_mode == DISPLAY_MODE_TEXT

            if file_type == 'pdf':
                if main_file.file_path and not is_text_only:
                    pdf_storage = get_pdf_storage_path()
                    file_path = FilePath(pdf_storage) / main_file.file_path
                    # Validate path stays within user's storage directory
                    file_path = validate_user_file_path(file_path, FilePath(pdf_storage), user["sub"])
                    filename = doc.filename
                    media_type = "application/pdf"

            elif file_type == 'pdfx_json':
                # Note: pdfx_json_path is stored relative to pdf_storage: {user_id}/pdfx_json/{doc_id}.json
                if main_file.pdfx_json_path:
                    pdf_storage = get_pdf_storage_path()
                    # Construct full path from pdf_storage (paths are relative to pdf_storage root)
                    file_path = FilePath(pdf_storage) / main_file.pdfx_json_path
                    # Validate path stays within user's storage directory (pdf_storage/{user_id})
                    file_path = validate_user_file_path(file_path, FilePath(pdf_storage), user["sub"])
                    filename = f"{doc.filename.rsplit('.', 1)[0]}_pdfx.json"
                    media_type = "application/json"

            elif file_type == 'processed_json':
                # Note: processed_json_path is stored relative to pdf_storage ({user_id}/processed_json/..., named by paper id for older and PDF-processed files, by file id for provider imports)
                if main_file.processed_json_path:
                    pdf_storage = get_pdf_storage_path()
                    # Construct full path from pdf_storage (paths are relative to pdf_storage root)
                    file_path = FilePath(pdf_storage) / main_file.processed_json_path
                    # Validate path stays within user's storage directory (pdf_storage/{user_id})
                    file_path = validate_user_file_path(file_path, FilePath(pdf_storage), user["sub"])
                    filename = f"{doc.filename.rsplit('.', 1)[0]}_processed.json"
                    media_type = "application/json"

            elif file_type == 'source_markdown':
                source_markdown_relative_path = main_file.source_markdown_path
                if source_markdown_relative_path:
                    pdf_storage = get_pdf_storage_path()
                    file_path = FilePath(pdf_storage) / source_markdown_relative_path
                    file_path = validate_user_file_path(file_path, FilePath(pdf_storage), user["sub"])
                    filename = f"{doc.filename.rsplit('.', 1)[0]}_source.md"
                    media_type = "text/markdown; charset=utf-8"

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
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to download file",
            log_message=f"Error downloading {file_type} for document {document_id}",
            exc=e,
        )
