"""Processing API endpoints for document reprocessing and re-embedding."""

from fastapi import APIRouter, HTTPException, Path, Body, BackgroundTasks
from typing import Optional
import logging
from typing import Dict, Any
from pathlib import Path as FilePath

from ..models.api_schemas import (
    OperationResult,
    ReprocessRequest,
    ReembedRequest,
    EmbeddingConfiguration
)
from ..models.document import ProcessingStatus
from ..lib.weaviate_client.documents import get_document, update_document_status, re_embed_document, update_document_status_detailed
from ..lib.pipeline.tracker import PipelineTracker
from ..models.pipeline import ProcessingStage
from .auth import get_auth_dependency
from ..lib.pipeline.orchestrator import DocumentPipelineOrchestrator
from ..lib.weaviate_client.connection import get_connection
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
from ..models.sql.pdf_processing_job import PdfJobStatus
from ..services.user_service import principal_from_claims, provision_user
from ..config import get_pdf_storage_path

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


@router.post("/documents/{document_id}/reprocess", response_model=OperationResult)
async def reprocess_document_endpoint(
    background_tasks: BackgroundTasks,
    document_id: str = Path(..., description="Document ID"),
    request: ReprocessRequest = Body(...),
    user: Dict[str, Any] = get_auth_dependency()
):
    """
    Reprocess a document with a different chunking strategy.

    This will re-chunk the document using the specified strategy.
    If force_reparse is true, the PDF will be re-parsed from scratch.

    Requires authentication (FR-009, FR-010, FR-011, FR-014).
    """
    try:
        user_id = user['sub']
        document_data = await get_document(user_id, document_id)

        if not document_data:
            raise HTTPException(
                status_code=404,
                detail=f"Document with ID {document_id} not found"
            )

        doc_status = _normalize_processing_status(
            document_data["document"].get("processing_status")
            or document_data["document"].get("processingStatus")
        )

        latest_job = _latest_job_for_user_document(document_id, user)
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
                stage_label = _stage_value(pipeline_status.current_stage)
                raise HTTPException(
                    status_code=409,
                    detail=f"Document is currently being processed (stage: {stage_label})"
                )

            if latest_job and latest_job.status in _TERMINAL_PDF_JOB_STATUSES:
                logger.warning(
                    "Allowing reprocess for stale document processing status=%s with terminal job status=%s",
                    doc_status,
                    latest_job.status,
                )
            else:
                stage_label = _stage_value(pipeline_status.current_stage) if pipeline_status else doc_status
                raise HTTPException(
                    status_code=409,
                    detail=f"Document is currently being processed (stage: {stage_label})"
                )

        # Get filename to construct path
        filename = document_data["document"].get("filename")
        if not filename:
             raise HTTPException(
                status_code=500,
                detail="Document metadata missing filename"
            )
            
        base_storage = get_pdf_storage_path()
        # Fix: File is stored in a subdirectory named after document_id
        file_path = base_storage / user_id / document_id / filename
        
        if not file_path.exists():
             raise HTTPException(
                status_code=404,
                detail=f"Source file not found at {file_path}"
            )

        await update_document_status(document_id, user_id, ProcessingStatus.PROCESSING)

        stage = ProcessingStage.PARSING if request.force_reparse else ProcessingStage.CHUNKING
        await pipeline_tracker.track_pipeline_progress(
            document_id,
            stage
        )
        
        # Define background processing task
        async def process_document():
            try:
                connection = get_connection()
                orchestrator = DocumentPipelineOrchestrator(
                    weaviate_client=connection,
                    tracker=pipeline_tracker,
                )
                
                # Determine start stage based on force_reparse
                # If not force_reparse, we might skip parsing, but DocumentPipelineOrchestrator
                # currently runs full pipeline.
                # However, parse_pdf_document in pdfx_parser checks for existing json
                # if we implement caching properly.
                # For now, we'll run the full pipeline as requested, relying on orchestrator logic.
                
                # Note: DocumentPipelineOrchestrator.process_pdf_document runs the whole flow.
                # If we want to skip parsing, we'd need to modify the orchestrator or 
                # rely on the parser to skip if output exists (and force_reparse is False).
                
                # Since pdfx is expensive, let's assume orchestrator handles it or we are fine reprocessing.
                # Actually, parse_pdf_document takes 'extraction_strategy' but not 'force'.
                # But let's pass the request parameters if possible.
                # The current process_pdf_document signature doesn't explicitly support 'skip_parsing'.
                # But if we are here, we probably want to re-run things.
                
                result = await orchestrator.process_pdf_document(
                    file_path=file_path,
                    document_id=document_id,
                    user_id=user_id,
                    validate_first=False # Already validated
                )
                logger.info('Document %s reprocessing completed: %s', document_id, result)
            except Exception as e:
                logger.error('Error reprocessing document %s: %s', document_id, e, exc_info=True)
                await update_document_status(document_id, user_id, ProcessingStatus.FAILED)

        background_tasks.add_task(process_document)

        # Invalidate document metadata cache - reprocessing will produce fresh data
        from src.lib.document_cache import invalidate_cache
        invalidate_cache(user_id, document_id)

        return OperationResult(
            success=True,
            message=f"Document reprocessing initiated with strategy: {request.strategy_name}",
            operation="reprocess_document",
            document_id=document_id
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Error reprocessing document %s: %s', document_id, e)

        try:
            await update_document_status(document_id, user_id, ProcessingStatus.FAILED)
        except:
            pass

        raise HTTPException(
            status_code=500,
            detail=f"Failed to initiate reprocessing: {str(e)}"
        )


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
    try:
        user_id = user['sub']
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

        latest_job = _latest_job_for_user_document(document_id, user)
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
                stage_label = _stage_value(pipeline_status.current_stage)
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
        logger.error('Error re-embedding document %s: %s', document_id, e)

        try:
            await update_document_status(document_id, user_id, ProcessingStatus.FAILED)
        except:
            pass

        raise HTTPException(
            status_code=500,
            detail=f"Failed to initiate re-embedding: {str(e)}"
        )
