"""Batch processing API endpoints.

Enables curators to run saved flows on multiple documents sequentially.
Provides batch creation, status tracking, streaming progress, and cancellation.

All endpoints require Cognito JWT authentication via Security(get_auth_dependency()).
Batch ownership is enforced - users can only access their own batches.
"""

import asyncio
import json
import logging
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .auth import get_auth_dependency
from ..lib.batch.service import BatchService
from ..lib.batch.validation import validate_flow_for_batch
from ..lib.batch.processor import process_batch_task
from ..lib.batch.events import get_batch_broadcaster
from ..models.sql import get_db, CurationFlow, PDFDocument
from ..models.sql.batch import BatchStatus, BatchDocumentStatus
from ..models.sql.database import SessionLocal
from ..schemas.batch import (
    BatchCreateRequest,
    BatchResponse,
    BatchListResponse,
    BatchValidationResponse,
)
from ..services.user_service import set_global_user_from_cognito


logger = logging.getLogger(__name__)

# Main batch router - handles /api/batches endpoints
router = APIRouter(prefix="/api/batches")

# Secondary router for flow validation - handles /api/flows/{id}/validate-batch
flow_validation_router = APIRouter(prefix="/api/flows")


@router.post("", response_model=BatchResponse, status_code=201)
async def create_batch(
    request: BatchCreateRequest,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> BatchResponse:
    """Create and start a new batch processing job.

    Validates the flow exists and is batch-compatible, creates batch records,
    and begins sequential document processing in the background.

    Args:
        request: Batch creation request with flow_id and document_ids
        background_tasks: FastAPI background tasks for async processing
        user: Authenticated user from Cognito JWT
        db: Database session

    Returns:
        Created batch with initial pending status

    Raises:
        404: If flow not found or not owned by user
        400: If flow is not batch-compatible
    """
    db_user = set_global_user_from_cognito(db, user)

    # Verify flow exists and user owns it
    flow = db.query(CurationFlow).filter(
        CurationFlow.id == request.flow_id,
        CurationFlow.user_id == db_user.id,
        CurationFlow.is_active == True,  # noqa: E712
    ).first()

    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    # Validate flow is batch-compatible
    validation = validate_flow_for_batch(flow.flow_definition)
    if not validation.valid:
        raise HTTPException(
            status_code=400,
            detail=f"Flow not compatible with batch processing: {', '.join(validation.errors)}"
        )

    # Validate document IDs exist and belong to the user
    if not request.document_ids:
        raise HTTPException(
            status_code=400,
            detail="At least one document ID is required"
        )

    # Query for all requested documents owned by the user
    found_docs = db.query(PDFDocument).filter(
        PDFDocument.id.in_(request.document_ids),
        PDFDocument.user_id == db_user.id,
    ).all()

    found_ids = {doc.id for doc in found_docs}
    missing_ids = [str(doc_id) for doc_id in request.document_ids if doc_id not in found_ids]

    if missing_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Documents not found or not accessible: {', '.join(missing_ids[:5])}"
            + (f" and {len(missing_ids) - 5} more" if len(missing_ids) > 5 else "")
        )

    # Create batch and batch_document records
    service = BatchService(db)
    batch = service.create_batch(
        user_id=db_user.id,
        flow_id=request.flow_id,
        document_ids=request.document_ids,
    )

    # Start background processing
    logger.info("Starting background task for batch_id=%s", batch.id)
    background_tasks.add_task(process_batch_task, batch.id)

    # Return created batch (flow name already known from validation)
    return service.batch_to_response(batch, flow_name=flow.name)


