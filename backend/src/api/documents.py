"""Documents API endpoints for Weaviate Control Panel.

Task: T026 - Add Security() dependency injection to document endpoints
All endpoints now require valid Okta JWT token via Security(auth.get_user).
"""

from fastapi import APIRouter, HTTPException, Query, Path, Depends, UploadFile, File, BackgroundTasks, Security
from fastapi.responses import StreamingResponse, FileResponse
from typing import Dict, Any
from typing import Optional, List
from datetime import datetime
from pathlib import Path as FilePath
import logging
import asyncio
import json
import uuid
import os
import shutil

import httpx

from .auth import get_auth_dependency

from ..services.user_service import set_global_user_from_cognito
from ..lib.weaviate_helpers import get_tenant_name

from ..models.api_schemas import (
    DocumentListRequest,
    DocumentListResponse,
    DocumentDetailResponse,
    DocumentFilter,
    PaginationInfo,
    SortBy,
    SortOrder,
    OperationResult,
    DocumentResponse
)
from ..models.document import EmbeddingStatus, ProcessingStatus
from ..models.pipeline import ProcessingStage
from ..lib.weaviate_helpers import get_connection
from ..lib.weaviate_client.documents import (
    async_list_documents as list_documents,
    get_document,
    delete_document,
    search_similar,
    create_document
)
from ..lib.pipeline.upload import PDFUploadHandler
from ..lib.pipeline.orchestrator import DocumentPipelineOrchestrator
from ..lib.pipeline.tracker import PipelineTracker
from ..config import get_pdf_storage_path
from ..models.sql.database import SessionLocal
from ..models.sql.pdf_document import PDFDocument as ViewerPDFDocument
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from ..schemas.documents import DocumentUpdateRequest, DocumentUpdateResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/weaviate")
pipeline_tracker = PipelineTracker()


async def cleanup_phantom_documents(user: Dict[str, Any]) -> int:
    """Clean up phantom documents - records in PostgreSQL that don't exist in Weaviate.

    This prevents the "invisible documents" issue where a user has documents in the
    database but they don't appear in the UI because the Weaviate records are missing.

    Args:
        user: Authenticated user dict with 'sub' key

    Returns:
        Number of phantom documents cleaned up
    """
    from ..lib.weaviate_helpers import get_connection, get_user_collections, get_tenant_name
    from ..models.sql.user import User

    user_id = user["sub"]
    cleaned_count = 0

    logger.info(f"[Phantom Check] Starting phantom document check for user {user_id[:8]}...")

    try:
        # Step 1: Get user's database ID
        session = SessionLocal()
        try:
            db_user = session.query(User).filter(User.auth_sub == user_id).first()
            if not db_user:
                logger.info(f"[Phantom Check] User not provisioned yet, skipping")
                return 0  # User not provisioned yet

            # Step 2: Get all document IDs from PostgreSQL for this user
            pg_docs = session.query(ViewerPDFDocument).filter(
                ViewerPDFDocument.user_id == db_user.id
            ).all()

            if not pg_docs:
                logger.info(f"[Phantom Check] No documents in PostgreSQL, nothing to check")
                return 0  # No documents to check

            pg_doc_ids = {str(doc.id) for doc in pg_docs}
            logger.info(f"[Phantom Check] Found {len(pg_doc_ids)} documents in PostgreSQL")

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
                    logger.info(f"[Phantom Check] Found {len(weaviate_doc_ids)} documents in Weaviate")

                except Exception as e:
                    logger.warning(f"[Phantom Check] Error fetching Weaviate documents: {e}")
                    return 0

            # Step 4: Find inconsistencies in both directions
            # Phantom = in PostgreSQL but NOT in Weaviate (user can't see their doc)
            # Orphan = in Weaviate but NOT in PostgreSQL (leftover data)
            phantom_ids = pg_doc_ids - weaviate_doc_ids
            orphan_ids = weaviate_doc_ids - pg_doc_ids

            if not phantom_ids and not orphan_ids:
                logger.info(f"[Phantom Check] ✓ All clean - {len(pg_doc_ids)} documents in sync")
                return 0

            # Step 5a: Delete phantom records from PostgreSQL
            if phantom_ids:
                logger.warning(f"[Phantom Check] Found {len(phantom_ids)} phantom documents (in PG, not in Weaviate)")

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
                                    logger.info(f"[Phantom Check] Cleaned up filesystem for phantom doc {phantom_id}")
                        except Exception as fs_err:
                            logger.warning(f"[Phantom Check] Failed to cleanup files for phantom {phantom_id}: {fs_err}")

                        session.delete(phantom_doc)
                        cleaned_count += 1
                        logger.info(f"[Phantom Check] Deleted phantom PG record {phantom_id} ({phantom_doc.filename})")

                session.commit()

            # Step 5b: Delete orphan records from Weaviate
            if orphan_ids:
                logger.warning(f"[Phantom Check] Found {len(orphan_ids)} orphan documents (in Weaviate, not in PG)")

                with connection.session() as client:
                    try:
                        _, pdf_collection = get_user_collections(client, user_id)

                        for orphan_id in orphan_ids:
                            try:
                                pdf_collection.data.delete_by_id(uuid.UUID(orphan_id))
                                cleaned_count += 1
                                logger.info(f"[Phantom Check] Deleted orphan Weaviate doc {orphan_id}")
                            except Exception as del_err:
                                logger.warning(f"[Phantom Check] Failed to delete orphan {orphan_id}: {del_err}")

                    except Exception as e:
                        logger.error(f"[Phantom Check] Error cleaning orphan Weaviate docs: {e}")

            logger.info(f"[Phantom Check] Cleaned up {cleaned_count} inconsistent documents for user {user_id[:8]}")

        finally:
            session.close()

    except Exception as e:
        logger.error(f"Error during phantom document cleanup: {e}")

    return cleaned_count


