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
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from src.models.sql.database import SessionLocal
from src.models.sql.batch import Batch, BatchDocument, BatchStatus, BatchDocumentStatus
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.user import User
from src.models.sql.file_output import FileOutput
from src.lib.observability.background_tasks import report_background_task_exception
from src.lib.openai_agents.config import (
    get_batch_worker_heartbeat_seconds,
    get_batch_worker_lease_seconds,
)
from .events import get_batch_broadcaster
from .service import BatchService
from .status import require_batch_document_status_transition

logger = logging.getLogger(__name__)
_BACKEND_ONLY_EVENT_FIELDS = {"internal"}


class BatchFlowExecutionError(RuntimeError):
    """Raised when a flow emits a terminal failure during batch processing."""

    def __init__(self, message: str, *, sentry_already_reported: bool = False) -> None:
        super().__init__(message)
        self.sentry_already_reported = sentry_already_reported


class BatchCancelled(RuntimeError):
    """Raised internally when persisted cancellation wins a checkpoint."""


def _require_running_batch(
    db: Session,
    batch: Batch,
    *,
    lock_for_update: bool = False,
    lease_owner: Optional[UUID] = None,
) -> None:
    """Refresh batch state and stop before the next processing side effect.

    Write checkpoints lock the batch row until their immediate commit so a
    concurrent cancellation cannot interleave between the check and counters.
    """
    db.refresh(batch, with_for_update=lock_for_update)
    if batch.status != BatchStatus.RUNNING:
        raise BatchCancelled(f"Batch {batch.id} is no longer running")
    if lease_owner is not None:
        now = datetime.now(timezone.utc)
        if batch.lease_owner != lease_owner or (
            batch.lease_expires_at is None or batch.lease_expires_at <= now
        ):
            raise BatchCancelled(f"Batch {batch.id} worker lease is no longer owned")


