"""Feedback submission API endpoints."""

import logging
import threading
from typing import Annotated, Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.api.auth import get_auth_dependency
from src.lib.feedback.service import FeedbackService
from src.models.sql.database import get_feedback_db
from src.schemas.feedback import ErrorResponse, FeedbackResponse, FeedbackSubmission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback")


def _require_user_sub(user: Dict[str, Any]) -> str:
    """Return the authenticated user subject or raise 401."""

    raw_user_id = user.get("sub")
    if raw_user_id is None:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

    user_id = str(raw_user_id).strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")
    return user_id


def _run_feedback_processing_in_background(feedback_id: str) -> None:
    """Process one feedback report using a fresh database session."""

    from src.models.sql.database import FeedbackSessionLocal

    bg_db = FeedbackSessionLocal()
    try:
        FeedbackService(bg_db).process_feedback_report(feedback_id)
    except Exception as exc:
        logger.error(
            "Background processing failed for feedback %s: %s",
            feedback_id,
            exc,
            exc_info=True,
        )
    finally:
        bg_db.close()


def dispatch_feedback_report_processing(feedback_id: str) -> threading.Thread:
    """Launch feedback processing on a detached daemon thread."""

    worker = threading.Thread(
        target=_run_feedback_processing_in_background,
        kwargs={"feedback_id": feedback_id},
        name=f"feedback-report-{feedback_id}",
        daemon=True,
    )
    worker.start()
    return worker


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
    db: Annotated[Session, Depends(get_feedback_db)],
    user: Dict[str, Any] = get_auth_dependency(),
) -> FeedbackResponse:
    """Submit curator feedback with automatic trace capture.

    Args:
        submission: Feedback submission data (session_id, curator_id, feedback_text, trace_ids)
        db: Database session for feedback operations

    Returns:
        FeedbackResponse with success status, feedback_id, and message

    Raises:
        HTTPException: 400 for validation errors, 500 for database errors
    """
    user_auth_sub = _require_user_sub(user)

    logger.info(
        "Received feedback submission from %s for session %s",
        submission.curator_id,
        submission.session_id,
    )

    try:
        service = FeedbackService(db)
        feedback_id = service.create_feedback_payload(
            session_id=submission.session_id,
            curator_id=submission.curator_id,
            feedback_text=submission.feedback_text,
            trace_ids=submission.trace_ids,
            user_auth_sub=user_auth_sub,
        )
    except ValueError as exc:
        logger.warning("Validation error in feedback submission: %s", str(exc))
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": "Validation error",
                "details": [{"field": "feedback_text", "message": str(exc)}],
            },
        )
    except Exception as exc:
        logger.error(
            "Failed to save feedback to database: %s",
            str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": "Failed to save feedback to database",
            },
        )

    logger.info(
        "Created feedback payload %s, dispatching background processing",
        feedback_id,
    )

    try:
        dispatch_feedback_report_processing(feedback_id)
    except RuntimeError as exc:
        logger.error(
            "Failed to dispatch background processing for feedback %s: %s",
            feedback_id,
            exc,
            exc_info=True,
        )

    return FeedbackResponse(
        status="success",
        feedback_id=feedback_id,
        message="Feedback submitted successfully. Report will be processed in background.",
    )
