"""Batch processing background task implementation.

Processes documents sequentially using the selected flow, persisting
state after each document for crash recovery. Supports cancellation.

Architecture:
    BackgroundTasks runs synchronous functions in a thread pool.
    We use asyncio.run() to execute the async flow executor from
    within the synchronous task. Each document gets its own asyncio
    event loop iteration.

    Events from the flow executor are published to the BatchEventBroadcaster,
    which allows the SSE endpoint to stream them to the frontend in real-time.
"""
import asyncio
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from src.models.sql.database import SessionLocal
from src.models.sql.batch import Batch, BatchDocument, BatchStatus, BatchDocumentStatus
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.user import User
from src.models.sql.file_output import FileOutput
from .events import get_batch_broadcaster

logger = logging.getLogger(__name__)


def _validate_file_ownership(file_id: str, expected_curator_id: str) -> bool:
    """Validate that a file is owned by the expected user.

    This is a defense-in-depth check to prevent cross-user file leakage
    in case of event routing bugs or race conditions.

    RACE CONDITION FIX (2026-01-23, KANBAN-935):
    When multiple batch jobs run concurrently, events could potentially
    leak between batches. This guardrail ensures that even if a FILE_READY
    event from Batch A somehow reaches Batch B's processor, the ownership
    check will reject it.

    Args:
        file_id: UUID of the file to check
        expected_curator_id: Cognito sub of the user who should own the file

    Returns:
        True if file is owned by expected user, False otherwise
    """
    try:
        db = SessionLocal()
        try:
            file_record = db.query(FileOutput).filter(
                FileOutput.id == file_id
            ).first()

            if not file_record:
                logger.warning(
                    "File %s not found in database during ownership check",
                    file_id
                )
                return False

            if file_record.curator_id != expected_curator_id:
                logger.warning(
                    "File ownership mismatch: file %s owned by %s, expected %s "
                    "(possible race condition or event routing bug)",
                    file_id, file_record.curator_id, expected_curator_id
                )
                return False

            return True
        finally:
            db.close()
    except Exception as e:
        logger.error("Error validating file ownership: %s", e)
        # Fail open to avoid blocking legitimate files, but log the error
        return True


