"""Feedback submission API endpoints."""

import logging
import threading
from typing import Annotated, Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.api.admin.prompts import get_admin_emails
from src.api.auth import get_auth_dependency
from src.lib.feedback.service import FeedbackDebugDetailForbidden, FeedbackService
from src.models.sql.database import get_feedback_db
from src.schemas.feedback import (
    ErrorResponse,
    FeedbackDebugDetailResponse,
    FeedbackResponse,
    FeedbackSubmission,
)

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


def _authenticated_user_email(user: Dict[str, Any]) -> str | None:
    """Return the normalized authenticated user email when present."""

    raw_email = user.get("email")
    if raw_email is None:
        return None

    email = str(raw_email).strip()
    return email or None


def _can_admin_debug_feedback(user: Dict[str, Any]) -> bool:
    """Return whether the user can inspect any feedback debug detail.

    Feedback debug admin access uses the same documented ADMIN_EMAILS allowlist
    policy as the admin prompt API.
    """

    user_email = _authenticated_user_email(user)
    if user_email is None:
        return False
    return user_email.lower() in get_admin_emails()


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
            authenticated_curator_email=(
                str(user.get("email")).strip() if user.get("email") is not None else None
            ),
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


@router.get(
    "/{feedback_id}/debug",
    response_model=FeedbackDebugDetailResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Feedback report not found"},
        403: {"model": ErrorResponse, "description": "Not authorized to inspect this feedback"},
        500: {"model": ErrorResponse, "description": "Server error loading debug detail"},
    },
    summary="Get feedback debug detail",
    description="""
    Returns read-only debug metadata for a feedback report. The response includes
    non-secret feedback metadata, transcript availability, trace IDs, trace-data
    capture status, redacted trace-data error metadata, and canonical links for
    the feedback debug endpoint and TraceReview session bundle export.
    Access is limited to the feedback owner, matched by authenticated subject or
    email, and administrators listed in the ADMIN_EMAILS allowlist.

    This endpoint intentionally does not expose raw trace payloads, auth headers,
    cookies, or full persisted trace data.
    """,
)
def get_feedback_debug_detail(
    feedback_id: str,
    db: Annotated[Session, Depends(get_feedback_db)],
    user: Dict[str, Any] = get_auth_dependency(),
) -> FeedbackDebugDetailResponse:
    """Return read-only feedback debug details for an authenticated user."""

    user_auth_sub = _require_user_sub(user)
    authenticated_curator_email = _authenticated_user_email(user)

    try:
        detail = FeedbackService(db).get_feedback_debug_detail(
            feedback_id,
            user_auth_sub=user_auth_sub,
            authenticated_curator_email=authenticated_curator_email,
            allow_admin_debug_access=_can_admin_debug_feedback(user),
        )
    except FeedbackDebugDetailForbidden:
        return JSONResponse(
            status_code=403,
            content={
                "status": "error",
                "error": "Not authorized to inspect this feedback",
            },
        )
    except Exception as exc:
        logger.error(
            "Failed to load feedback debug detail for %s: %s",
            feedback_id,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": "Failed to load feedback debug detail",
            },
        )

    if detail is None:
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "error": "Feedback report not found",
            },
        )

    return FeedbackDebugDetailResponse.model_validate(detail)