def verify_document_ownership(
    db: Session,
    document_id: str,
    okta_user: Dict[str, Any]
) -> ViewerPDFDocument:
    """Verify document ownership and return document if authorized.

    Args:
        db: Database session
        document_id: Document UUID to check
        okta_user: Authenticated Okta user

    Returns:
        ViewerPDFDocument if user owns the document

    Raises:
        HTTPException: 404 if document not found, 403 if not owned by user

    Requirements: FR-014 (cross-user access prevention with 403)
    """
    # Get database user
    db_user = set_global_user_from_cognito(db, okta_user)

    # Query document from PostgreSQL
    doc = db.query(ViewerPDFDocument).filter(
        ViewerPDFDocument.id == uuid.UUID(document_id)
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
        >>> storage = Path("/app/pdf_storage")
        >>> user_path = storage / "00u1abc2" / "doc.pdf"
        >>> validate_user_file_path(user_path, storage, "00u1abc2")
        Path("/app/pdf_storage/00u1abc2/doc.pdf")
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
                f"Path traversal attempt detected: {file_path} resolves outside user storage for {user_id}"
            )
            raise HTTPException(
                status_code=403,
                detail="Access denied: file path validation failed"
            )

        return resolved_path

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating file path {file_path}: {e}")
        raise HTTPException(
            status_code=500,
            detail="File path validation error"
        )


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
            logger.info(f"Cleaned up {phantoms_cleaned} phantom documents before listing")

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
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve documents: {str(e)}"
        )


