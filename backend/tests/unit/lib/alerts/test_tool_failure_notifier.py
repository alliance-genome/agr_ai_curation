"""Unit tests for tool failure Sentry and SNS notifications."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.lib.alerts import tool_failure_notifier as notifier
from src.lib.observability.sentry import _redact_event, hash_sentry_identifier


@pytest.fixture
def direct_to_thread(monkeypatch):
    """Run asyncio.to_thread work inline for deterministic tests."""

    async def _direct_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(notifier.asyncio, "to_thread", _direct_to_thread)


@pytest.fixture(autouse=True)
def no_sentry_sdk_by_default(monkeypatch):
    """Keep tests deterministic unless they explicitly install a fake Sentry SDK."""

    real_import = notifier.importlib.import_module

    def _import_without_sentry(name):
        if name == "sentry_sdk":
            raise ImportError(name)
        return real_import(name)

    monkeypatch.setattr(notifier.importlib, "import_module", _import_without_sentry)


@pytest.fixture
def fake_sentry(monkeypatch):
    """Install a fake sentry_sdk module for deterministic alert tests."""

    calls = {"messages": [], "tags": [], "contexts": [], "levels": []}

    class _Scope:
        def set_level(self, level):
            calls["levels"].append(level)

        def set_tag(self, key, value):
            calls["tags"].append((key, value))

        def set_context(self, key, value):
            calls["contexts"].append((key, value))

    class _ScopeManager:
        def __enter__(self):
            return _Scope()

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_sdk = SimpleNamespace(
        new_scope=lambda: _ScopeManager(),
        capture_message=lambda message, level=None: calls["messages"].append(
            {"message": message, "level": level}
        ),
    )

    def _fake_import(name):
        if name == "sentry_sdk":
            return fake_sdk
        raise ImportError(name)

    monkeypatch.setattr(notifier.importlib, "import_module", _fake_import)
    return calls


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
    assert "Source:         opus_report (AI Chat detected and reported)" in message
    assert "Error Type:     TimeoutError" in message
    assert "Error Message:  TraceReview service timeout (30s exceeded)" in message
    assert "Tool:           get_trace_summary" in message
    assert "Trace ID:       abc-123-def" in message
    assert "Session ID:     session-456" in message
    assert "Curator:        curator@example.com" in message
    assert "Timestamp:      " in message
    assert "View trace: https://langfuse.alliancegenome.org/trace/abc-123-def" in message


def test_notify_tool_failure_sns_previews_respect_configured_cap(
    monkeypatch,
    direct_to_thread,
):
    mock_client = MagicMock()
    mock_client.publish.return_value = {"MessageId": "msg-bounded"}
    monkeypatch.setattr(notifier.boto3, "client", lambda *_args, **_kwargs: mock_client)
    monkeypatch.setenv("TOOL_FAILURE_ALERTS_ENABLED", "true")
    monkeypatch.setenv(
        "PROMPT_SUGGESTIONS_SNS_TOPIC_ARN",
        "arn:aws:sns:us-east-1:123456789012:test",
    )
    monkeypatch.setenv("TOOL_FAILURE_ALERT_SUMMARY_MAX_CHARS", "7")

    result = asyncio.run(
        notifier.notify_tool_failure(
            error_type="RuntimeError",
            error_message="abcdefghij",
            source="infrastructure",
            specialist_name="gene_expression",
            trace_id="trace-1",
            session_id="session-1",
            curator_id="curator@example.com",
            context="klmnopqrst",
        )
    )

    assert result is True
    message = mock_client.publish.call_args.kwargs["Message"]
    assert "Error Message:  abcdefg..." in message
    assert "Context:        klmnopq..." in message


def test_notify_tool_failure_captures_sentry_when_sns_disabled(
    monkeypatch,
    direct_to_thread,
    fake_sentry,
):
    """Sentry capture is independent of the SNS feature flag."""
    mock_client = MagicMock()
    monkeypatch.setattr(notifier.boto3, "client", lambda *_args, **_kwargs: mock_client)
    monkeypatch.setenv("TOOL_FAILURE_ALERTS_ENABLED", "false")

    result = asyncio.run(
        notifier.notify_tool_failure(
            error_type="RuntimeError",
            error_message="raw curator detail that should not be in Sentry",
            source="infrastructure",
            specialist_name="gene_expression",
            trace_id="trace-1",
            session_id="session-1",
            curator_id="curator@example.com",
            context="prompt text should not be in Sentry",
        )
    )

    assert result is False
    mock_client.publish.assert_not_called()
    assert fake_sentry["messages"] == [
        {"message": "Tool failure: RuntimeError (gene_expression)", "level": "error"}
    ]
    sentry_text = repr(fake_sentry)
    assert "raw curator detail" not in sentry_text
    assert "prompt text" not in sentry_text
    assert ("alert_type", "tool_failure") in fake_sentry["tags"]
    assert ("ai_curation.trace.id_hash", hash_sentry_identifier("trace-1")) in fake_sentry["tags"]
    assert ("ai_curation.chat.session_id_hash", hash_sentry_identifier("session-1")) in fake_sentry["tags"]
    for raw_id in ("trace-1", "session-1", "curator@example.com"):
        assert raw_id not in sentry_text


@pytest.mark.parametrize(
    "trace_id,session_id",
    [("application-trace-123", "application-session-456"), (None, None), ("", "")],
)
def test_tool_failure_correlation_survives_final_redactor(
    monkeypatch, direct_to_thread, fake_sentry, trace_id, session_id,
):
    """Validate the emitted custom payload with the production final redactor."""
    monkeypatch.setenv("TOOL_FAILURE_ALERTS_ENABLED", "false")
    asyncio.run(
        notifier.notify_tool_failure(
            error_type="TimeoutError",
            error_message="private failure detail",
            source="infrastructure",
            specialist_name="gene_expression",
            trace_id=trace_id,
            session_id=session_id,
            curator_id="private-curator@example.com",
            context="private prompt detail",
        )
    )
    sdk_trace_id = "0123456789abcdef0123456789abcdef"
    event = _redact_event({
        "tags": dict(fake_sentry["tags"]),
        "contexts": {
            **dict(fake_sentry["contexts"]),
            "trace": {"trace_id": sdk_trace_id, "span_id": "0123456789abcdef"},
        },
        "message": fake_sentry["messages"][0]["message"],
    })
    for raw_value in (
        trace_id, session_id, "private-curator@example.com",
        "private failure detail", "private prompt detail",
    ):
        if raw_value:
            assert raw_value not in repr(event)
    for key, identifier in (
        ("ai_curation.trace.id_hash", trace_id),
        ("ai_curation.chat.session_id_hash", session_id),
    ):
        if identifier:
            assert event["tags"][key] == hash_sentry_identifier(identifier)
        else:
            assert key not in event["tags"]
    assert "trace_id" not in event["tags"]
    assert "session_id" not in event["tags"]
    assert event["tags"]["alert_type"] == "tool_failure"
    assert event["tags"]["source"] == "infrastructure"
    assert event["tags"]["tool_name"] == "gene_expression"
    assert event["tags"]["error_type"] == "TimeoutError"
    assert event["contexts"]["trace"]["trace_id"] == sdk_trace_id


def test_notify_tool_failure_can_preserve_sns_without_duplicate_sentry(
    monkeypatch,
    direct_to_thread,
    fake_sentry,
):
    """An owning runtime boundary may suppress only the notifier's Sentry copy."""

    mock_client = MagicMock()
    mock_client.publish.return_value = {"MessageId": "msg-owned"}
    monkeypatch.setattr(notifier.boto3, "client", lambda *_args, **_kwargs: mock_client)
    monkeypatch.setenv("TOOL_FAILURE_ALERTS_ENABLED", "true")
    monkeypatch.setenv(
        "PROMPT_SUGGESTIONS_SNS_TOPIC_ARN",
        "arn:aws:sns:us-east-1:123456789012:test",
    )

    result = asyncio.run(
        notifier.notify_tool_failure(
            error_type="SpecialistOutputError",
            error_message="specialist failed",
            source="infrastructure",
            specialist_name="gene_expression",
            trace_id="trace-owned",
            session_id="session-owned",
            curator_id="curator@example.com",
            capture_sentry=False,
        )
    )

    assert result is True
    mock_client.publish.assert_called_once()
    assert fake_sentry["messages"] == []
    assert fake_sentry["tags"] == []
    assert fake_sentry["contexts"] == []


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


