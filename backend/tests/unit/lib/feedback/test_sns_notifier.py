"""Unit tests for SNS feedback notifier."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.lib.feedback import sns_notifier


def test_init_creates_sns_client(monkeypatch):
    fake_client = MagicMock()
    monkeypatch.setattr(sns_notifier.boto3, "client", lambda service, region_name: fake_client)

    notifier = sns_notifier.SNSNotifier(
        topic_arn="arn:aws:sns:us-east-1:123456789012:feedback",
        region="us-west-2",
    )

    assert notifier.topic_arn.endswith(":feedback")
    assert notifier.region == "us-west-2"
    assert notifier.sns_client is fake_client


def test_build_email_body_includes_trace_ids(monkeypatch):
    fake_client = MagicMock()
    monkeypatch.setattr(sns_notifier.boto3, "client", lambda *_args, **_kwargs: fake_client)
    notifier = sns_notifier.SNSNotifier("arn:aws:sns:us-east-1:123:feedback")
    monkeypatch.setattr(notifier, "_get_timestamp", lambda: "2026-02-27 10:00:00 UTC")

    body = notifier._build_email_body(
        feedback_id="feedback-1",
        curator_id="curator@example.org",
        feedback_text="Looks good",
        session_id="session-1",
        trace_ids=["trace-a", "trace-b"],
    )

    assert "Feedback ID: feedback-1" in body
    assert "Curator: curator@example.org" in body
    assert "Submitted: 2026-02-27 10:00:00 UTC" in body
    assert "Associated Trace IDs:" in body
    assert "trace-a" in body and "trace-b" in body


def test_send_feedback_notification_raises_when_send_fails(monkeypatch):
    fake_client = MagicMock()
    monkeypatch.setattr(sns_notifier.boto3, "client", lambda *_args, **_kwargs: fake_client)
    notifier = sns_notifier.SNSNotifier("arn:aws:sns:us-east-1:123:feedback")
    monkeypatch.setattr(notifier, "_send_sns", lambda **_kwargs: False)

    report = SimpleNamespace(
        id="feedback-1",
        curator_id="curator@example.org",
        feedback_text="Looks good",
        session_id="session-1",
        trace_ids=None,
    )

    with pytest.raises(Exception, match="Failed to send SNS notification"):
        notifier.send_feedback_notification(report)


def test_send_sns_returns_true_on_publish_success(monkeypatch):
    fake_client = MagicMock()
    fake_client.publish.return_value = {"MessageId": "msg-1"}
    monkeypatch.setattr(sns_notifier.boto3, "client", lambda *_args, **_kwargs: fake_client)
    notifier = sns_notifier.SNSNotifier("arn:aws:sns:us-east-1:123:feedback")
    monkeypatch.setattr(notifier, "_get_timestamp", lambda: "2026-02-27 10:00:00 UTC")

    ok = notifier._send_sns(
        feedback_id="feedback-1",
        curator_id="curator@example.org",
        feedback_text="Looks good",
        session_id="session-1",
        trace_ids=[],
    )

    assert ok is True
    fake_client.publish.assert_called_once()
    kwargs = fake_client.publish.call_args.kwargs
    assert kwargs["TopicArn"].endswith(":feedback")
    assert kwargs["Subject"] == "New Curator Feedback: feedback"
    assert kwargs["MessageAttributes"]["feedback_id"]["StringValue"] == "feedback-1"


def test_send_sns_returns_false_on_client_error(monkeypatch):
    fake_client = MagicMock()
    fake_client.publish.side_effect = ClientError(
        error_response={"Error": {"Code": "InternalError", "Message": "boom"}},
        operation_name="Publish",
    )
    monkeypatch.setattr(sns_notifier.boto3, "client", lambda *_args, **_kwargs: fake_client)
    notifier = sns_notifier.SNSNotifier("arn:aws:sns:us-east-1:123:feedback")

    ok = notifier._send_sns(
        feedback_id="feedback-1",
        curator_id="curator@example.org",
        feedback_text="Looks good",
        session_id="session-1",
        trace_ids=[],
    )

    assert ok is False


def test_send_sns_returns_false_on_unexpected_exception(monkeypatch):
    fake_client = MagicMock()
    fake_client.publish.side_effect = RuntimeError("network down")
    monkeypatch.setattr(sns_notifier.boto3, "client", lambda *_args, **_kwargs: fake_client)
    notifier = sns_notifier.SNSNotifier("arn:aws:sns:us-east-1:123:feedback")

    ok = notifier._send_sns(
        feedback_id="feedback-1",
        curator_id="curator@example.org",
        feedback_text="Looks good",
        session_id="session-1",
        trace_ids=[],
    )

    assert ok is False