@router.get("/documents/docling-health")
async def get_docling_health():
    """Report health status for the Docling parsing service."""

    service_url = os.getenv("DOCLING_SERVICE_URL", "http://docling-internal.alliancegenome.org:8000").rstrip("/")
    health_endpoint = f"{service_url}/health"
    checked_at = datetime.utcnow().isoformat() + "Z"

    timeout_seconds = float(os.getenv("DOCLING_HEALTH_TIMEOUT", "5"))

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(health_endpoint)

        try:
            payload = response.json()
        except ValueError:
            payload = None

        is_healthy = (
            response.status_code == 200
            and isinstance(payload, dict)
            and str(payload.get("status", "")).lower() == "healthy"
        )

        status = "healthy" if is_healthy else "degraded"

        return {
            "status": status,
            "service_url": service_url,
            "last_checked": checked_at,
            "response_code": response.status_code,
            "details": payload,
        }

    except httpx.RequestError as exc:
        logger.warning("Docling health check failed: %s", exc)
        return {
            "status": "unreachable",
            "service_url": service_url,
            "last_checked": checked_at,
            "error": str(exc),
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
        db_user = set_global_user_from_cognito(session, user)
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

        # T031: Return contract Document schema (document_endpoints.yaml)
        return DocumentResponse(
            document_id=document_id,
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
        logger.error(f"Error retrieving document {document_id}: {e}")
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
        logger.error(f"Error updating document {document_id}: {e}")
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
        document = await get_document(user["sub"], document_id)

        if not document:
            raise HTTPException(
                status_code=404,
                detail=f"Document with ID {document_id} not found"
            )

        doc_payload = document.get("document", {})
        if doc_payload.get("processing_status") == ProcessingStatus.PROCESSING:
            raise HTTPException(
                status_code=409,
                detail="Cannot delete document while it is being processed"
            )

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
                            logger.info(f"Deleted filesystem artifacts for document {document_id}")

                    # 2. Delete Docling JSON: {user_id}/docling_json/{doc_id}.json
                    if doc_to_delete.docling_json_path:
                        docling_path = FilePath(base_storage) / doc_to_delete.docling_json_path
                        if docling_path.exists() and base_storage in docling_path.parents:
                            docling_path.unlink()
                            logger.info(f"Deleted Docling JSON for {document_id}")

                    # 3. Delete Processed JSON: {user_id}/processed_json/{doc_id}.json
                    if doc_to_delete.processed_json_path:
                        processed_path = FilePath(base_storage) / doc_to_delete.processed_json_path
                        if processed_path.exists() and base_storage in processed_path.parents:
                            processed_path.unlink()
                            logger.info(f"Deleted Processed JSON for {document_id}")

                except Exception as fs_error:
                    logger.error(f"Failed to clean up files for {document_id}: {fs_error}")

                # Delete DB record
                cleanup_session.delete(doc_to_delete)
                cleanup_session.commit()
                logger.info(f"Deleted PostgreSQL record for {document_id}")

                # Invalidate document metadata cache to prevent stale cache hits
                from src.lib.document_cache import invalidate_cache
                invalidate_cache(user["sub"], document_id)
        except Exception as db_error:
            logger.error(f"Failed to cleanup PostgreSQL for {document_id}: {db_error}")
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
        logger.error(f"Error deleting document {document_id}: {e}")
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
        # Validate file is PDF
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(
                status_code=400,
                detail=f"File must be a PDF. Got: {file.filename}"
            )

        # T029: Use user-specific storage path (FR-012)
        # Create: pdf_storage/{user_id}/
        from pathlib import Path
        base_storage = get_pdf_storage_path()
        user_storage_path = base_storage / user["sub"]  # User-specific directory
        user_storage_path.mkdir(parents=True, exist_ok=True)

        # Create upload handler with user-specific path
        upload_handler = PDFUploadHandler(storage_path=user_storage_path)

        # Save uploaded file and create document
        saved_path, document = await upload_handler.save_uploaded_pdf(file)

        # T029: CRITICAL - Provision user and tenant BEFORE Weaviate operations
        # This ensures provision_weaviate_tenants() runs before create_document()
        from src.services.user_service import set_global_user_from_cognito

        session = SessionLocal()
        try:
            # Ensure user exists in database and provision Weaviate tenants
            db_user = set_global_user_from_cognito(session, user)

            # Check for duplicate file BEFORE creating Weaviate document
            # This prevents UUID mismatch between PostgreSQL and Weaviate
            existing = (
                session.execute(
                    select(ViewerPDFDocument).where(
                        ViewerPDFDocument.user_id == db_user.id,
                        ViewerPDFDocument.file_hash == document.metadata.checksum
                    )
                )
                .scalars()
                .first()
            )

            if existing:
                # Phase 2 Fix: Check for "Phantom" document (exists in PG but not Weaviate)
                try:
                    # Check if the OLD document exists in Weaviate
                    existing_weaviate_doc = await get_document(user["sub"], str(existing.id))
                    if not existing_weaviate_doc:
                        logger.warning(f"Phantom document detected (Hash match in PG, missing in Weaviate): {existing.id}. Cleaning up old record.")

                        # Delete the phantom PG record
                        session.delete(existing)
                        session.commit()

                        # Clear 'existing' so we don't trigger the 409 error
                        existing = None
                except ValueError as not_found_err:
                    # Document not found in Weaviate - this IS a phantom document
                    # get_document raises ValueError when document doesn't exist
                    logger.warning(f"Phantom document detected (ValueError - not in Weaviate): {existing.id}. Cleaning up: {not_found_err}")

                    # Delete the phantom PG record
                    session.delete(existing)
                    session.commit()

                    # Clear 'existing' so we don't trigger the 409 error
                    existing = None
                except Exception as check_err:
                    logger.error(f"Error checking phantom status: {check_err}")
                    # If check fails for OTHER reasons, we conservatively assume it's a real duplicate

                if existing:
                    # Cleanup: Remove the newly uploaded file since it's a duplicate
                    if saved_path.exists():
                        # Remove the document directory (contains the PDF)
                        shutil.rmtree(saved_path.parent)

                    session.close()
                    raise HTTPException(
                        status_code=409,  # Conflict
                        detail={
                            "error": "duplicate_file",
                            "message": f"This file has already been uploaded on {existing.upload_timestamp.strftime('%B %d, %Y at %I:%M %p')}",
                            "existing_document_id": str(existing.id),
                            "uploaded_at": existing.upload_timestamp.isoformat(),
                            "suggestion": "If you want to re-process this file, delete the existing document first and then upload again."
                        }
                    )

            # T029: Store document in tenant-scoped Weaviate collection (FR-011, FR-013)
            # Now this will succeed because tenant exists
            await create_document(user["sub"], document)

            # Calculate relative path from user storage directory
            relative_path = saved_path.relative_to(base_storage)  # Includes {user_id}/ prefix

            record = ViewerPDFDocument(
                id=uuid.UUID(document.id),
                filename=document.filename,
                file_path=str(relative_path).replace('\\', '/'),
                file_hash=document.metadata.checksum,
                file_size=saved_path.stat().st_size,
                page_count=max(document.metadata.page_count, 1),
                user_id=db_user.id,  # T029: Track document ownership (FR-016)
            )
            session.merge(record)
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = (
                session.execute(
                    select(ViewerPDFDocument).where(ViewerPDFDocument.file_hash == document.metadata.checksum)
                )
                .scalars()
                .first()
            )
            if existing:
                existing.filename = document.filename
                existing.file_path = str(relative_path).replace('\\', '/')
                existing.file_size = saved_path.stat().st_size
                existing.page_count = max(document.metadata.page_count, 1)
                existing.user_id = db_user.id  # T029: Update ownership on re-upload
                session.commit()
        finally:
            session.close()

        # Track initial status
        from ..models.pipeline import ProcessingStage
        await pipeline_tracker.track_pipeline_progress(document.id, ProcessingStage.UPLOAD)

        # Start processing pipeline in background
        # Capture user["sub"] for background task (FR-011, FR-014: tenant scoping)
        user_id = user["sub"]

        async def process_document():
            try:
                connection = get_connection()
                orchestrator = DocumentPipelineOrchestrator(
                    weaviate_client=connection,
                    tracker=pipeline_tracker,
                )
                result = await orchestrator.process_pdf_document(
                    file_path=saved_path,
                    document_id=document.id,
                    user_id=user_id,  # FR-011, FR-014: Pass user ID for tenant scoping
                    validate_first=False  # Already validated
                )
                logger.info(f"Document {document.id} processing completed: {result}")
            except Exception as e:
                logger.error(f"Error processing document {document.id}: {e}", exc_info=True)
                # Update document status to failed
                from ..lib.weaviate_client.documents import update_document_status
                await update_document_status(document.id, user_id, "failed")

        # Add processing task to background
        background_tasks.add_task(process_document)

        # T029: Return Document schema with ownership metadata (FR-014, FR-016)
        # Get tenant name for response
        tenant_name = get_tenant_name(user["sub"])

        return DocumentResponse(
            document_id=document.id,
            user_id=db_user.id,
            filename=document.filename,
            status="PENDING",  # Initial status
            upload_timestamp=datetime.utcnow(),
            processing_started_at=None,  # Not started yet
            processing_completed_at=None,
            file_size_bytes=saved_path.stat().st_size,
            weaviate_tenant=tenant_name,
            chunk_count=None,  # Not processed yet
            error_message=None
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading document: {e}")
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
        verify_document_ownership(session, document_id, user)
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

        # Get pipeline status
        pipeline_status = await pipeline_tracker.get_pipeline_status(document_id)

        return {
            "document_id": document_id,
            "processing_status": document["document"].get("processing_status", "pending"),
            "embedding_status": document["document"].get("embedding_status", "pending"),
            "pipeline_status": pipeline_status.dict() if pipeline_status else None,
            "chunk_count": document.get("total_chunks", 0),
            "vector_count": document["document"].get("vector_count", 0)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document status: {e}")
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
    async def generate():
        """Generate SSE events for document processing progress."""
        last_status_snapshot = None
        retry_count = 0
        max_retries = 300  # 5 minutes max wait time (300 * 1 second)

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
                status = await pipeline_tracker.get_pipeline_status(document_id)

                if status:
                    status_snapshot = status.model_dump()

                    # Check if status has changed
                    if status_snapshot != last_status_snapshot:
                        stage_value = status_snapshot.get('current_stage', ProcessingStage.PENDING)
                        if isinstance(stage_value, ProcessingStage):
                            stage_str = stage_value.value
                        else:
                            stage_str = str(stage_value)
                        progress_value = status_snapshot.get('progress_percentage', 0)
                        message_value = status_snapshot.get('message') or 'Processing document...'
                        # Convert datetime to ISO format string for JSON serialization
                        updated_at = status_snapshot.get('updated_at')
                        if isinstance(updated_at, datetime):
                            timestamp_value = updated_at.isoformat()
                        else:
                            timestamp_value = updated_at or datetime.now().isoformat()

                        event_data = {
                            'stage': stage_str,
                            'progress': progress_value,
                            'message': message_value,
                            'timestamp': timestamp_value
                        }

                        yield f"data: {json.dumps(event_data)}\n\n"

                        last_status_snapshot = status_snapshot

                        current_stage = stage_value
                        if isinstance(current_stage, ProcessingStage):
                            current_stage_value = current_stage
                        else:
                            try:
                                current_stage_value = ProcessingStage(stage_str)
                            except Exception:
                                current_stage_value = ProcessingStage.PENDING

                        # Exit if completed or failed
                        if current_stage_value in [ProcessingStage.COMPLETED, ProcessingStage.FAILED]:
                            final_message = (
                                'Processing completed successfully'
                                if current_stage_value == ProcessingStage.COMPLETED
                                else f"Processing failed: {message_value}"
                            )
                            final_data = {
                                'stage': current_stage_value.value,
                                'progress': 100 if current_stage_value == ProcessingStage.COMPLETED else progress_value,
                                'message': final_message,
                                'timestamp': timestamp_value,
                                'final': True
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

                await asyncio.sleep(1)  # Poll every second
                retry_count += 1

            # If we've exceeded max retries, send timeout message
            if retry_count >= max_retries:
                timeout_data = {
                    'stage': 'timeout',
                    'progress': 0,
                    'message': 'Progress monitoring timed out after 5 minutes',
                    'timestamp': datetime.now().isoformat(),
                    'final': True
                }
                yield f"data: {json.dumps(timeout_data)}\n\n"

        except Exception as e:
            logger.error(f"Error in SSE stream for document {document_id}: {e}")
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

    Returns availability and sizes of PDF, Docling JSON, and processed JSON files.

    Requirements:
        - FR-014: Verify document ownership before serving download info
        - Return 403 Forbidden for cross-user access attempts
    """
    try:
        from ..config import get_pdf_storage_path

        # Get document from database to check file paths
        session = SessionLocal()
        try:
            # T031: Get database user for ownership verification (FR-014)
            db_user = set_global_user_from_cognito(session, user)

            doc = session.query(ViewerPDFDocument).filter(
                ViewerPDFDocument.id == uuid.UUID(document_id)
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

            # Check Docling JSON file with path validation (T032)
            # Note: docling_json_path is stored relative to pdf_storage: {user_id}/docling_json/{doc_id}.json
            docling_json_available = False
            docling_json_size = None
            if doc.docling_json_path:
                # Construct full path from pdf_storage (paths are relative to pdf_storage root)
                docling_path = FilePath(pdf_storage) / doc.docling_json_path
                # Validate path stays within user's storage directory (pdf_storage/{user_id})
                docling_path = validate_user_file_path(docling_path, FilePath(pdf_storage), user["sub"])
                docling_json_available = docling_path.exists()
                docling_json_size = docling_path.stat().st_size if docling_json_available else None

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
                "docling_json_available": docling_json_available,
                "docling_json_size": docling_json_size,
                "processed_json_available": processed_json_available,
                "processed_json_size": processed_json_size,
                "filename": doc.filename
            }
        finally:
            session.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting download info for document {document_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get download info: {str(e)}"
        )


@router.get("/documents/{document_id}/download/{file_type}")
async def download_document_file(
    document_id: str = Path(..., description="Document ID"),
    file_type: str = Path(..., description="File type to download (pdf, docling_json, processed_json)"),
    user: Dict[str, Any] = get_auth_dependency()
):
    """
    Download a specific file associated with a document.

    Available file types:
    - pdf: Original PDF document
    - docling_json: Raw Docling extraction output
    - processed_json: Cleaned document ready for embedding

    Requirements:
        - FR-014: Verify document ownership before serving files
        - Return 403 Forbidden for cross-user access attempts
    """
    try:
        from ..config import get_pdf_storage_path

        if file_type not in ['pdf', 'docling_json', 'processed_json']:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type: {file_type}"
            )

        # Get document from database
        session = SessionLocal()
        try:
            # T031: Get database user for ownership verification (FR-014)
            db_user = set_global_user_from_cognito(session, user)

            doc = session.query(ViewerPDFDocument).filter(
                ViewerPDFDocument.id == uuid.UUID(document_id)
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

            elif file_type == 'docling_json':
                # Note: docling_json_path is stored relative to pdf_storage: {user_id}/docling_json/{doc_id}.json
                if doc.docling_json_path:
                    pdf_storage = get_pdf_storage_path()
                    # Construct full path from pdf_storage (paths are relative to pdf_storage root)
                    file_path = FilePath(pdf_storage) / doc.docling_json_path
                    # Validate path stays within user's storage directory (pdf_storage/{user_id})
                    file_path = validate_user_file_path(file_path, FilePath(pdf_storage), user["sub"])
                    filename = f"{doc.filename.rsplit('.', 1)[0]}_docling.json"
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
        logger.error(f"Error downloading {file_type} for document {document_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to download file: {str(e)}"
        )
