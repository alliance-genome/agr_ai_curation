"""SNS notification service for feedback reports."""
import logging
import boto3
from botocore.exceptions import ClientError
from datetime import datetime

logger = logging.getLogger(__name__)


class SNSNotifier:
    """Send feedback notifications via AWS SNS."""

    def __init__(self, topic_arn: str, region: str = "us-east-1"):
        """Initialize SNS notifier.

        Args:
            topic_arn: ARN of SNS topic to publish to
            region: AWS region (default: us-east-1)
        """
        self.topic_arn = topic_arn
        self.region = region
        self.sns_client = boto3.client("sns", region_name=region)

    def _build_email_body(
        self,
        feedback_id: str,
        curator_id: str,
        feedback_text: str,
        session_id: str,
        trace_ids: list[str],
    ) -> str:
        """Build plain text email body."""
        lines = [
            "New Curator Feedback Submitted",
            "=" * 50,
            "",
            f"Feedback ID: {feedback_id}",
            f"Curator: {curator_id}",
            f"Session ID: {session_id}",
            f"Submitted: {self._get_timestamp()}",
            "",
            "Feedback:",
            "-" * 50,
            feedback_text,
            "",
        ]

        # Add trace IDs if provided
        if trace_ids:
            lines.extend([
                "Associated Trace IDs:",
                "-" * 50,
            ])
            for trace_id in trace_ids:
                lines.append(f"  - {trace_id}")

        lines.extend([
            "",
            "-" * 50,
            "This is an automated notification from AI Curation Platform",
        ])

        return "\n".join(lines)

    def _get_timestamp(self) -> str:
        """Get current timestamp as string."""
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    def send_feedback_notification(self, feedback_report) -> None:
        """Send feedback notification for a feedback report.

        Args:
            feedback_report: FeedbackReport model instance with:
                - id: Feedback ID
                - session_id: Session ID
                - curator_id: Curator email
                - feedback_text: Feedback comments
                - trace_ids: List of trace IDs

        Raises:
            Exception: If SNS publish fails
        """
        success = self._send_sns(
            feedback_id=feedback_report.id,
            curator_id=feedback_report.curator_id,
            feedback_text=feedback_report.feedback_text,
            session_id=feedback_report.session_id,
            trace_ids=feedback_report.trace_ids or [],
        )

        if not success:
            raise Exception("Failed to send SNS notification")

    def _send_sns(
        self,
        feedback_id: str,
        curator_id: str,
        feedback_text: str,
        session_id: str,
        trace_ids: list[str],
    ) -> bool:
        """Send feedback notification via SNS.

        Args:
            feedback_id: UUID of feedback report
            curator_id: Email/ID of curator who submitted feedback
            feedback_text: The feedback text
            session_id: Chat session ID
            trace_ids: List of trace IDs

        Returns:
            True if notification sent successfully, False otherwise
        """
        try:
            logger.info(
                "SNS publish attempt",
                extra={
                    "topic_arn": self.topic_arn,
                    "region": self.region,
                    "feedback_id": str(feedback_id),
                    "session_id": session_id,
                    "curator_id": curator_id,
                    "trace_id_count": len(trace_ids)
                }
            )

            # Build email subject
            subject = f"New Curator Feedback: {str(feedback_id)[:8]}"

            # Build email body (plain text)
            body = self._build_email_body(
                feedback_id=str(feedback_id),
                curator_id=curator_id,
                feedback_text=feedback_text,
                session_id=session_id,
                trace_ids=trace_ids,
            )

            # Publish to SNS
            response = self.sns_client.publish(
                TopicArn=self.topic_arn,
                Subject=subject,
                Message=body,
                MessageAttributes={
                    "feedback_id": {
                        "DataType": "String",
                        "StringValue": str(feedback_id)
                    },
                    "curator_id": {
                        "DataType": "String",
                        "StringValue": curator_id
                    }
                }
            )

            message_id = response["MessageId"]
            logger.info(
                f"Feedback notification sent via SNS: {message_id} "
                f"for feedback {feedback_id}",
                extra={
                    "topic_arn": self.topic_arn,
                    "region": self.region,
                    "feedback_id": str(feedback_id),
                    "session_id": session_id,
                    "curator_id": curator_id,
                    "trace_id_count": len(trace_ids),
                    "message_id": message_id
                }
            )
            return True

        except ClientError as e:
            logger.error(
                f"Failed to send SNS notification for feedback {feedback_id}: {e}",
                exc_info=True,
                extra={
                    "topic_arn": self.topic_arn,
                    "region": self.region,
                    "feedback_id": str(feedback_id),
                    "session_id": session_id,
                    "curator_id": curator_id,
                    "trace_id_count": len(trace_ids)
                }
            )
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error sending SNS notification: {e}",
                exc_info=True,
                extra={
                    "topic_arn": self.topic_arn,
                    "region": self.region,
                    "feedback_id": str(feedback_id),
                    "session_id": session_id,
                    "curator_id": curator_id,
                    "trace_id_count": len(trace_ids)
                }
            )
            return False
