"""Pydantic schemas for feedback API.

Defines request and response schemas for the user feedback submission endpoint.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Any, List, Optional


class FeedbackSubmission(BaseModel):
    """Request schema for submitting curator feedback.

    Used for the POST /api/feedback/submit endpoint.
    """

    session_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Unique identifier for the chat session",
        examples=["chat_session_20251021_143000"],
    )

    curator_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Identifier for the curator submitting feedback (email or user ID)",
        examples=["curator@example.com"],
    )

    feedback_text: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Curator's detailed feedback comments",
        examples=[
            "The AI suggested the wrong ontology term. It should use DOID:1234 instead of DOID:5678."
        ],
    )

    trace_ids: List[str] = Field(
        default_factory=list,
        description="List of Langfuse trace IDs from this chat session (optional, can be empty)",
        examples=[["trace_abc123", "trace_def456"], []],
    )

    @field_validator("feedback_text")
    @classmethod
    def validate_feedback_text_not_empty(cls, v: str) -> str:
        """Ensure feedback text is not empty after stripping whitespace."""
        if not v.strip():
            raise ValueError("Feedback text cannot be empty or whitespace only")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "session_id": "chat_session_20251021_143000",
                    "curator_id": "curator@example.com",
                    "feedback_text": "The AI recommended the wrong gene annotation. Expected FBgn123456 but got FBgn789012.",
                    "trace_ids": ["fdeac438c90617c73497d298490b6db1"],
                }
            ]
        }
    }


class FeedbackResponse(BaseModel):
    """Response schema for successful feedback submission.

    Returned when lightweight processing completes successfully.
    Background processing continues asynchronously.
    """

    status: str = Field(
        ...,
        pattern="^success$",
        description="Always 'success' for 200 response",
        examples=["success"],
    )

    feedback_id: str = Field(
        ...,
        description="Unique identifier for this feedback report (UUID)",
        examples=["f47ac10b-58cc-4372-a567-0e02b2c3d479"],
    )

    message: str = Field(
        ...,
        description="Human-readable success message",
        examples=["Feedback submitted successfully. Report will be processed in background."],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "success",
                    "feedback_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
                    "message": "Feedback submitted successfully. Report will be processed in background.",
                }
            ]
        }
    }


class FeedbackTranscriptDebug(BaseModel):
    """Transcript availability metadata for feedback debug detail responses."""

    available: bool = Field(..., description="Whether a durable transcript snapshot exists")
    message_count: Optional[int] = Field(
        default=None,
        description="Number of transcript messages captured when available",
    )
    captured_at: Optional[str] = Field(
        default=None,
        description="Timestamp when the transcript snapshot was captured",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Transcript session identifier when stored",
    )
    chat_kind: Optional[str] = Field(
        default=None,
        description="Transcript chat kind when stored",
    )
    title: Optional[str] = Field(
        default=None,
        description="Stored transcript title when present",
    )
    effective_title: Optional[str] = Field(
        default=None,
        description="Stored transcript effective title when present",
    )
    session_matches_feedback: Optional[bool] = Field(
        default=None,
        description="Whether the stored transcript session matches the feedback session",
    )


class FeedbackTraceDataError(BaseModel):
    """Redacted trace-data error metadata for one trace capture attempt."""

    trace_id: Optional[str] = Field(default=None, description="Trace ID for the failed capture")
    type: Optional[str] = Field(default=None, description="Exception or error type")
    message: Optional[str] = Field(default=None, description="Redacted error message")


class FeedbackTraceDataDebug(BaseModel):
    """Trace-data capture status summary for feedback debug detail responses."""

    available: bool = Field(..., description="Whether persisted trace_data exists")
    status: str = Field(
        ...,
        description=(
            "Explicit trace-data status: not_requested, missing, stale, success, partial, "
            "error, or capture_status_missing"
        ),
    )
    stale: bool = Field(
        ...,
        description="Whether persisted trace_data no longer matches feedback identifiers",
    )
    capture_status: Optional[str] = Field(
        default=None,
        description="Stored trace_data capture_status when available",
    )
    captured_at: Optional[str] = Field(
        default=None,
        description="Stored trace_data captured_at timestamp when available",
    )
    schema_version: Optional[int] = Field(
        default=None,
        description="Stored trace_data schema version when available",
    )
    source_kind: Optional[str] = Field(
        default=None,
        description="Trace-data source kind when available",
    )
    source_extractor: Optional[str] = Field(
        default=None,
        description="Trace-data extractor identifier when available",
    )
    expected_trace_ids: List[str] = Field(
        default_factory=list,
        description="Trace IDs currently stored on the feedback report",
    )
    stored_trace_ids: List[str] = Field(
        default_factory=list,
        description="Trace IDs recorded inside persisted trace_data",
    )
    trace_count: int = Field(
        ...,
        description="Number of per-trace status records in persisted trace_data",
    )
    omitted_trace_id_count: Optional[int] = Field(
        default=None,
        description=(
            "Number of trace IDs omitted from persisted trace_data summary when stored"
        ),
    )
    error_summary: Optional[dict[str, Any]] = Field(
        default=None,
        description="Redacted trace-data error summary metadata",
    )
    errors: List[FeedbackTraceDataError] = Field(
        default_factory=list,
        description="Redacted per-trace error metadata",
    )


class FeedbackDebugDetailResponse(BaseModel):
    """Read-only feedback debug detail response for authorized users."""

    feedback_id: str = Field(..., description="Feedback report identifier")
    session_id: str = Field(..., description="Chat session identifier")
    curator_id: str = Field(..., description="Curator identifier stored with feedback")
    feedback_text: str = Field(..., description="Curator feedback comments")
    trace_ids: List[str] = Field(
        default_factory=list,
        description="Trace IDs stored with the feedback report",
    )
    processing_status: str = Field(..., description="Feedback background processing status")
    created_at: Optional[str] = Field(default=None, description="Feedback creation timestamp")
    processing_started_at: Optional[str] = Field(
        default=None,
        description="Background processing start timestamp",
    )
    processing_completed_at: Optional[str] = Field(
        default=None,
        description="Background processing completion timestamp",
    )
    email_sent_at: Optional[str] = Field(
        default=None,
        description="Notification sent timestamp when available",
    )
    processing_error: Optional[str] = Field(
        default=None,
        description="Redacted processing error summary when available",
    )
    feedback_debug_url: str = Field(
        ...,
        description="Canonical AI Curation feedback debug detail URL",
    )
    trace_review_session_url: str = Field(
        ...,
        description="Canonical TraceReview session bundle export URL",
    )
    transcript: FeedbackTranscriptDebug = Field(
        ...,
        description="Stored transcript availability metadata",
    )
    trace_data: FeedbackTraceDataDebug = Field(
        ...,
        description="Persisted trace-data capture summary",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "feedback_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
                    "session_id": "chat_session_20251021_143000",
                    "curator_id": "curator@example.com",
                    "feedback_text": "The AI recommended the wrong gene annotation.",
                    "trace_ids": ["fdeac438c90617c73497d298490b6db1"],
                    "processing_status": "completed",
                    "created_at": "2026-04-25T12:00:00+00:00",
                    "processing_started_at": "2026-04-25T12:00:01+00:00",
                    "processing_completed_at": "2026-04-25T12:00:02+00:00",
                    "email_sent_at": "2026-04-25T12:00:02+00:00",
                    "processing_error": None,
                    "feedback_debug_url": (
                        "/api/feedback/f47ac10b-58cc-4372-a567-0e02b2c3d479/debug"
                    ),
                    "trace_review_session_url": (
                        "/api/traces/sessions/chat_session_20251021_143000/export"
                        "?source=remote"
                    ),
                    "transcript": {
                        "available": True,
                        "message_count": 8,
                        "captured_at": "2026-04-25T12:00:00+00:00",
                        "session_id": "chat_session_20251021_143000",
                        "chat_kind": "assistant_chat",
                        "title": "Saved title",
                        "effective_title": "Saved title",
                        "session_matches_feedback": True,
                    },
                    "trace_data": {
                        "available": True,
                        "status": "success",
                        "stale": False,
                        "capture_status": "success",
                        "captured_at": "2026-04-25T12:00:01Z",
                        "schema_version": 1,
                        "source_kind": "langfuse",
                        "source_extractor": (
                            "src.lib.agent_studio.trace_context_service."
                            "get_trace_context_for_explorer"
                        ),
                        "expected_trace_ids": ["fdeac438c90617c73497d298490b6db1"],
                        "stored_trace_ids": ["fdeac438c90617c73497d298490b6db1"],
                        "trace_count": 1,
                        "omitted_trace_id_count": 0,
                        "error_summary": None,
                        "errors": [],
                    },
                }
            ]
        }
    }


class ValidationError(BaseModel):
    """Individual field validation error."""

    field: str = Field(..., description="Field name that failed validation")
    message: str = Field(..., description="Error message describing the validation failure")


class ErrorResponse(BaseModel):
    """Response schema for error cases (400, 500).

    Used for both validation errors (400) and server errors (500).
    """

    status: str = Field(
        ...,
        pattern="^error$",
        description="Always 'error' for error responses",
        examples=["error"],
    )

    error: str = Field(
        ...,
        description="Error message",
        examples=["Validation error", "Failed to save feedback to database"],
    )

    details: Optional[List[ValidationError]] = Field(
        default=None,
        description="Specific validation errors (only present for 400 responses)",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "error",
                    "error": "Validation error",
                    "details": [
                        {
                            "field": "feedback_text",
                            "message": "Feedback text cannot be empty",
                        }
                    ],
                },
                {
                    "status": "error",
                    "error": "Failed to save feedback to database",
                    "details": None,
                },
            ]
        }
    }
