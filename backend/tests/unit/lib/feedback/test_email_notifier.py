"""Unit tests for EmailNotifier.

Tests email notification logic with retry and error handling.

CRITICAL: These tests MUST FAIL before implementation!
"""

import pytest
from unittest.mock import MagicMock, patch, call
import smtplib


@pytest.fixture
def mock_smtp():
    """Mock SMTP server."""
    with patch("src.lib.feedback.email_notifier.smtplib.SMTP") as mock:
        server = MagicMock()
        mock.return_value.__enter__.return_value = server
        yield server


@pytest.fixture
def sample_feedback_report():
    """Sample feedback report for email notification."""
    report = MagicMock()
    report.id = "feedback_123"
    report.session_id = "session_456"
    report.curator_id = "curator@example.com"
    report.feedback_text = "Test feedback comment about ontology terms"
    return report


@pytest.fixture
def mock_config():
    """Mock email configuration via environment variables."""
    env_vars = {
        "SMTP_HOST": "smtp.test.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "test@example.com",
        "SMTP_PASSWORD": "test_password",
        "SMTP_FROM_EMAIL": "feedback@example.com",
        "FEEDBACK_RECIPIENT_EMAIL": "developers@example.com",
        "LANGFUSE_HOST": "http://localhost:3000"
    }
    with patch("src.lib.feedback.email_notifier.os.getenv") as mock_getenv:
        # Return values based on the requested environment variable
        mock_getenv.side_effect = lambda key, default=None: env_vars.get(key, default)
        yield mock_getenv