def test_notify_tool_failure_sentry_failure_does_not_block_sns(monkeypatch, direct_to_thread):
    """A Sentry capture failure is logged and SNS still publishes."""
    mock_client = MagicMock()
    mock_client.publish.return_value = {"MessageId": "msg-456"}
    monkeypatch.setattr(notifier.boto3, "client", lambda *_args, **_kwargs: mock_client)

    class _BrokenScopeManager:
        def __enter__(self):
            raise RuntimeError("sentry unavailable")

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_sdk = SimpleNamespace(new_scope=lambda: _BrokenScopeManager())

    def _fake_import(name):
        if name == "sentry_sdk":
            return fake_sdk
        raise ImportError(name)

    monkeypatch.setattr(
        notifier.importlib,
        "import_module",
        _fake_import,
    )

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

    assert result is True
    mock_client.publish.assert_called_once()


def test_notify_tool_failure_sentry_import_failure_does_not_block_sns(
    monkeypatch,
    direct_to_thread,
):
    """A Sentry import-time failure is non-fatal and SNS still publishes."""
    mock_client = MagicMock()
    mock_client.publish.return_value = {"MessageId": "msg-789"}
    monkeypatch.setattr(notifier.boto3, "client", lambda *_args, **_kwargs: mock_client)

    def _broken_import(name):
        if name == "sentry_sdk":
            raise RuntimeError("broken sentry import")
        raise ImportError(name)

    monkeypatch.setattr(notifier.importlib, "import_module", _broken_import)
    monkeypatch.setenv("TOOL_FAILURE_ALERTS_ENABLED", "true")
    monkeypatch.setenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:test")

    result = asyncio.run(
        notifier.notify_tool_failure(
            error_type="RuntimeError",
            error_message="boom",
            source="infrastructure",
            specialist_name="gene_expression",
            trace_id="trace-3",
            session_id="session-3",
            curator_id="curator@example.com",
        )
    )

    assert result is True
    mock_client.publish.assert_called_once()