@router.get("/running-count")
async def get_running_batch_count(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> Dict[str, int]:
    """Get count of running/pending batches and documents for the current user.

    Lightweight endpoint for nav badge polling.
    Returns:
        - running_count: Number of batches in PENDING or RUNNING state
        - pending_documents: Number of documents remaining to process across all running batches
    """
    db_user = set_global_user_from_cognito(db, user)
    service = BatchService(db)
    return {
        "running_count": service.count_running_batches(db_user.id),
        "pending_documents": service.get_pending_documents_count(db_user.id),
    }


@router.get("", response_model=BatchListResponse)
async def list_batches(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> BatchListResponse:
    """List user's batches.

    Returns batches ordered by created_at descending (most recent first).
    """
    db_user = set_global_user_from_cognito(db, user)
    service = BatchService(db)
    batches = service.list_batches(db_user.id)

    # Convert to response models (flow names will be looked up)
    batch_responses = [service.batch_to_response(batch) for batch in batches]

    return BatchListResponse(batches=batch_responses, total=len(batch_responses))


@router.get("/{batch_id}", response_model=BatchResponse)
async def get_batch(
    batch_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> BatchResponse:
    """Get batch details by ID.

    Returns full batch information including per-document status.
    """
    db_user = set_global_user_from_cognito(db, user)
    service = BatchService(db)
    batch = service.get_batch(batch_id, db_user.id)

    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    return service.batch_to_response(batch)


@router.delete("/{batch_id}")
async def delete_batch(
    batch_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    """Delete a batch and its associated documents.

    FUTURE FEATURE: This endpoint is a placeholder for batch deletion functionality.
    When implemented, it should:
    - Verify batch belongs to the requesting user
    - Only allow deletion of completed or cancelled batches
    - Delete associated batch_documents records
    - Delete result files from storage (file_outputs/)
    - Delete the batch record itself
    - Handle cleanup of any related Weaviate documents if applicable

    Args:
        batch_id: UUID of the batch to delete

    Returns:
        Success message on deletion

    Raises:
        HTTPException 501: This feature is not yet implemented
        HTTPException 404: Batch not found (when implemented)
        HTTPException 400: Batch cannot be deleted (in progress)
    """
    raise HTTPException(
        status_code=501,
        detail="Batch deletion not yet implemented. Batches cannot currently be removed."
    )


@router.get("/{batch_id}/download-zip")
async def download_batch_zip(
    batch_id: UUID,
    request: Request,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Download all completed batch results as a ZIP file.

    Creates a ZIP archive containing all result files from completed
    documents in the batch. Only includes documents with status=completed
    and a valid result_file_path.

    Reads files directly from disk to avoid authentication issues with
    internal HTTP requests (browser uses cookies, not Authorization headers).

    Args:
        batch_id: UUID of the batch to download
        user: Authenticated user from Cognito JWT
        db: Database session

    Returns:
        StreamingResponse with ZIP file

    Raises:
        404: If batch not found or not owned by user
        400: If batch has no completed documents
    """
    import io
    import re
    import zipfile
    from pathlib import Path
    from ..models.sql.file_output import FileOutput

    db_user = set_global_user_from_cognito(db, user)
    service = BatchService(db)

    batch = service.get_batch(batch_id, db_user.id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    # Get completed documents with result files
    completed_docs = [
        doc for doc in batch.documents
        if doc.status == BatchDocumentStatus.COMPLETED and doc.result_file_path
    ]

    if not completed_docs:
        raise HTTPException(
            status_code=400,
            detail="No completed documents with results to download"
        )

    # Create ZIP in memory
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for doc in completed_docs:
            try:
                # Extract file_id from result_file_path URL
                # Format: /api/files/{file_id}/download
                match = re.search(r'/api/files/([a-f0-9-]+)/download', doc.result_file_path)
                if not match:
                    logger.warning(
                        "Could not extract file_id from result_file_path: doc_id=%s, path=%s",
                        doc.document_id, doc.result_file_path
                    )
                    continue

                file_id = match.group(1)

                # Look up the FileOutput record to get the actual file path
                file_output = db.query(FileOutput).filter(
                    FileOutput.id == file_id
                ).first()

                if not file_output:
                    logger.warning(
                        "FileOutput not found: doc_id=%s, file_id=%s",
                        doc.document_id, file_id
                    )
                    continue

                # Verify the file belongs to this user (security check)
                curator_id = user.get("sub") or user.get("uid", "unknown")
                if file_output.curator_id != curator_id:
                    logger.warning(
                        "File ownership mismatch: doc_id=%s, file_id=%s, "
                        "expected_curator=%s, actual_curator=%s",
                        doc.document_id, file_id, curator_id, file_output.curator_id
                    )
                    continue

                # Read file directly from disk with path traversal protection
                from ..lib.file_outputs.storage import FileOutputStorageService
                storage = FileOutputStorageService()
                file_path = Path(file_output.file_path)

                # Validate path is within storage directory (security check)
                try:
                    resolved_path = file_path.resolve()
                    base_path = storage.base_path.resolve()
                    if not resolved_path.is_relative_to(base_path):
                        logger.warning(
                            "Path traversal attempt in batch ZIP: doc_id=%s, path=%s",
                            doc.document_id, file_output.file_path
                        )
                        continue
                except (ValueError, Exception) as e:
                    logger.warning(
                        "Invalid file path in batch ZIP: doc_id=%s, path=%s, error=%s",
                        doc.document_id, file_output.file_path, str(e)
                    )
                    continue

                if not file_path.exists():
                    logger.warning(
                        "File not found on disk: doc_id=%s, path=%s",
                        doc.document_id, file_output.file_path
                    )
                    continue

                # Read file content
                file_content = file_path.read_bytes()

                # Use original filename with position prefix
                filename = f"{doc.position + 1:03d}_{file_output.filename}"

                zip_file.writestr(filename, file_content)
                logger.info("Added to ZIP: %s (%d bytes)", filename, len(file_content))

            except Exception as e:
                logger.warning(
                    "Error reading result file: doc_id=%s, error=%s",
                    doc.document_id, str(e)
                )

    zip_buffer.seek(0)

    # Generate filename with batch info
    filename = f"batch_{batch_id}_results.zip"

    return StreamingResponse(
        iter([zip_buffer.getvalue()]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
    )


@router.get("/{batch_id}/stream")
async def stream_batch_progress(
    batch_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Stream batch processing progress via Server-Sent Events.

    Streams real-time audit events from the batch processor, plus
    document/batch status updates from database polling.

    Event types (from processor):
    - All flow execution events (TOOL_COMPLETE, SPECIALIST_SUMMARY, etc.)
    - BATCH_DOCUMENT_ERROR: Document processing error

    Event types (from database polling):
    - BATCH_STATUS: Overall batch status update
    - DOCUMENT_STATUS: Individual document status change
    - BATCH_COMPLETE: Batch finished (completed or cancelled)
    - ERROR: Stream error

    Args:
        batch_id: UUID of the batch to stream
        user: Authenticated user from Cognito JWT
        db: Database session

    Returns:
        StreamingResponse with SSE events
    """
    db_user = set_global_user_from_cognito(db, user)
    user_id = db_user.id  # Capture user_id for use in generator
    service = BatchService(db)

    # Verify batch exists and user owns it (using request session)
    batch = service.get_batch(batch_id, user_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    async def generate_stream():
        """Generate SSE events from broadcaster and database polling.

        Combines real-time audit events from the batch processor with
        database polling for status changes.
        """
        # Get broadcaster and subscribe to events
        broadcaster = get_batch_broadcaster()
        event_queue = await broadcaster.subscribe(batch_id)

        # Create a new session for the generator to avoid request session issues
        stream_db = SessionLocal()
        try:
            stream_service = BatchService(stream_db)

            # Track last known state to detect changes
            last_completed = -1
            last_failed = -1
            last_doc_statuses = {}

            # Send initial batch status
            batch = stream_service.get_batch(batch_id, user_id)
            if batch:
                event = {
                    "type": "BATCH_STATUS",
                    "batch_id": str(batch.id),
                    "status": batch.status.value,
                    "total_documents": batch.total_documents,
                    "completed_documents": batch.completed_documents,
                    "failed_documents": batch.failed_documents,
                }
                yield f"data: {json.dumps(event)}\n\n"
                last_completed = batch.completed_documents
                last_failed = batch.failed_documents
                last_doc_statuses = {str(d.id): d.status.value for d in batch.documents}

            batch_complete = False

            # Main event loop: check broadcaster queue and poll DB
            while not batch_complete:
                # First, drain all events from the broadcaster queue (non-blocking)
                while True:
                    try:
                        event = event_queue.get_nowait()
                        # Stream event directly to frontend (already enriched)
                        yield f"data: {json.dumps(event, default=str)}\n\n"

                        # Check for stream completion marker
                        if event.get("type") == "BATCH_STREAM_COMPLETE":
                            batch_complete = True
                            break
                    except asyncio.QueueEmpty:
                        break

                if batch_complete:
                    break

                # Small sleep to avoid busy-waiting
                await asyncio.sleep(0.1)

                # Poll database for status changes (every ~1 second worth of iterations)
                # This catches status changes even if events are missed
                stream_db.expire_all()  # Clear SQLAlchemy cache
                batch = stream_service.get_batch(batch_id, user_id)

                if not batch:
                    error_event = {
                        "type": "ERROR",
                        "message": "Batch not found"
                    }
                    yield f"data: {json.dumps(error_event)}\n\n"
                    break

                # Check for document status changes
                for doc in batch.documents:
                    doc_id = str(doc.id)
                    current_status = doc.status.value

                    if doc_id not in last_doc_statuses or last_doc_statuses[doc_id] != current_status:
                        doc_event = {
                            "type": "DOCUMENT_STATUS",
                            "batch_id": str(batch.id),
                            "document_id": str(doc.document_id),
                            "batch_document_id": doc_id,
                            "position": doc.position,
                            "status": current_status,
                            "result_file_path": doc.result_file_path,
                            "error_message": doc.error_message,
                            "processing_time_ms": doc.processing_time_ms,
                        }
                        yield f"data: {json.dumps(doc_event)}\n\n"
                        last_doc_statuses[doc_id] = current_status

                # Check for overall progress changes
                if batch.completed_documents != last_completed or batch.failed_documents != last_failed:
                    status_event = {
                        "type": "BATCH_STATUS",
                        "batch_id": str(batch.id),
                        "status": batch.status.value,
                        "total_documents": batch.total_documents,
                        "completed_documents": batch.completed_documents,
                        "failed_documents": batch.failed_documents,
                    }
                    yield f"data: {json.dumps(status_event)}\n\n"
                    last_completed = batch.completed_documents
                    last_failed = batch.failed_documents

                # Check if batch is complete
                if batch.status in (BatchStatus.COMPLETED, BatchStatus.CANCELLED):
                    complete_event = {
                        "type": "BATCH_COMPLETE",
                        "batch_id": str(batch.id),
                        "status": batch.status.value,
                        "total_documents": batch.total_documents,
                        "completed_documents": batch.completed_documents,
                        "failed_documents": batch.failed_documents,
                        "started_at": batch.started_at.isoformat() if batch.started_at else None,
                        "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
                    }
                    yield f"data: {json.dumps(complete_event)}\n\n"
                    batch_complete = True

        except Exception as e:
            logger.exception("Error streaming batch progress: batch_id=%s", batch_id)
            error_event = {
                "type": "ERROR",
                "message": str(e)
            }
            yield f"data: {json.dumps(error_event)}\n\n"
        finally:
            # Cleanup: unsubscribe and close session
            await broadcaster.unsubscribe(batch_id, event_queue)
            stream_db.close()

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/{batch_id}/cancel", response_model=BatchResponse)
async def cancel_batch(
    batch_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> BatchResponse:
    """Cancel a running batch.

    Stops processing after the current document completes.
    Already-completed documents retain their results.

    Args:
        batch_id: UUID of the batch to cancel
        user: Authenticated user from Cognito JWT
        db: Database session

    Returns:
        Updated batch with CANCELLED status

    Raises:
        404: If batch not found or not owned by user
        400: If batch is not in a cancellable state
    """
    db_user = set_global_user_from_cognito(db, user)
    service = BatchService(db)

    try:
        batch = service.cancel_batch(batch_id, db_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    logger.info("Batch cancelled via API: batch_id=%s, user_id=%s", batch_id, db_user.id)

    return service.batch_to_response(batch)


@flow_validation_router.get("/{flow_id}/validate-batch", response_model=BatchValidationResponse)
async def validate_flow_for_batch_endpoint(
    flow_id: UUID,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> BatchValidationResponse:
    """Validate whether a flow is compatible with batch processing.

    Checks that the flow:
    - Exists and is owned by the user
    - Contains only batch-compatible agents/tools
    - Has a valid structure for sequential execution

    Returns validation result with any incompatibility errors.
    """
    db_user = set_global_user_from_cognito(db, user)

    # Get flow from database
    flow = db.query(CurationFlow).filter(
        CurationFlow.id == flow_id,
        CurationFlow.user_id == db_user.id,
        CurationFlow.is_active == True,  # noqa: E712
    ).first()

    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    return validate_flow_for_batch(flow.flow_definition)
