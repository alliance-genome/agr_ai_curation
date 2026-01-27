"""Pydantic schemas for feedback API.

Defines request and response schemas for the user feedback submission endpoint.
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional


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