@pytest.mark.parametrize('mode,expected', [('disabled', None), ('unconfigured', 'tool_failure_sns_not_configured'), ('failed', 'tool_failure_sns_publish_failed')])
def test_notification_delivery_failure_has_separate_sanitized_capture(monkeypatch, direct_to_thread, mode, expected):
    captures = []
    monkeypatch.setattr(notifier, 'report_runtime_exception', lambda *a, **k: captures.append((a, k)))
    monkeypatch.setenv('TOOL_FAILURE_ALERTS_ENABLED', 'false' if mode == 'disabled' else 'true')
    monkeypatch.delenv('PROMPT_SUGGESTIONS_SNS_TOPIC_ARN', raising=False)
    if mode == 'failed':
        monkeypatch.setenv('PROMPT_SUGGESTIONS_SNS_TOPIC_ARN', 'arn:private')
        monkeypatch.delenv('AWS_PROFILE', raising=False)
        def fail(*a, **kw):
            raise RuntimeError('PRIVATE TRANSPORT CREDENTIAL')
        import boto3
        monkeypatch.setattr(boto3, 'client', fail)
    result = asyncio.run(notifier.notify_tool_failure(error_type='test', error_message='PRIVATE PAPER', source='test', specialist_name=None, trace_id=None, session_id=None, curator_id=None, capture_sentry=False))
    assert result is False
    assert len(captures) == (1 if expected else 0)
    if expected:
        assert captures[0][1]['operation'] == expected
    assert 'PRIVATE' not in str(captures)
