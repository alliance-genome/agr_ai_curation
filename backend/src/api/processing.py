"""Processing API endpoint for document re-embedding."""

from fastapi import APIRouter, HTTPException, Path, Body
from starlette.concurrency import run_in_threadpool
from typing import Optional
import logging
from typing import Dict, Any

from ..models.api_schemas import (
    OperationResult,
    ReembedRequest,
)
from ..lib.http_errors import raise_sanitized_http_exception
from ..models.document import ProcessingStatus
from ..lib.weaviate_client.documents import get_document, update_document_status, re_embed_document
from ..lib.pipeline.tracker import PipelineTracker
from ..models.pipeline import ProcessingStage
from .auth import get_auth_dependency
from ..lib.pdf_jobs import service as pdf_job_service
from ..services.processing_status_policy import (
    ACTIVE_PDF_JOB_STATUSES as _ACTIVE_PDF_JOB_STATUSES,
    ACTIVE_PROCESSING_STATUSES as _ACTIVE_PROCESSING_STATUSES,
    TERMINAL_PDF_JOB_STATUSES as _TERMINAL_PDF_JOB_STATUSES,
    is_pipeline_status_active as _is_pipeline_status_active,
    normalize_processing_status as _normalize_processing_status,
    stage_value as _stage_value,
)
from ..models.sql.database import SessionLocal
from ..services.user_service import principal_from_claims, provision_user

# Create a global tracker instance for the API
pipeline_tracker = PipelineTracker()

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/weaviate")


def _latest_job_for_user_document(document_id: str, auth_user: Dict[str, Any]):
    session = SessionLocal()
    try:
        db_user = provision_user(session, principal_from_claims(auth_user))
        try:
            return pdf_job_service.get_latest_job_for_document(
                document_id=document_id,
                user_id=db_user.id,
                reconcile_stale=True,
            )
        except (TypeError, ValueError):
            # Some contract tests use synthetic non-UUID ids (e.g., "doc-1").
            # Treat those as "no durable job row" instead of surfacing 500.
            return None
    finally:
        session.close()


@router.post("/documents/{document_id}/reembed", response_model=OperationResult)
async def reembed_document_endpoint(
    document_id: str = Path(..., description="Document ID"),
    request: Optional[ReembedRequest] = Body(default=None),
    user: Dict[str, Any] = get_auth_dependency()
):
    """
    Re-generate embeddings for all chunks of a document.

    This will use the current or provided embedding configuration to regenerate
    all chunk embeddings without re-chunking the document.

    Requires authentication (FR-009, FR-010, FR-011, FR-014).
    """
    user_id = user["sub"]
    try:
        document = await get_document(user_id, document_id)

        if not document:
            raise HTTPException(
                status_code=404,
                detail=f"Document with ID {document_id} not found"
            )

        doc_status = _normalize_processing_status(
            document["document"].get("processing_status")
            or document["document"].get("processingStatus")
        )

        latest_job = await run_in_threadpool(_latest_job_for_user_document, document_id, user)
        if latest_job and latest_job.status in _ACTIVE_PDF_JOB_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Document is currently being processed "
                    f"(job status: {latest_job.status}, stage: {latest_job.current_stage or 'pending'})"
                ),
            )
        if doc_status in _ACTIVE_PROCESSING_STATUSES:
            pipeline_status = await pipeline_tracker.get_pipeline_status(document_id)
            if _is_pipeline_status_active(pipeline_status):
                stage_label = _stage_value(getattr(pipeline_status, "current_stage", None))
                raise HTTPException(
                    status_code=409,
                    detail=f"Document is currently being processed (stage: {stage_label})"
                )

            if latest_job and latest_job.status in _TERMINAL_PDF_JOB_STATUSES:
                logger.warning(
                    "Allowing re-embed for stale document processing status=%s with terminal job status=%s",
                    doc_status,
                    latest_job.status,
                )
            else:
                stage_label = _stage_value(pipeline_status.current_stage) if pipeline_status else doc_status
                raise HTTPException(
                    status_code=409,
                    detail=f"Document is currently being processed (stage: {stage_label})"
                )

        if document.get("total_chunks", 0) == 0:
            raise HTTPException(
                status_code=400,
                detail="Document has no chunks to re-embed"
            )

        await update_document_status(document_id, user_id, ProcessingStatus.PROCESSING)

        embedding_config = request.embedding_config if request else None
        batch_size = request.batch_size if request else 10

        result = await re_embed_document(
            document_id,
            user_id,
            embedding_config=embedding_config,
            batch_size=batch_size
        )

        await pipeline_tracker.track_pipeline_progress(document_id, ProcessingStage.EMBEDDING)

        return OperationResult(
            success=True,
            message=f"Re-embedding initiated for {result['total_chunks']} chunks",
            operation="reembed_document",
            document_id=document_id
        )

    except HTTPException:
        raise
    except Exception as e:
        try:
            await update_document_status(document_id, user_id, ProcessingStatus.FAILED)
        except Exception:
            pass

        raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to initiate re-embedding",
            log_message=f"Error re-embedding document {document_id}",
            exc=e,
        )
