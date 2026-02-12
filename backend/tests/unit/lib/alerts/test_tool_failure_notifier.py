"""Unit tests for tool failure SNS notifier."""

import asyncio
from unittest.mock import MagicMock

import pytest

from src.lib.alerts import tool_failure_notifier as notifier


@pytest.fixture
def direct_to_thread(monkeypatch):
    """Run asyncio.to_thread work inline for deterministic tests."""

    async def _direct_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(notifier.asyncio, "to_thread", _direct_to_thread)


def test_notify_tool_failure_publishes_expected_payload(monkeypatch, direct_to_thread):
    """Publishes SNS notification with expected subject/body/attributes."""
    mock_client = MagicMock()
    mock_client.publish.return_value = {"MessageId": "msg-123"}
    monkeypatch.setattr(notifier.boto3, "client", lambda *_args, **_kwargs: mock_client)

    monkeypatch.setenv("TOOL_FAILURE_ALERTS_ENABLED", "true")
    monkeypatch.setenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:test")
    monkeypatch.setenv("SNS_REGION", "us-east-1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_URL", "https://langfuse.alliancegenome.org")

    result = asyncio.run(
        notifier.notify_tool_failure(
            error_type="TimeoutError",
            error_message="TraceReview service timeout (30s exceeded)",
            source="opus_report",
            specialist_name="get_trace_summary",
            trace_id="abc-123-def",
            session_id="session-456",
            curator_id="curator@example.com",
        )
    )

    assert result is True
    mock_client.publish.assert_called_once()
    call = mock_client.publish.call_args.kwargs

    assert call["TopicArn"] == "arn:aws:sns:us-east-1:123456789012:test"
    assert call["MessageAttributes"]["type"]["DataType"] == "String"
    assert call["MessageAttributes"]["type"]["StringValue"] == "tool_failure"
    assert "[Tool Failure] TimeoutError: get_trace_summary" in call["Subject"]

    message = call["Message"]
    assert "Source:         opus_report (Claude detected and reported)" in message
    assert "Error Type:     TimeoutError" in message
    assert "Error Message:  TraceReview service timeout (30s exceeded)" in message
    assert "Tool:           get_trace_summary" in message
    assert "Trace ID:       abc-123-def" in message
    assert "Session ID:     session-456" in message
    assert "Curator:        curator@example.com" in message
    assert "Timestamp:      " in message
    assert "View trace: https://langfuse.alliancegenome.org/trace/abc-123-def" in message


def test_notify_tool_failure_respects_feature_flag(monkeypatch, direct_to_thread):
    """Does nothing when TOOL_FAILURE_ALERTS_ENABLED is false."""
    mock_client = MagicMock()
    monkeypatch.setattr(notifier.boto3, "client", lambda *_args, **_kwargs: mock_client)

    monkeypatch.setenv("TOOL_FAILURE_ALERTS_ENABLED", "false")
    monkeypatch.setenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:test")

    result = asyncio.run(
        notifier.notify_tool_failure(
            error_type="ConnectionError",
            error_message="Connection refused",
            source="infrastructure",
            specialist_name="get_trace_summary",
            trace_id="trace-1",
            session_id="session-1",
            curator_id="curator@example.com",
        )
    )

    assert result is False
    mock_client.publish.assert_not_called()


def test_notify_tool_failure_returns_false_when_arn_missing(monkeypatch, direct_to_thread):
    """Returns False and does not call SNS when enabled but ARN is not set."""
    mock_client = MagicMock()
    monkeypatch.setattr(notifier.boto3, "client", lambda *_args, **_kwargs: mock_client)

    monkeypatch.setenv("TOOL_FAILURE_ALERTS_ENABLED", "true")
    monkeypatch.delenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", raising=False)

    result = asyncio.run(
        notifier.notify_tool_failure(
            error_type="TimeoutError",
            error_message="timed out",
            source="infrastructure",
            specialist_name="get_trace_summary",
            trace_id="trace-1",
            session_id="session-1",
            curator_id="curator@example.com",
        )
    )

    assert result is False
    mock_client.publish.assert_not_called()


def test_notify_tool_failure_does_not_raise_on_sns_failure(monkeypatch, direct_to_thread):
    """Logs and returns false if SNS publish fails, without raising."""
    mock_client = MagicMock()
    mock_client.publish.side_effect = RuntimeError("SNS unavailable")
    monkeypatch.setattr(notifier.boto3, "client", lambda *_args, **_kwargs: mock_client)

    monkeypatch.setenv("TOOL_FAILURE_ALERTS_ENABLED", "true")
    monkeypatch.setenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:test")

    result = asyncio.run(
        notifier.notify_tool_failure(
            error_type="RuntimeError",
            error_message="boom",
            source="infrastructure",
            specialist_name="gene_expression",
            trace_id="trace-2",
            session_id="session-2",
            curator_id="curator@example.com",
        )
    )

    assert result is False
