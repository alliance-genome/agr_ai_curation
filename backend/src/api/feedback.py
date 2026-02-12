"""Feedback submission API endpoints.

Provides endpoints for curators to submit detailed feedback on AI chat interactions
with automatic capture of session context (Langfuse traces, messages, logs).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Dict, Any

from src.models.sql.database import get_feedback_db
from src.schemas.feedback import FeedbackSubmission, FeedbackResponse, ErrorResponse, ValidationError
from src.lib.feedback.service import FeedbackService
from src.api.auth import get_auth_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback")


@router.post(
    "/submit",
    response_model=FeedbackResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error in input"},
        500: {"model": ErrorResponse, "description": "Server error during submission"},
    },
    summary="Submit curator feedback",
    description="""
    Submits curator feedback for an AI chat interaction. This is a two-phase process:

    **Phase 1: Lightweight Initial Processing** (< 500ms)
    - Validates input data
    - Saves minimal payload to PostgreSQL database
    - Returns success response immediately

    **Phase 2: Background Heavy Lifting** (non-blocking)
    - Enriches Langfuse traces with feedback metadata
    - Extracts complete trace data (observations, tool calls, reasoning, costs)
    - Compiles comprehensive report
    - Sends email notification to developers

    The curator sees success immediately (Phase 1). Background processing (Phase 2)
    runs asynchronously without blocking the response. Background failures are logged
    and emailed to developers but do not affect the curator experience.
    """,
)
def submit_feedback(
    submission: FeedbackSubmission,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_feedback_db)],
    user: Dict[str, Any] = get_auth_dependency()
) -> FeedbackResponse:
    """Submit curator feedback with automatic trace capture.

    Args:
        submission: Feedback submission data (session_id, curator_id, feedback_text, trace_ids)
        background_tasks: FastAPI background tasks manager
        db: Database session for feedback operations

    Returns:
        FeedbackResponse with success status, feedback_id, and message

    Raises:
        HTTPException: 400 for validation errors, 500 for database errors
    """
    try:
        logger.info(
            f"Received feedback submission from {submission.curator_id} "
            f"for session {submission.session_id}"
        )

        # Phase 1: Lightweight processing (create payload, save to DB)
        service = FeedbackService(db)
        feedback_id = service.create_feedback_payload(
            session_id=submission.session_id,
            curator_id=submission.curator_id,
            feedback_text=submission.feedback_text,
            trace_ids=submission.trace_ids,
        )

        logger.info(
            'Created feedback payload %s, scheduling background processing', feedback_id)

        # Phase 2: Schedule background processing (trace extraction, enrichment, email)
        # NOTE: We create a new service instance in the background task because
        # the db session will be closed by the time the background task runs.
        # The background task will need its own database session.
        def process_in_background():
            """Background task to process feedback report.

            This runs in a separate thread after the HTTP response is sent.
            Creates its own database session to avoid session lifecycle issues.
            """
            # Import here to avoid circular import issues
            from src.models.sql.database import FeedbackSessionLocal

            # Create new database session for background processing
            bg_db = FeedbackSessionLocal()
            try:
                bg_service = FeedbackService(bg_db)
                bg_service.process_feedback_report(feedback_id)
            except Exception as e:
                logger.error(
                    'Background processing failed for feedback %s: %s', feedback_id, str(e), exc_info=True,
                )
            finally:
                bg_db.close()

        background_tasks.add_task(process_in_background)

        # Return success immediately (curator doesn't wait for background processing)
        return FeedbackResponse(
            status="success",
            feedback_id=feedback_id,
            message="Feedback submitted successfully. Report will be processed in background.",
        )

    except ValueError as e:
        # Validation errors (from Pydantic or business logic)
        logger.warning('Validation error in feedback submission: %s', str(e))
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": "Validation error",
                "details": [{"field": "feedback_text", "message": str(e)}],
            },
        )

    except Exception as e:
        # Database or unexpected errors during lightweight processing
        logger.error(
            'Failed to save feedback to database: %s', str(e), exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": "Failed to save feedback to database",
            },
        )
