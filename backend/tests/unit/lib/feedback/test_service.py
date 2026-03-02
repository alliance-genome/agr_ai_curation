"""Unit tests for FeedbackService orchestration logic."""

from types import SimpleNamespace
from unittest.mock import MagicMock
import importlib

# Import sql.database first to avoid the package init circular path when importing service directly.
import src.models.sql.database  # noqa: F401


def _feedback_service_module():
    """Import feedback service lazily to avoid stale module references in full-suite runs."""
    return importlib.import_module("src.lib.feedback.service")


def _status_value(status):
    """Normalize enum-or-string processing status values for stable assertions."""
    return getattr(status, "value", status)


class _QueryChain:
    def __init__(self, report):
        self._report = report

    def filter(self, _expr):
        return self

    def first(self):
        return self._report


def _report(report_id="feedback-1"):
    return SimpleNamespace(
        id=report_id,
        session_id="session-1",
        curator_id="curator@example.org",
        feedback_text="feedback text",
        trace_ids=["trace-1"],
        processing_status="pending",
        processing_started_at=None,
        processing_completed_at=None,
        email_sent_at=None,
        error_details=None,
    )


def test_init_uses_email_notifier_by_default(monkeypatch):
    class _EmailNotifier:
        pass

    class _SNSNotifier:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("SNS should not be used by default")

    monkeypatch.delenv("FEEDBACK_USE_SNS", raising=False)
    monkeypatch.setattr("src.lib.feedback.service.EmailNotifier", _EmailNotifier)
    monkeypatch.setattr("src.lib.feedback.service.SNSNotifier", _SNSNotifier)

    service = _feedback_service_module().FeedbackService(db=MagicMock())
    assert isinstance(service.notifier, _EmailNotifier)


def test_init_falls_back_to_email_when_sns_topic_missing(monkeypatch):
    class _EmailNotifier:
        pass

    class _SNSNotifier:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("SNS should not be created without topic ARN")

    monkeypatch.setenv("FEEDBACK_USE_SNS", "true")
    monkeypatch.delenv("SNS_TOPIC_ARN", raising=False)
    monkeypatch.setattr("src.lib.feedback.service.EmailNotifier", _EmailNotifier)
    monkeypatch.setattr("src.lib.feedback.service.SNSNotifier", _SNSNotifier)

    service = _feedback_service_module().FeedbackService(db=MagicMock())
    assert isinstance(service.notifier, _EmailNotifier)


def test_init_uses_sns_when_enabled_and_configured(monkeypatch):
    class _EmailNotifier:
        pass

    class _SNSNotifier:
        def __init__(self, topic_arn, region):
            self.topic_arn = topic_arn
            self.region = region

    monkeypatch.setenv("FEEDBACK_USE_SNS", "true")
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:feedback")
    monkeypatch.setenv("SNS_REGION", "us-west-2")
    monkeypatch.setattr("src.lib.feedback.service.EmailNotifier", _EmailNotifier)
    monkeypatch.setattr("src.lib.feedback.service.SNSNotifier", _SNSNotifier)

    service = _feedback_service_module().FeedbackService(db=MagicMock())
    assert isinstance(service.notifier, _SNSNotifier)
    assert service.notifier.topic_arn.endswith(":feedback")
    assert service.notifier.region == "us-west-2"


def test_create_feedback_payload_persists_pending_report(monkeypatch):
    db = MagicMock()
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    monkeypatch.setattr("src.lib.feedback.service.uuid.uuid4", lambda: "uuid-123")

    service = _feedback_service_module().FeedbackService(db=db)
    feedback_id = service.create_feedback_payload(
        session_id="session-1",
        curator_id="curator@example.org",
        feedback_text="Looks good",
        trace_ids=["trace-1", "trace-2"],
    )

    assert feedback_id == "uuid-123"
    db.add.assert_called_once()
    db.commit.assert_called_once()
    report = db.add.call_args[0][0]
    assert report.id == "uuid-123"
    assert _status_value(report.processing_status) == "pending"
    assert report.trace_ids == ["trace-1", "trace-2"]


def test_process_feedback_report_returns_when_not_found(monkeypatch):
    db = MagicMock()
    db.query.return_value = _QueryChain(None)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)

    service.process_feedback_report("missing-id")

    db.commit.assert_not_called()


def test_process_feedback_report_marks_completed_on_success(monkeypatch):
    report = _report()
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)
    service.notifier = MagicMock()

    service.process_feedback_report(report.id)

    service.notifier.send_feedback_notification.assert_called_once_with(report)
    assert _status_value(report.processing_status) == "completed"
    assert report.processing_started_at is not None
    assert report.email_sent_at is not None
    assert report.processing_completed_at is not None
    assert report.error_details is None
    assert db.commit.call_count == 2


def test_process_feedback_report_handles_notifier_failure(monkeypatch):
    report = _report()
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)
    service.notifier = MagicMock()
    service.notifier.send_feedback_notification.side_effect = RuntimeError("smtp down")

    service.process_feedback_report(report.id)

    assert _status_value(report.processing_status) == "completed"
    assert report.email_sent_at is None
    assert report.processing_completed_at is not None
    assert "Notification error: smtp down" in report.error_details
    assert db.commit.call_count == 2


def test_process_feedback_report_marks_failed_on_unexpected_error(monkeypatch):
    report = _report()
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    db.commit.side_effect = [RuntimeError("db unavailable"), None]
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)
    service.notifier = MagicMock()

    service.process_feedback_report(report.id)

    assert _status_value(report.processing_status) == "failed"
    assert "Unexpected error: db unavailable" in report.error_details
    assert db.commit.call_count == 2
