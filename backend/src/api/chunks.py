"""Chunks API endpoints for Weaviate Control Panel."""

from fastapi import APIRouter, HTTPException, Query, Path
from typing import Dict, Any
import asyncio
import logging
from uuid import UUID

from ..models.api_schemas import ChunkListResponse, PaginationInfo
from ..lib.document_files.readable import ReadableFilesError, ReadingScope, reading_scope, resolve_readable_files
from ..lib.http_errors import raise_sanitized_http_exception
from ..lib.weaviate_client.chunks import get_chunks
from .auth import get_auth_dependency

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/weaviate")


@router.get("/documents/{document_id}/chunks", response_model=ChunkListResponse)
async def get_document_chunks_endpoint(
    document_id: str = Path(..., description="Document ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    include_metadata: bool = Query(True, description="Include chunk metadata"),
    user: Dict[str, Any] = get_auth_dependency()  # T038: Inject authenticated user
):
    """
    Get all chunks for a specific document with pagination.

    Returns the chunks of the document's ready files, sorted by chunk index, with
    optional metadata inclusion. Pieces of files being added are not listed.
    Requires authentication - user can only access their own document chunks (FR-014).
    """
    try:
        # T038: Extract cognito_sub for tenant-scoped chunk retrieval
        user_id = user.get('sub') if user else None
        if not user_id:
            raise HTTPException(
                status_code=401,
                detail="User authentication required for chunk access (FR-011, FR-014)"
            )

        pagination = {
            "page": page,
            "page_size": page_size,
            "include_metadata": include_metadata
        }

        not_found = HTTPException(status_code=404, detail=f"No chunks found for document {document_id}")
        try:
            UUID(document_id)
        except ValueError:
            raise not_found
        try:
            with reading_scope(ReadingScope.ALL_READY_FILES):
                readable = await asyncio.to_thread(resolve_readable_files, document_id, user_id)
        except ReadableFilesError:
            raise not_found
        result = await get_chunks(document_id, pagination, user_id, readable=readable)

        if result is None or result.get("total", 0) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No chunks found for document {document_id}"
            )

        total_pages = (result["total"] + page_size - 1) // page_size

        return ChunkListResponse(
            chunks=result["chunks"],
            pagination=PaginationInfo(
                current_page=page,
                total_pages=total_pages,
                total_items=result["total"],
                page_size=page_size
            ),
            document_id=document_id
        )

    except HTTPException:
        raise
    except Exception as e:
        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to retrieve chunks",
            log_message=f"Error retrieving chunks for document {document_id}",
            exc=e,
        )
