"""Sentry-only tool-failure capture contracts."""
import asyncio
from unittest.mock import MagicMock

import boto3
import pytest

from src.lib.alerts import tool_failure_notifier as notifier


@pytest.mark.parametrize("legacy_flag", ["true", "false"])
@pytest.mark.parametrize("mode", ["queued", "disabled", "dropped", "capture_error", "import_error", "owned"])
def test_sentry_capture_never_publishes_sns(monkeypatch, legacy_flag, mode):
    monkeypatch.setenv("TOOL_FAILURE_ALERTS_ENABLED", legacy_flag)
    monkeypatch.setenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", "old-topic-must-not-be-used")
    aws_client = MagicMock(side_effect=AssertionError("SNS must not be used"))
    monkeypatch.setattr(boto3, "client", aws_client)
    monkeypatch.setattr(boto3, "Session", aws_client)
    sdk = MagicMock()
    sdk.is_initialized.return_value = mode != "disabled"
    sdk.capture_message.return_value = None if mode == "dropped" else "event-id"
    if mode == "capture_error":
        sdk.capture_message.side_effect = RuntimeError("transport failed")
    def import_sdk(name):
        assert name == "sentry_sdk"
        if mode == "import_error":
            raise ImportError("SDK missing")
        return sdk
    monkeypatch.setattr(notifier.importlib, "import_module", import_sdk)
    result = asyncio.run(notifier.notify_tool_failure(
        error_type="TimeoutError", error_message="PRIVATE PAPER TEXT", source="infrastructure",
        specialist_name="extractor", trace_id="trace-1", session_id="session-1",
        curator_id="private@example.org", context="SECRET CONTEXT", capture_sentry=mode != "owned",
    ))
    assert result is (mode == "queued")
    aws_client.assert_not_called()
    if mode in {"disabled", "owned", "import_error"}:
        sdk.capture_message.assert_not_called()
    else:
        sdk.capture_message.assert_called_once()
        scope = sdk.new_scope.return_value.__enter__.return_value
        scope.set_tag.assert_any_call("alert_type", "tool_failure")
        scope.set_tag.assert_any_call("ai_curation.trace.id_hash", notifier.hash_sentry_identifier("trace-1"))
        recorded = str(sdk.mock_calls)
        for secret in ("PRIVATE PAPER TEXT", "SECRET CONTEXT", "private@example.org"):
            assert secret not in recorded
        context = scope.set_context.call_args.args[1]
        assert "curator_id" not in context
        assert context["session_id"] == notifier.hash_sentry_identifier("session-1")


def test_final_redactor_preserves_canonical_hashes_not_application_ids():
    from src.lib.observability.sentry import _redact_event
    event = _redact_event({
        "tags": {"trace_id": "raw-app-trace", "session_id": "raw-app-session",
                 "ai_curation.trace.id_hash": notifier.hash_sentry_identifier("raw-app-trace"),
                 "ai_curation.chat.session_id_hash": notifier.hash_sentry_identifier("raw-app-session")},
        "contexts": {"trace": {"trace_id": "a" * 32, "span_id": "b" * 16}},
    })
    assert "raw-app" not in str(event)
    assert event["tags"]["ai_curation.trace.id_hash"] == notifier.hash_sentry_identifier("raw-app-trace")
    assert event["tags"]["ai_curation.chat.session_id_hash"] == notifier.hash_sentry_identifier("raw-app-session")
    assert event["contexts"]["trace"]["trace_id"] == "a" * 32
