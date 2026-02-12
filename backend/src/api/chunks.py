"""Chunks API endpoints for Weaviate Control Panel."""

from fastapi import APIRouter, HTTPException, Query, Path
from typing import Dict, Any, Optional
import logging

from ..models.api_schemas import ChunkListResponse, PaginationInfo
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

    Returns chunks sorted by chunk index with optional metadata inclusion.
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

        result = await get_chunks(document_id, pagination, user_id)

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
        logger.error('Error retrieving chunks for document %s: %s', document_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve chunks: {str(e)}"
        )