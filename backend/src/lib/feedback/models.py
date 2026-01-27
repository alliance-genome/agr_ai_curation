"""SQLAlchemy models for user feedback system.

Defines the FeedbackReport model for storing curator feedback submissions
along with captured Langfuse trace data and processing status.
"""

from sqlalchemy import Column, String, Text, DateTime, JSON, Enum as SQLEnum
from datetime import datetime
import enum
import uuid

from src.models.sql.database import Base


class ProcessingStatus(enum.Enum):
    """Processing status for feedback reports.

    - PENDING: Initial state after submission, awaiting background processing
    - PROCESSING: Background task actively processing trace extraction and enrichment
    - COMPLETED: All processing complete (trace enriched, data extracted, email sent)
    - FAILED: Background processing encountered fatal error
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class FeedbackReport(Base):
    """User feedback report with captured trace data.

    Represents a curator's submitted feedback about a specific AI interaction.
    Stores both the lightweight payload (captured immediately) and the heavy
    data extracted during background processing (trace data, enrichment results).

    Lifecycle:
        1. Created with PENDING status when curator submits feedback
        2. Moves to PROCESSING when background task starts
        3. Moves to COMPLETED when all extraction/enrichment/email completes
        4. Moves to FAILED if background processing encounters fatal error
    """

    __tablename__ = "feedback_reports"

    # Primary key
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Core feedback data (captured immediately during lightweight processing)
    session_id = Column(String(255), nullable=False, index=True)
    curator_id = Column(String(255), nullable=False)
    feedback_text = Column(Text, nullable=False)
    trace_ids = Column(JSON, nullable=False)  # List[str]
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    # Processing status
    processing_status = Column(
        SQLEnum(ProcessingStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ProcessingStatus.PENDING,
        index=True,
    )

    # Extracted data (populated by background task)
    trace_data = Column(JSON, nullable=True)  # Full trace extraction from Langfuse

    # Error tracking
    error_details = Column(Text, nullable=True)

    # Timing metadata
    email_sent_at = Column(DateTime, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    processing_completed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<FeedbackReport(id={self.id}, session_id={self.session_id}, status={self.processing_status.value})>"