@contextmanager
def get_db_session():
    """Create a database session for background tasks."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def process_batch_task(batch_id: UUID) -> None:
    """Background task to process all documents in a batch.

    This function runs in a thread pool via FastAPI's BackgroundTasks.
    It creates its own database session to avoid threading issues.

    Args:
        batch_id: UUID of the batch to process
    """
    logger.info("Starting batch processing: batch_id=%s", batch_id)

    with get_db_session() as db:
        stmt = (
            select(Batch)
            .where(Batch.id == batch_id)
            .options(selectinload(Batch.documents))
        )
        batch = db.scalars(stmt).first()

        if not batch:
            logger.error("Batch not found: batch_id=%s", batch_id)
            return

        # Get the flow definition
        flow = db.query(CurationFlow).filter(CurationFlow.id == batch.flow_id).first()
        if not flow:
            logger.error("Flow not found: flow_id=%s, batch_id=%s", batch.flow_id, batch_id)
            batch.status = BatchStatus.CANCELLED
            db.commit()
            return

        # Get the user's auth_sub (Cognito subject) for flow execution
        user = db.query(User).filter(User.id == batch.user_id).first()
        if not user or not user.auth_sub:
            logger.error("User not found or missing auth_sub: user_id=%s, batch_id=%s", batch.user_id, batch_id)
            batch.status = BatchStatus.CANCELLED
            db.commit()
            return
        cognito_sub = user.auth_sub

        # Mark batch as running
        batch.status = BatchStatus.RUNNING
        batch.started_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Batch marked as running: batch_id=%s, flow=%s", batch_id, flow.name)

        # Process each document
        for batch_doc in batch.documents:
            # Check for cancellation before each document
            db.refresh(batch)
            if batch.status == BatchStatus.CANCELLED:
                logger.info("Batch cancelled, stopping: batch_id=%s", batch_id)
                break

            try:
                _process_single_document(db, batch, batch_doc, flow, cognito_sub)
            except Exception as e:
                # CR-3: Explicit rollback before updating failure status
                # If _process_single_document made partial changes, rollback ensures clean state
                db.rollback()
                logger.exception(
                    "Error processing document: batch_id=%s, doc_id=%s",
                    batch_id, batch_doc.document_id
                )
                # Re-fetch objects after rollback to ensure they're in session
                batch = db.query(Batch).filter(Batch.id == batch_id).first()
                batch_doc = db.query(BatchDocument).filter(BatchDocument.id == batch_doc.id).first()
                if batch_doc and batch:
                    batch_doc.status = BatchDocumentStatus.FAILED
                    batch_doc.error_message = str(e)[:500]  # Limit error message length
                    batch_doc.processed_at = datetime.now(timezone.utc)
                    batch.failed_documents += 1
                    db.commit()

        # Mark batch as completed (unless cancelled)
        db.refresh(batch)
        if batch.status != BatchStatus.CANCELLED:
            batch.status = BatchStatus.COMPLETED
            batch.completed_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(
                "Batch completed: batch_id=%s, completed=%d, failed=%d",
                batch_id, batch.completed_documents, batch.failed_documents
            )


def _process_single_document(
    db: Session,
    batch: Batch,
    batch_doc: BatchDocument,
    flow: CurationFlow,
    cognito_sub: str,
) -> None:
    """Process a single document in the batch.

    Args:
        db: Database session
        batch: Parent Batch object
        batch_doc: BatchDocument to process
        flow: CurationFlow to execute
        cognito_sub: Cognito subject (auth_sub) for the user
    """
    logger.info(
        "Processing document: batch_id=%s, doc_id=%s, position=%d/%d",
        batch.id, batch_doc.document_id, batch_doc.position + 1, batch.total_documents
    )

    # Mark as processing
    batch_doc.status = BatchDocumentStatus.PROCESSING
    db.commit()

    start_time = time.time()

    try:
        # Execute the flow on this document
        # Use asyncio.run() since we're in a sync context
        result_file_path = asyncio.run(
            _execute_flow_for_document(
                flow=flow,
                document_id=str(batch_doc.document_id),
                cognito_sub=cognito_sub,
                batch_id=str(batch.id),
            )
        )

        # Calculate processing time
        processing_time_ms = int((time.time() - start_time) * 1000)

        # Mark as completed
        batch_doc.status = BatchDocumentStatus.COMPLETED
        batch_doc.result_file_path = result_file_path
        batch_doc.processing_time_ms = processing_time_ms
        batch_doc.processed_at = datetime.now(timezone.utc)
        batch.completed_documents += 1
        db.commit()

        logger.info(
            "Document completed: batch_id=%s, doc_id=%s, time_ms=%d, result=%s",
            batch.id, batch_doc.document_id, processing_time_ms, result_file_path
        )

    except Exception as e:
        processing_time_ms = int((time.time() - start_time) * 1000)
        batch_doc.status = BatchDocumentStatus.FAILED
        batch_doc.error_message = str(e)[:500]
        batch_doc.processing_time_ms = processing_time_ms
        batch_doc.processed_at = datetime.now(timezone.utc)
        batch.failed_documents += 1
        db.commit()
        raise


async def _execute_flow_for_document(
    flow: CurationFlow,
    document_id: str,
    cognito_sub: str,
    batch_id: str,
) -> Optional[str]:
    """Execute a flow on a single document and extract the result file path.

    All events from the flow execution are published to the BatchEventBroadcaster
    so they can be streamed to the frontend via SSE.

    Args:
        flow: The curation flow to execute
        document_id: Weaviate document UUID
        cognito_sub: Cognito subject (auth_sub) for Weaviate tenant and file output
        batch_id: Batch UUID for session tracking

    Returns:
        File path/ID of the generated result file, or None if no file output
    """
    from src.lib.flows.executor import execute_flow
    from src.lib.context import set_current_user_id, set_current_session_id

    # Get the broadcaster for publishing events
    broadcaster = get_batch_broadcaster()
    batch_uuid = UUID(batch_id)

    # Generate unique session ID for this document execution
    session_id = f"batch-{batch_id}-doc-{document_id[:8]}"

    # Set context variables for file output tools (expects Cognito subject)
    set_current_user_id(cognito_sub)
    set_current_session_id(session_id)

    logger.debug(
        "Executing flow '%s' on document %s, session=%s, user=%s",
        flow.name, document_id, session_id, cognito_sub
    )

    result_file_path = None

    try:
        # Execute the flow and collect events
        async for event in execute_flow(
            flow=flow,
            user_id=cognito_sub,  # Cognito subject ID for Weaviate tenant
            session_id=session_id,
            document_id=document_id,
            document_name=None,  # Will be fetched by DocumentContext
            user_query=None,  # Use task_instructions from flow
            active_groups=None,  # Default groups
        ):
            event_type = event.get("type", "")

            # Enrich event with batch context for the frontend
            enriched_event = _enrich_event_for_batch(event, batch_id, document_id, session_id)

            # Publish ALL events to the broadcaster for SSE streaming
            broadcaster.publish_sync(batch_uuid, enriched_event)

            # Look for file output in FILE_READY events
            # The runner emits FILE_READY when file output tools produce FileInfo
            if event_type == "FILE_READY":
                download_url = event.get("details", {}).get("download_url")
                file_id = event.get("details", {}).get("file_id")

                if download_url and file_id:
                    # GUARDRAIL: Validate file ownership before capturing
                    # This prevents cross-user file leakage even if event routing has bugs
                    # (defense-in-depth for KANBAN-935 race condition fix)
                    if _validate_file_ownership(file_id, cognito_sub):
                        result_file_path = download_url
                        logger.info(
                            "Found file output in flow: %s (filename: %s)",
                            result_file_path,
                            event.get("details", {}).get("filename")
                        )
                    else:
                        logger.warning(
                            "FILE_READY event ignored - file %s not owned by user %s "
                            "(possible race condition or event routing bug)",
                            file_id, cognito_sub
                        )
                elif download_url:
                    # Fallback for events without file_id (shouldn't happen, but be safe)
                    logger.warning(
                        "FILE_READY event missing file_id, capturing without ownership check: %s",
                        download_url
                    )
                    result_file_path = download_url

            # Log supervisor completion for debugging
            elif event_type == "SUPERVISOR_COMPLETE":
                logger.debug(
                    "Flow supervisor complete for document %s",
                    document_id
                )

    except Exception as e:
        logger.error(
            "Flow execution failed for document %s: %s",
            document_id, str(e)
        )
        # Publish error event before re-raising
        error_event = {
            "type": "BATCH_DOCUMENT_ERROR",
            "batch_id": batch_id,
            "document_id": document_id,
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {"error": str(e)},
        }
        broadcaster.publish_sync(batch_uuid, error_event)
        raise

    if not result_file_path:
        logger.warning(
            "No file output found from flow '%s' for document %s",
            flow.name, document_id
        )

    return result_file_path


def _enrich_event_for_batch(
    event: Dict[str, Any],
    batch_id: str,
    document_id: str,
    session_id: str,
) -> Dict[str, Any]:
    """Enrich an event with batch context for the frontend.

    The frontend audit panel expects certain fields to be present.
    This function adds batch-specific context while preserving
    all original event data.

    Args:
        event: Original event from flow executor
        batch_id: Batch UUID string
        document_id: Document UUID string
        session_id: Session ID string

    Returns:
        Enriched event dict
    """
    # Start with a copy of the original event
    enriched = dict(event)

    # Add batch context
    enriched["batch_id"] = batch_id
    enriched["document_id"] = document_id
    enriched["session_id"] = session_id
    enriched["sessionId"] = session_id  # Frontend expects both formats

    # Flatten 'data' field if present (matches chat.py pattern)
    if "data" in event:
        for key, value in event["data"].items():
            if key not in enriched:
                enriched[key] = value

    return enriched
