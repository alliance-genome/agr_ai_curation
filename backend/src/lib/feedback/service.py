"""FeedbackService: Orchestrate user feedback processing workflow."""

import logging
import os
import uuid
from datetime import datetime
from typing import List

from sqlalchemy.orm import Session

from src.lib.chat_history_repository import ChatHistoryRepository
from src.lib.feedback.models import FeedbackReport, ProcessingStatus
from src.lib.feedback.email_notifier import EmailNotifier
from src.lib.feedback.sns_notifier import SNSNotifier
from src.lib.feedback.transcript import capture_feedback_conversation_transcript

logger = logging.getLogger(__name__)


class FeedbackService:
    """Orchestrates the complete user feedback processing workflow.

    This service coordinates:
    1. Lightweight payload creation (immediate, < 100ms)
    2. Background notification via SNS or email

    The two-phase approach ensures curators get immediate feedback
    while notifications happen asynchronously.
    """

    def __init__(self, db: Session):
        """Initialize FeedbackService with database session.

        Args:
            db: SQLAlchemy database session for feedback operations
        """
        self.db = db

        # Check if SNS is enabled
        use_sns = os.getenv("FEEDBACK_USE_SNS", "false").lower() == "true"

        if use_sns:
            sns_topic_arn = os.getenv("SNS_TOPIC_ARN")
            sns_region = os.getenv("SNS_REGION", "us-east-1")

            if not sns_topic_arn:
                logger.warning("SNS enabled but SNS_TOPIC_ARN not configured, falling back to Email")
                self.notifier = EmailNotifier()
            else:
                logger.info('Using SNS for feedback notifications: %s', sns_topic_arn)
                self.notifier = SNSNotifier(topic_arn=sns_topic_arn, region=sns_region)
        else:
            # Use existing SMTP email notifier
            logger.info("Using SMTP for feedback notifications")
            self.notifier = EmailNotifier()

        logger.info("FeedbackService initialized")

    def create_feedback_payload(
        self,
        session_id: str,
        curator_id: str,
        feedback_text: str,
        trace_ids: List[str],
        user_auth_sub: str,
        authenticated_user_email: str | None = None,
    ) -> str:
        """Create lightweight feedback payload and save to database.

        This method performs minimal processing to ensure fast response times
        (< 100ms). Heavy processing happens later in background.

        Args:
            session_id: Chat session identifier
            curator_id: Email/ID of curator submitting feedback
            feedback_text: Feedback comments from curator
            trace_ids: List of trace IDs to attach (for reference only)
            user_auth_sub: Authenticated token subject used for transcript lookup
            authenticated_user_email: Authenticated email claim for curator verification

        Returns:
            feedback_id: UUID string identifying this feedback report
        """
        feedback_id = str(uuid.uuid4())
        conversation_transcript = self._maybe_capture_conversation_transcript(
            feedback_id=feedback_id,
            session_id=session_id,
            curator_id=curator_id,
            user_auth_sub=user_auth_sub,
            authenticated_user_email=authenticated_user_email,
        )

        report = FeedbackReport(
            id=feedback_id,
            session_id=session_id,
            curator_id=curator_id,
            feedback_text=feedback_text,
            trace_ids=trace_ids,
            conversation_transcript=conversation_transcript,
            processing_status=ProcessingStatus.PENDING,
            created_at=datetime.utcnow(),
        )

        self.db.add(report)
        self.db.commit()

        logger.info('Created feedback payload %s for session %s', feedback_id, session_id)
        return feedback_id

    def process_feedback_report(self, feedback_id: str) -> None:
        """Process feedback report in background (send notification).

        This method:
        1. Fetches the feedback report from database
        2. Sends notification via SNS or email

        Args:
            feedback_id: UUID of feedback report to process
        """
        # Fetch report from database
        report = (
            self.db.query(FeedbackReport)
            .filter(FeedbackReport.id == feedback_id)
            .first()
        )

        if not report:
            logger.error('Feedback report %s not found in database', feedback_id)
            return

        try:
            # Mark as processing
            report.processing_status = ProcessingStatus.PROCESSING
            report.processing_started_at = datetime.utcnow()
            self.db.commit()

            logger.info('Starting background processing for feedback %s', feedback_id)

            # Send notification (SNS or email)
            try:
                self.notifier.send_feedback_notification(report)
                report.email_sent_at = datetime.utcnow()
                logger.info('Sent notification for feedback %s', feedback_id)
            except Exception as e:
                logger.error('Notification failed for %s: %s', feedback_id, str(e), exc_info=True)
                report.error_details = f"Notification error: {str(e)}"

            # Mark as completed
            report.processing_status = ProcessingStatus.COMPLETED
            report.processing_completed_at = datetime.utcnow()
            self.db.commit()

            logger.info('Completed background processing for feedback %s', feedback_id)

        except Exception as e:
            # Catch-all for unexpected errors
            logger.error(
                'Unexpected error processing feedback %s: %s', feedback_id, str(e), exc_info=True
            )
            report.processing_status = ProcessingStatus.FAILED
            report.error_details = f"Unexpected error: {str(e)}"
            self.db.commit()

    def _maybe_capture_conversation_transcript(
        self,
        *,
        feedback_id: str,
        session_id: str,
        curator_id: str,
        user_auth_sub: str,
        authenticated_user_email: str | None,
    ) -> dict | None:
        """Capture one durable transcript snapshot when the auth context matches."""

        if not self._curator_matches_authenticated_user(
            curator_id=curator_id,
            user_auth_sub=user_auth_sub,
            authenticated_user_email=authenticated_user_email,
        ):
            logger.info(
                "Skipping durable transcript lookup for feedback %s because "
                "curator_id %s does not match the authenticated user",
                feedback_id,
                curator_id,
            )
            return None

        repository = ChatHistoryRepository(self.db)
        try:
            transcript = capture_feedback_conversation_transcript(
                repository=repository,
                session_id=session_id,
                user_auth_sub=user_auth_sub,
            )
        except Exception as exc:
            logger.warning(
                "Failed to capture durable transcript for feedback %s "
                "(session_id=%s, user_auth_sub=%s): %s",
                feedback_id,
                session_id,
                user_auth_sub,
                exc,
                exc_info=True,
            )
            return None

        if transcript is None:
            logger.warning(
                "Durable transcript lookup returned no session for feedback %s "
                "(session_id=%s, user_auth_sub=%s)",
                feedback_id,
                session_id,
                user_auth_sub,
            )
            return None

        session_payload = transcript.get("session") if isinstance(transcript, dict) else None
        chat_kind = None
        if isinstance(session_payload, dict):
            normalized_chat_kind = str(session_payload.get("chat_kind") or "").strip()
            chat_kind = normalized_chat_kind or None

        if chat_kind == "assistant_chat":
            return transcript

        if chat_kind == "agent_studio":
            logger.warning(
                "Feedback %s captured an unexpected agent_studio transcript for session %s",
                feedback_id,
                session_id,
            )
            return transcript

        logger.warning(
            "Feedback %s captured transcript for session %s with unexpected chat_kind=%r",
            feedback_id,
            session_id,
            chat_kind,
        )

        return transcript

    @staticmethod
    def _curator_matches_authenticated_user(
        *,
        curator_id: str,
        user_auth_sub: str,
        authenticated_user_email: str | None,
    ) -> bool:
        normalized_curator_id = curator_id.strip().lower()
        if not normalized_curator_id:
            return False

        candidate_ids = {user_auth_sub.strip().lower()}
        if authenticated_user_email is not None and authenticated_user_email.strip():
            candidate_ids.add(authenticated_user_email.strip().lower())

        return normalized_curator_id in candidate_ids