@contextmanager
def _maintain_batch_lease(batch_id: UUID, lease_owner: UUID):
    """Heartbeat a durable worker lease while document flow work is running."""
    stopped = threading.Event()
    lease_seconds = get_batch_worker_lease_seconds()
    heartbeat_seconds = get_batch_worker_heartbeat_seconds()

    def heartbeat() -> None:
        while not stopped.wait(heartbeat_seconds):
            try:
                with get_db_session() as heartbeat_db:
                    if not BatchService(heartbeat_db).heartbeat_batch_lease(
                        batch_id,
                        lease_owner,
                        lease_seconds,
                    ):
                        logger.info(
                            "Batch lease heartbeat lost ownership: batch_id=%s", batch_id
                        )
                        return
            except Exception:
                logger.warning(
                    "Batch lease heartbeat failed; renewal will retry on the next interval: "
                    "batch_id=%s",
                    batch_id,
                    exc_info=True,
                )

    thread = threading.Thread(
        target=heartbeat,
        name=f"batch-lease-{batch_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()


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
        # Fail closed - do not trust file ownership when validation cannot run.
        return False


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
        service = BatchService(db)
        lease_owner = uuid4()
        batch = service.claim_recoverable_batch(
            batch_id,
            lease_owner,
            get_batch_worker_lease_seconds(),
        )
        if not batch:
            logger.info(
                "Batch claim skipped because it is not recoverable or another worker owns it: batch_id=%s",
                batch_id,
            )
            return

        with _maintain_batch_lease(batch_id, lease_owner):
            _process_claimed_batch(db, service, batch, lease_owner)


def _process_claimed_batch(
    db: Session,
    service: BatchService,
    batch: Batch,
    lease_owner: UUID,
) -> None:
    """Process only pending documents under an already-acquired durable lease."""
    batch_id = batch.id
    flow = db.query(CurationFlow).filter(CurationFlow.id == batch.flow_id).first()
    if not flow:
        logger.error("Flow not found: flow_id=%s, batch_id=%s", batch.flow_id, batch_id)
        if not service.cancel_running_batch_for_lease(batch_id, lease_owner):
            logger.info(
                "Skipping missing-flow cancellation after lease loss: batch_id=%s",
                batch_id,
            )
        return

    user = db.query(User).filter(User.id == batch.user_id).first()
    if not user or not user.auth_sub:
        logger.error("User not found or missing auth_sub: user_id=%s, batch_id=%s", batch.user_id, batch_id)
        if not service.cancel_running_batch_for_lease(batch_id, lease_owner):
            logger.info(
                "Skipping missing-user cancellation after lease loss: batch_id=%s",
                batch_id,
            )
        return
    cognito_sub = user.auth_sub

    logger.info("Batch lease acquired: batch_id=%s, flow=%s", batch_id, flow.name)

    for batch_doc in batch.documents:
        if batch_doc.status != BatchDocumentStatus.PENDING:
            continue
        try:
            _require_running_batch(db, batch, lease_owner=lease_owner)
            _process_single_document(
                db, batch, batch_doc, flow, cognito_sub, lease_owner=lease_owner
            )
        except BatchCancelled:
            db.rollback()
            logger.info("Batch cancelled during document processing: batch_id=%s", batch_id)
            break
        except Exception as error:
            db.rollback()
            _report_document_failure(error, batch_id, batch_doc)
            batch = db.get(Batch, batch_id)
            batch_doc = db.get(BatchDocument, batch_doc.id)
            if not batch or not batch_doc:
                continue
            try:
                _require_running_batch(
                    db, batch, lock_for_update=True, lease_owner=lease_owner
                )
            except BatchCancelled:
                logger.info(
                    "Skipping document failure update after lease loss: batch_id=%s",
                    batch_id,
                )
                break
            if batch_doc.status != BatchDocumentStatus.FAILED:
                require_batch_document_status_transition(
                    batch_doc.status, BatchDocumentStatus.FAILED
                )
                batch_doc.status = BatchDocumentStatus.FAILED
                batch_doc.error_message = str(error)[:500]
                batch_doc.processed_at = datetime.now(timezone.utc)
                service.recompute_batch_counters(batch)
                db.commit()

    if service.complete_running_batch(batch_id, lease_owner):
        logger.info(
            "Batch completed: batch_id=%s, completed=%d, failed=%d",
            batch_id, batch.completed_documents, batch.failed_documents
        )


def _report_document_failure(
    error: Exception,
    batch_id: UUID,
    batch_doc: BatchDocument,
) -> None:
    """Report an unhandled document failure once while preserving loop progress."""
    if getattr(error, "sentry_already_reported", False):
        logger.warning(
            "Batch document failed after upstream Sentry reporting: batch_id=%s, doc_id=%s",
            batch_id,
            batch_doc.document_id,
        )
        return
    logger.exception(
        "Error processing document: batch_id=%s, doc_id=%s",
        batch_id,
        batch_doc.document_id,
        exc_info=(type(error), error, error.__traceback__),
    )
    report_background_task_exception(
        error,
        task_name="batch.process_document",
        tags={
            "component": "batch",
            "batch_id": batch_id,
            "document_id": batch_doc.document_id,
            "batch_document_id": batch_doc.id,
        },
    )


def _process_single_document(
    db: Session,
    batch: Batch,
    batch_doc: BatchDocument,
    flow: CurationFlow,
    cognito_sub: str,
    *,
    lease_owner: Optional[UUID] = None,
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

    # Check cancellation immediately before each document-side state change.
    _require_running_batch(
        db, batch, lock_for_update=True, lease_owner=lease_owner
    )
    require_batch_document_status_transition(
        batch_doc.status, BatchDocumentStatus.PROCESSING
    )
    batch_doc.status = BatchDocumentStatus.PROCESSING
    db.commit()

    start_time = time.time()

    try:
        _require_running_batch(db, batch, lease_owner=lease_owner)
        # Execute the flow on this document
        # Use asyncio.run() since we're in a sync context
        result_file_path, review_session_ids = asyncio.run(
            _execute_flow_for_document(
                flow=flow,
                document_id=str(batch_doc.document_id),
                cognito_sub=cognito_sub,
                batch_id=str(batch.id),
                db_user_id=batch.user_id,
            )
        )

        if not result_file_path and not review_session_ids:
            raise RuntimeError("Flow completed without FILE_READY or curation handoff output")

        # Calculate processing time
        processing_time_ms = int((time.time() - start_time) * 1000)

        _require_running_batch(
            db, batch, lock_for_update=True, lease_owner=lease_owner
        )
        require_batch_document_status_transition(
            batch_doc.status, BatchDocumentStatus.COMPLETED
        )
        batch_doc.status = BatchDocumentStatus.COMPLETED
        batch_doc.result_file_path = result_file_path
        batch_doc.review_session_ids = review_session_ids or None
        batch_doc.processing_time_ms = processing_time_ms
        batch_doc.processed_at = datetime.now(timezone.utc)
        BatchService(db).recompute_batch_counters(batch)
        db.commit()

        logger.info(
            "Document completed: batch_id=%s, doc_id=%s, time_ms=%d, result=%s",
            batch.id, batch_doc.document_id, processing_time_ms, result_file_path
        )

    except BatchCancelled:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        _require_running_batch(
            db, batch, lock_for_update=True, lease_owner=lease_owner
        )
        db.refresh(batch_doc)
        processing_time_ms = int((time.time() - start_time) * 1000)
        require_batch_document_status_transition(
            batch_doc.status, BatchDocumentStatus.FAILED
        )
        batch_doc.status = BatchDocumentStatus.FAILED
        batch_doc.result_file_path = None
        batch_doc.review_session_ids = None
        batch_doc.error_message = str(e)[:500]
        batch_doc.processing_time_ms = processing_time_ms
        batch_doc.processed_at = datetime.now(timezone.utc)
        BatchService(db).recompute_batch_counters(batch)
        db.commit()
        get_batch_broadcaster().publish_sync(
            batch.id,
            {
                "type": "DOCUMENT_STATUS",
                "batch_id": str(batch.id),
                "document_id": str(batch_doc.document_id),
                "batch_document_id": str(batch_doc.id),
                "position": batch_doc.position,
                "status": BatchDocumentStatus.FAILED.value,
                "result_file_path": None,
                "review_session_ids": None,
                "error_message": batch_doc.error_message,
                "processing_time_ms": processing_time_ms,
                "timestamp": batch_doc.processed_at.isoformat(),
            },
        )
        raise


async def _execute_flow_for_document(
    flow: CurationFlow,
    document_id: str,
    cognito_sub: str,
    batch_id: str,
    db_user_id: Optional[int] = None,
) -> tuple[Optional[str], list[str]]:
    """Execute a flow on a single document and extract batch success outputs.

    All events from the flow execution are published to the BatchEventBroadcaster
    so they can be streamed to the frontend via SSE.

    Args:
        flow: The curation flow to execute
        document_id: Weaviate document UUID
        cognito_sub: Cognito subject (auth_sub) for Weaviate tenant and file output
        batch_id: Batch UUID for session tracking

    Returns:
        File path/ID of the generated result file and any review session ids.
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
    review_session_ids: list[str] = []
    flow_failure_message: Optional[str] = None
    flow_failure_already_reported = False

    try:
        # Execute the flow and collect events
        async for event in execute_flow(
            flow=flow,
            user_id=cognito_sub,  # Cognito subject ID for Weaviate tenant
            session_id=session_id,
            db_user_id=db_user_id,
            document_id=document_id,
            document_name=None,  # Will be fetched by DocumentContext
            user_query=None,  # Use task_instructions from flow
            active_groups=None,  # Default groups
            flow_run_id=batch_id,
        ):
            event_type = event.get("type", "")

            # Look for file output in FILE_READY events
            # The runner emits FILE_READY when file output tools produce FileInfo
            if event_type == "FILE_READY":
                file_ready_details: Any = event.get("details")
                if not isinstance(file_ready_details, dict):
                    logger.warning(
                        "FILE_READY event ignored - malformed details payload: %r",
                        file_ready_details
                    )
                    continue

                download_url = file_ready_details.get("download_url")
                file_id = file_ready_details.get("file_id")

                if download_url and file_id:
                    # GUARDRAIL: Validate file ownership before capturing
                    # This prevents cross-user file leakage even if event routing has bugs
                    # (defense-in-depth for KANBAN-935 race condition fix)
                    if _validate_file_ownership(file_id, cognito_sub):
                        result_file_path = download_url
                        enriched_event = _enrich_event_for_batch(event, batch_id, document_id, session_id)
                        broadcaster.publish_sync(batch_uuid, enriched_event)
                        logger.info(
                            "Found file output in flow: %s (filename: %s)",
                            result_file_path,
                            file_ready_details.get("filename")
                        )
                    else:
                        logger.warning(
                            "FILE_READY event ignored - file %s not owned by user %s "
                            "(possible race condition or event routing bug)",
                            file_id, cognito_sub
                        )
                elif download_url:
                    logger.warning(
                        "FILE_READY event missing file_id, ignoring unverified output: %s",
                        download_url
                    )
                continue

            if event_type == "CURATION_HANDOFF_READY":
                handoff_details: Any = event.get("details")
                if isinstance(handoff_details, dict):
                    raw_review_session_ids = handoff_details.get("review_session_ids")
                    review_session_ids = (
                        [
                            str(review_session_id)
                            for review_session_id in raw_review_session_ids
                            if str(review_session_id).strip()
                        ]
                        if isinstance(raw_review_session_ids, list)
                        else []
                    )
                    enriched_event = _enrich_event_for_batch(event, batch_id, document_id, session_id)
                    broadcaster.publish_sync(batch_uuid, enriched_event)
                continue

            # Enrich/publish non-file events with batch context for frontend streaming.
            enriched_event = _enrich_event_for_batch(event, batch_id, document_id, session_id)
            broadcaster.publish_sync(batch_uuid, enriched_event)
            if event_type in {"FLOW_ERROR", "RUN_ERROR"}:
                raw_details = event.get("details")
                raw_data = event.get("data")
                failure_details: Dict[str, Any] = (
                    raw_details if isinstance(raw_details, dict) else {}
                )
                data: Dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
                flow_failure_message = (
                    failure_details.get("message")
                    or failure_details.get("error")
                    or data.get("message")
                    or data.get("error")
                    or event.get("message")
                    or "Flow execution failed."
                )
                flow_failure_already_reported = (
                    failure_details.get("reason") in {
                        "extraction_persistence_empty_result",
                        "extraction_persistence_failed",
                        "extraction_persistence_partial_result",
                    }
                )

            # Log supervisor completion for debugging
            if event_type == "SUPERVISOR_COMPLETE":
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

    if flow_failure_message:
        # A late flow error invalidates any earlier output event; do not return
        # a partial file or review handoff as a successful batch document.
        raise BatchFlowExecutionError(
            flow_failure_message,
            sentry_already_reported=flow_failure_already_reported,
        )

    if not result_file_path and not review_session_ids:
        logger.warning(
            "No batch success output found from flow '%s' for document %s",
            flow.name, document_id
        )

    return result_file_path, review_session_ids


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
    # Start with a sanitized copy of the original event.
    # Keep backend-only payloads (e.g., internal tool output) out of SSE events.
    enriched = {
        key: value
        for key, value in event.items()
        if key not in _BACKEND_ONLY_EVENT_FIELDS
    }

    # Add batch context
    enriched["batch_id"] = batch_id
    enriched["document_id"] = document_id
    enriched["session_id"] = session_id

    # Flatten 'data' field if present (matches chat.py pattern)
    if "data" in event:
        for key, value in event["data"].items():
            if key not in enriched:
                enriched[key] = value

    return enriched