class TestEmailNotifierSendNotification:
    """Tests for send_feedback_notification() method."""

    def test_send_feedback_notification_sends_email(
        self, mock_smtp, mock_config, sample_feedback_report
    ):
        """Test that email notification is sent successfully.

        VERIFY: This test should FAIL initially (EmailNotifier doesn't exist yet).
        """
        from src.lib.feedback.email_notifier import EmailNotifier

        notifier = EmailNotifier()
        notifier.send_feedback_notification(sample_feedback_report)

        # Should call SMTP methods
        assert mock_smtp.starttls.called
        mock_smtp.login.assert_called_once_with("test@example.com", "test_password")
        assert mock_smtp.send_message.called

    def test_send_feedback_notification_email_content(
        self, mock_smtp, mock_config, sample_feedback_report
    ):
        """Test email content includes all required information.

        VERIFY: This test should FAIL initially (EmailNotifier doesn't exist yet).
        """
        from src.lib.feedback.email_notifier import EmailNotifier

        notifier = EmailNotifier()
        notifier.send_feedback_notification(sample_feedback_report)

        # Get the message that was sent
        sent_message = mock_smtp.send_message.call_args[0][0]

        # Should include feedback ID in subject
        assert "feedback_123" in sent_message["Subject"] or "123" in sent_message["Subject"]

        # Should include sender and recipient
        assert sent_message["From"] == "feedback@example.com"
        assert sent_message["To"] == "developers@example.com"

        # Should include feedback details in body
        body = str(sent_message.get_payload())
        assert "feedback_123" in body
        assert "session_456" in body
        assert "curator@example.com" in body
        assert "Test feedback comment" in body

    def test_send_feedback_notification_retry_on_failure(
        self, mock_smtp, mock_config, sample_feedback_report
    ):
        """Test retry logic when SMTP fails.

        VERIFY: This test should FAIL initially (EmailNotifier doesn't exist yet).
        """
        from src.lib.feedback.email_notifier import EmailNotifier

        # Mock SMTP failures followed by success
        mock_smtp.send_message.side_effect = [
            smtplib.SMTPException("Temporary error"),  # 1st attempt fails
            smtplib.SMTPException("Temporary error"),  # 2nd attempt fails
            None,  # 3rd attempt succeeds
        ]

        notifier = EmailNotifier()
        notifier.send_feedback_notification(sample_feedback_report)

        # Should retry 3 times total
        assert mock_smtp.send_message.call_count == 3

    def test_send_feedback_notification_raises_after_max_retries(
        self, mock_smtp, mock_config, sample_feedback_report
    ):
        """Test that exception is raised after max retries.

        VERIFY: This test should FAIL initially (EmailNotifier doesn't exist yet).
        """
        from src.lib.feedback.email_notifier import EmailNotifier

        # Mock all attempts failing
        mock_smtp.send_message.side_effect = smtplib.SMTPException("Permanent error")

        notifier = EmailNotifier()

        # Should raise exception after 3 retries
        with pytest.raises(Exception):
            notifier.send_feedback_notification(sample_feedback_report)

        # Should have attempted 3 times
        assert mock_smtp.send_message.call_count == 3

    def test_send_feedback_notification_exponential_backoff(
        self, mock_smtp, mock_config, sample_feedback_report
    ):
        """Test exponential backoff between retries.

        VERIFY: This test should FAIL initially (EmailNotifier doesn't exist yet).
        """
        from src.lib.feedback.email_notifier import EmailNotifier

        # Mock failures
        mock_smtp.send_message.side_effect = [
            smtplib.SMTPException("Error"),
            smtplib.SMTPException("Error"),
            None,
        ]

        notifier = EmailNotifier()

        with patch("src.lib.feedback.email_notifier.time.sleep") as mock_sleep:
            notifier.send_feedback_notification(sample_feedback_report)

            # Should sleep between retries with exponential backoff
            # 1st retry: 2^0 = 1 second
            # 2nd retry: 2^1 = 2 seconds
            sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
            assert len(sleep_calls) == 2
            assert sleep_calls[0] == 1  # 2^0
            assert sleep_calls[1] == 2  # 2^1

    def test_send_feedback_notification_logs_success(
        self, mock_smtp, mock_config, sample_feedback_report
    ):
        """Test that successful email sending is logged.

        VERIFY: This test should FAIL initially (EmailNotifier doesn't exist yet).
        """
        from src.lib.feedback.email_notifier import EmailNotifier

        notifier = EmailNotifier()

        with patch("src.lib.feedback.email_notifier.logger") as mock_logger:
            notifier.send_feedback_notification(sample_feedback_report)

            # Should log success (format string + args with %s pattern)
            mock_logger.info.assert_called()
            call_args = mock_logger.info.call_args[0]
            assert "feedback_123" in str(call_args)

    def test_send_feedback_notification_logs_retries(
        self, mock_smtp, mock_config, sample_feedback_report
    ):
        """Test that retry attempts are logged.

        VERIFY: This test should FAIL initially (EmailNotifier doesn't exist yet).
        """
        from src.lib.feedback.email_notifier import EmailNotifier

        # Mock failures then success
        mock_smtp.send_message.side_effect = [
            smtplib.SMTPException("Temporary error"),
            None,
        ]

        notifier = EmailNotifier()

        with patch("src.lib.feedback.email_notifier.logger") as mock_logger:
            notifier.send_feedback_notification(sample_feedback_report)

            # Should log warning about retry
            mock_logger.warning.assert_called()
            log_message = mock_logger.warning.call_args[0][0]
            assert "attempt" in log_message.lower()

    def test_send_feedback_notification_logs_final_failure(
        self, mock_smtp, mock_config, sample_feedback_report
    ):
        """Test that final failure after all retries is logged.

        VERIFY: This test should FAIL initially (EmailNotifier doesn't exist yet).
        """
        from src.lib.feedback.email_notifier import EmailNotifier

        # Mock all attempts failing
        mock_smtp.send_message.side_effect = smtplib.SMTPException("Permanent error")

        notifier = EmailNotifier()

        with patch("src.lib.feedback.email_notifier.logger") as mock_logger:
            try:
                notifier.send_feedback_notification(sample_feedback_report)
            except Exception:
                pass

            # Should log error (format string + args with %s pattern)
            mock_logger.error.assert_called()
            call_args = mock_logger.error.call_args[0]
            assert "feedback_123" in str(call_args)
            assert "failed" in call_args[0].lower()

    def test_send_feedback_notification_includes_langfuse_link(
        self, mock_smtp, mock_config, sample_feedback_report
    ):
        """Test that email includes link to Langfuse trace.

        VERIFY: This test should FAIL initially (EmailNotifier doesn't exist yet).
        """
        from src.lib.feedback.email_notifier import EmailNotifier

        notifier = EmailNotifier()
        notifier.send_feedback_notification(sample_feedback_report)

        # Get email body
        sent_message = mock_smtp.send_message.call_args[0][0]
        body = str(sent_message.get_payload())

        # Should include Langfuse link
        assert "localhost:3000" in body
        assert "session_456" in body or "traces" in body.lower()
