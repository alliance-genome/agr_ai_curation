"""EmailNotifier: Send email notifications for user feedback reports.

This module provides functionality to send email notifications to developers
when curators submit feedback, with retry logic and exponential backoff.
"""

import os
import logging
import smtplib
import time
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Sends email notifications for feedback reports.

    Sends formatted emails to developers with feedback details and links
    to Langfuse traces. Includes retry logic with exponential backoff
    for resilience against temporary SMTP failures.
    """

    MAX_RETRIES = 3

    def __init__(self):
        """Initialize EmailNotifier with SMTP configuration from environment."""
        self.smtp_host = os.getenv("SMTP_HOST")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER")
        self.smtp_password = os.getenv("SMTP_PASSWORD")
        self.from_email = os.getenv("SMTP_FROM_EMAIL")
        self.recipient_email = os.getenv("FEEDBACK_RECIPIENT_EMAIL")
        self.langfuse_host = os.getenv("LANGFUSE_HOST", "http://localhost:3000")

        logger.info("EmailNotifier initialized")

    def send_feedback_notification(self, feedback_report: Any) -> None:
        """Send email notification for a feedback report.

        Args:
            feedback_report: FeedbackReport model instance with:
                - id: Feedback ID
                - session_id: Session ID for Langfuse link
                - curator_id: Curator email
                - feedback_text: Feedback comments

        Raises:
            Exception: After MAX_RETRIES failed attempts
        """
        # Build email message
        message = self._build_email_message(feedback_report)

        # Send with retry logic
        for attempt in range(self.MAX_RETRIES):
            try:
                self._send_email(message)
                logger.info('Successfully sent feedback notification for %s', feedback_report.id)
                return  # Success - exit retry loop

            except smtplib.SMTPException as e:
                if attempt < self.MAX_RETRIES - 1:
                    # Calculate exponential backoff: 2^attempt seconds
                    sleep_time = 2 ** attempt
                    logger.warning(
                        f"Failed to send email for {feedback_report.id} "
                        f"(attempt {attempt + 1}/{self.MAX_RETRIES}): {str(e)}. "
                        f"Retrying in {sleep_time} seconds..."
                    )
                    time.sleep(sleep_time)
                else:
                    # Final attempt failed
                    logger.error(
                        f"Failed to send feedback notification for {feedback_report.id} "
                        f"after {self.MAX_RETRIES} attempts: {str(e)}"
                    )
                    raise  # Re-raise exception after max retries

    def _build_email_message(self, feedback_report: Any) -> EmailMessage:
        """Build formatted email message with feedback details.

        Args:
            feedback_report: FeedbackReport model instance

        Returns:
            EmailMessage ready to send
        """
        message = EmailMessage()
        message["Subject"] = f"Curator Feedback Report - {feedback_report.id}"
        message["From"] = self.from_email
        message["To"] = self.recipient_email

        # Build Langfuse link
        langfuse_link = f"{self.langfuse_host}/sessions/{feedback_report.session_id}"

        # Build email body
        body = f"""
New Curator Feedback Received

Feedback ID: {feedback_report.id}
Session ID: {feedback_report.session_id}
Curator: {feedback_report.curator_id}

Feedback Comments:
{feedback_report.feedback_text}

View in Langfuse:
{langfuse_link}

---
This is an automated notification from the AI Curation Prototype.
"""
        message.set_content(body)
        return message

    def _send_email(self, message: EmailMessage) -> None:
        """Send email via SMTP with TLS.

        Args:
            message: EmailMessage to send

        Raises:
            smtplib.SMTPException: On SMTP errors
        """
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(message)
