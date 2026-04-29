"""Unit tests for FeedbackService orchestration logic."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
import importlib

import pytest
from sqlalchemy.exc import SQLAlchemyError

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
        trace_data=None,
        conversation_transcript=None,
        processing_status="pending",
        created_at=datetime(2026, 4, 25, 12, 0, 0),
        processing_started_at=None,
        processing_completed_at=None,
        email_sent_at=None,
        error_details=None,
    )


def _get_debug_detail(
    service,
    feedback_id="feedback-1",
    *,
    user_auth_sub="auth-sub-1",
    authenticated_curator_email="curator@example.org",
    allow_admin_debug_access=False,
):
    return service.get_feedback_debug_detail(
        feedback_id,
        user_auth_sub=user_auth_sub,
        authenticated_curator_email=authenticated_curator_email,
        allow_admin_debug_access=allow_admin_debug_access,
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
    service._maybe_capture_conversation_transcript = MagicMock(
        return_value={"messages": [{"role": "user", "content": "hello"}]}
    )
    feedback_id = service.create_feedback_payload(
        session_id="session-1",
        curator_id="curator@example.org",
        feedback_text="Looks good",
        trace_ids=["trace-1", "trace-2"],
        user_auth_sub="auth-sub-1",
        authenticated_curator_email="curator@example.org",
    )

    assert feedback_id == "uuid-123"
    db.add.assert_called_once()
    db.commit.assert_called_once()
    report = db.add.call_args[0][0]
    assert report.id == "uuid-123"
    assert _status_value(report.processing_status) == "pending"
    assert report.trace_ids == ["trace-1", "trace-2"]
    assert report.conversation_transcript == {"messages": [{"role": "user", "content": "hello"}]}


def test_create_feedback_payload_looks_up_transcript_when_curator_id_matches_authenticated_email(
    monkeypatch,
):
    db = MagicMock()
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    monkeypatch.setattr("src.lib.feedback.service.uuid.uuid4", lambda: "uuid-123")
    capture = MagicMock(return_value={"messages": [{"role": "user", "content": "hello"}]})
    monkeypatch.setattr("src.lib.feedback.service.capture_feedback_conversation_transcript", capture)

    service = _feedback_service_module().FeedbackService(db=db)
    feedback_id = service.create_feedback_payload(
        session_id="session-1",
        curator_id="curator@example.org",
        feedback_text="Looks good",
        trace_ids=["trace-1", "trace-2"],
        user_auth_sub="auth-sub-1",
        authenticated_curator_email="curator@example.org",
    )

    assert feedback_id == "uuid-123"
    capture.assert_called_once()
    report = db.add.call_args[0][0]
    assert report.conversation_transcript == {"messages": [{"role": "user", "content": "hello"}]}


def test_create_feedback_payload_skips_lookup_when_curator_id_does_not_match_auth_user(monkeypatch):
    db = MagicMock()
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    monkeypatch.setattr("src.lib.feedback.service.uuid.uuid4", lambda: "uuid-123")
    capture = MagicMock(return_value={"messages": [{"role": "user", "content": "hello"}]})
    monkeypatch.setattr("src.lib.feedback.service.capture_feedback_conversation_transcript", capture)

    service = _feedback_service_module().FeedbackService(db=db)
    feedback_id = service.create_feedback_payload(
        session_id="session-1",
        curator_id="other-curator@example.org",
        feedback_text="Looks good",
        trace_ids=["trace-1", "trace-2"],
        user_auth_sub="auth-sub-1",
        authenticated_curator_email="curator@example.org",
    )

    assert feedback_id == "uuid-123"
    capture.assert_not_called()
    report = db.add.call_args[0][0]
    assert report.conversation_transcript is None


def test_create_feedback_payload_logs_and_continues_when_transcript_lookup_fails(monkeypatch):
    db = MagicMock()
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    monkeypatch.setattr("src.lib.feedback.service.uuid.uuid4", lambda: "uuid-123")
    monkeypatch.setattr(
        "src.lib.feedback.service.capture_feedback_conversation_transcript",
        MagicMock(side_effect=SQLAlchemyError("db unavailable")),
    )

    service = _feedback_service_module().FeedbackService(db=db)
    feedback_id = service.create_feedback_payload(
        session_id="session-1",
        curator_id="curator@example.org",
        feedback_text="Looks good",
        trace_ids=["trace-1"],
        user_auth_sub="auth-sub-1",
        authenticated_curator_email="curator@example.org",
    )

    assert feedback_id == "uuid-123"
    report = db.add.call_args[0][0]
    assert report.conversation_transcript is None
    db.rollback.assert_called_once()
    db.commit.assert_called_once()


def test_create_feedback_payload_propagates_unexpected_transcript_errors(monkeypatch):
    db = MagicMock()
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    monkeypatch.setattr("src.lib.feedback.service.uuid.uuid4", lambda: "uuid-123")
    monkeypatch.setattr(
        "src.lib.feedback.service.capture_feedback_conversation_transcript",
        MagicMock(side_effect=RuntimeError("unexpected bug")),
    )

    service = _feedback_service_module().FeedbackService(db=db)

    try:
        service.create_feedback_payload(
            session_id="session-1",
            curator_id="curator@example.org",
            feedback_text="Looks good",
            trace_ids=["trace-1"],
            user_auth_sub="auth-sub-1",
            authenticated_curator_email="curator@example.org",
        )
    except RuntimeError as exc:
        assert str(exc) == "unexpected bug"
    else:  # pragma: no cover - defensive failure branch
        raise AssertionError("Expected unexpected transcript errors to propagate")

    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_create_feedback_payload_logs_agent_studio_transcript_but_stores_it(monkeypatch):
    db = MagicMock()
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    monkeypatch.setattr("src.lib.feedback.service.uuid.uuid4", lambda: "uuid-123")
    monkeypatch.setattr(
        "src.lib.feedback.service.capture_feedback_conversation_transcript",
        MagicMock(
            return_value={
                "session": {"chat_kind": "agent_studio"},
                "messages": [{"role": "user", "content": "hello"}],
            }
        ),
    )

    service = _feedback_service_module().FeedbackService(db=db)
    feedback_id = service.create_feedback_payload(
        session_id="session-1",
        curator_id="auth-sub-1",
        feedback_text="Looks good",
        trace_ids=["trace-1"],
        user_auth_sub="auth-sub-1",
        authenticated_curator_email="curator@example.org",
    )

    assert feedback_id == "uuid-123"
    report = db.add.call_args[0][0]
    assert report.conversation_transcript == {
        "session": {"chat_kind": "agent_studio"},
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_curator_matching_accepts_authenticated_email_or_sub_only():
    service_module = _feedback_service_module()

    assert service_module.FeedbackService._curator_matches_authenticated_user(
        curator_id="AUTH-SUB-1",
        user_auth_sub="auth-sub-1",
        authenticated_curator_email="curator@example.org",
    ) is False
    assert service_module.FeedbackService._curator_matches_authenticated_user(
        curator_id="curator@example.org",
        user_auth_sub="opaque-auth-sub",
        authenticated_curator_email="curator@example.org",
    ) is True
    assert service_module.FeedbackService._curator_matches_authenticated_user(
        curator_id="Curator@Example.org",
        user_auth_sub="opaque-auth-sub",
        authenticated_curator_email="curator@example.org",
    ) is True
    assert service_module.FeedbackService._curator_matches_authenticated_user(
        curator_id="auth-sub-1",
        user_auth_sub="auth-sub-1",
        authenticated_curator_email="curator@example.org",
    ) is True
    assert service_module.FeedbackService._curator_matches_authenticated_user(
        curator_id="curator@example.org",
        user_auth_sub="opaque-auth-sub",
        authenticated_curator_email="different@example.org",
    ) is False


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
    service._capture_feedback_trace_snapshot = MagicMock(return_value=None)

    service.process_feedback_report(report.id)

    service.notifier.send_feedback_notification.assert_called_once_with(report)
    service._capture_feedback_trace_snapshot.assert_called_once_with(report)
    assert _status_value(report.processing_status) == "completed"
    assert report.processing_started_at is not None
    assert report.email_sent_at is not None
    assert report.processing_completed_at is not None
    assert report.trace_data is None
    assert report.error_details is None
    assert db.commit.call_count == 2


def test_process_feedback_report_persists_redacted_trace_snapshot(monkeypatch):
    now = datetime(2026, 4, 25, 12, 0, 0)
    trace_context = SimpleNamespace(
        trace_id="trace-1",
        session_id="session-1",
        timestamp=now,
        user_query="Please help curator@example.org with token=fixture-value",
        final_response_preview="See https://example.org/private?debug=fixture for details",
        prompts_executed=[
            SimpleNamespace(
                agent_id="gene_extractor",
                agent_name="Gene Extractor",
                prompt_preview="system prompt preview not stored",
                group_applied="WB",
                model="gpt-test",
                tokens_used=123,
            )
        ],
        routing_decisions=[
            SimpleNamespace(
                from_agent="supervisor",
                to_agent="gene_extractor",
                reason="not stored",
                timestamp=now + timedelta(seconds=1),
            )
        ],
        tool_calls=[
            SimpleNamespace(
                name="search_document",
                input={"api_key": "do-not-store"},
                output_preview="private output",
                duration_ms=42,
                status="completed",
            )
        ],
        total_duration_ms=1000,
        total_tokens=456,
        agent_count=1,
    )

    async def _get_trace_context(_trace_id):
        return trace_context

    report = _report()
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    monkeypatch.setattr(
        "src.lib.feedback.service.get_trace_context_for_explorer",
        _get_trace_context,
    )
    service = _feedback_service_module().FeedbackService(db=db)
    service.notifier = MagicMock()

    service.process_feedback_report(report.id)

    assert _status_value(report.processing_status) == "completed"
    assert report.trace_data["capture_status"] == "success"
    assert report.trace_data["feedback"] == {
        "session_id": "session-1",
        "trace_ids": ["trace-1"],
    }
    trace_snapshot = report.trace_data["traces"][0]
    assert trace_snapshot["session_matches_feedback"] is True
    assert trace_snapshot["metrics"]["tool_call_count"] == 1
    assert trace_snapshot["previews"]["user_query"] == (
        "Please help [redacted-email] with token=[redacted]"
    )
    assert trace_snapshot["previews"]["final_response"] == "See [redacted-url] for details"
    assert trace_snapshot["prompts_executed"] == [
        {
            "agent_id": "gene_extractor",
            "agent_name": "Gene Extractor",
            "group_applied": "WB",
            "model": "gpt-test",
            "tokens_used": 123,
        }
    ]
    assert trace_snapshot["tool_calls"] == [
        {
            "name": "search_document",
            "duration_ms": 42,
            "status": "completed",
        }
    ]
    assert "prompt_preview" not in trace_snapshot["prompts_executed"][0]
    assert "input" not in trace_snapshot["tool_calls"][0]
    assert "output_preview" not in trace_snapshot["tool_calls"][0]


def test_process_feedback_report_persists_trace_capture_failure_metadata(monkeypatch):
    async def _raise_trace_context(_trace_id):
        raise RuntimeError(
            "Langfuse unavailable for curator@example.org with api_key=fixture-value"
        )

    report = _report()
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    monkeypatch.setattr(
        "src.lib.feedback.service.get_trace_context_for_explorer",
        _raise_trace_context,
    )
    service = _feedback_service_module().FeedbackService(db=db)
    service.notifier = MagicMock()

    service.process_feedback_report(report.id)

    service.notifier.send_feedback_notification.assert_called_once_with(report)
    assert _status_value(report.processing_status) == "completed"
    assert report.error_details == (
        "Trace capture completed with errors. See trace_data.error_summary for details."
    )
    assert report.trace_data["capture_status"] == "error"
    assert report.trace_data["error_summary"]["trace_error_count"] == 1
    trace_error = report.trace_data["traces"][0]["error"]
    assert trace_error["type"] == "RuntimeError"
    assert trace_error["message"] == (
        "Langfuse unavailable for [redacted-email] with api_key=[redacted]"
    )
    assert "fixture-value" not in str(report.trace_data)
    assert "curator@example.org" not in str(report.trace_data)


def test_process_feedback_report_handles_notifier_failure(monkeypatch):
    report = _report()
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)
    service.notifier = MagicMock()
    service.notifier.send_feedback_notification.side_effect = RuntimeError("smtp down")
    service._capture_feedback_trace_snapshot = MagicMock(
        return_value={"schema_version": 1, "capture_status": "success"}
    )

    service.process_feedback_report(report.id)

    assert _status_value(report.processing_status) == "completed"
    assert report.trace_data == {"schema_version": 1, "capture_status": "success"}
    assert report.email_sent_at is None
    assert report.processing_completed_at is not None
    assert "Notification error: smtp down" in report.error_details
    assert db.commit.call_count == 2


def test_process_feedback_report_appends_notification_error_to_trace_capture_error(monkeypatch):
    report = _report()
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)
    service.notifier = MagicMock()
    service.notifier.send_feedback_notification.side_effect = RuntimeError("smtp down")
    service._capture_feedback_trace_snapshot = MagicMock(
        side_effect=RuntimeError("langfuse down")
    )

    service.process_feedback_report(report.id)

    assert _status_value(report.processing_status) == "completed"
    assert report.trace_data["capture_status"] == "error"
    assert report.email_sent_at is None
    assert report.error_details == (
        "Trace capture failed: RuntimeError: langfuse down; "
        "Notification error: smtp down"
    )
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


def test_get_feedback_debug_detail_returns_redacted_summary(monkeypatch):
    report = _report()
    report.processing_status = "completed"
    report.processing_started_at = datetime(2026, 4, 25, 12, 0, 1)
    report.processing_completed_at = datetime(2026, 4, 25, 12, 0, 2)
    report.email_sent_at = datetime(2026, 4, 25, 12, 0, 2)
    report.error_details = "Notification error for curator@example.org token=fixture-value"
    report.conversation_transcript = {
        "captured_at": "2026-04-25T12:00:00+00:00",
        "message_count": 2,
        "session": {
            "session_id": "session-1",
            "chat_kind": "assistant_chat",
            "title": "Stored title",
            "effective_title": "Stored title",
        },
        "messages": [
            {"role": "user", "content": "raw transcript is not returned"},
            {"role": "assistant", "content": "raw transcript is not returned"},
        ],
    }
    report.trace_data = {
        "schema_version": 1,
        "capture_status": "partial",
        "captured_at": "2026-04-25T12:00:01Z",
        "source": {
            "kind": "langfuse",
            "extractor": "src.lib.agent_studio.trace_context_service.get_trace_context_for_explorer",
        },
        "feedback": {
            "session_id": "session-1",
            "trace_ids": ["trace-1"],
        },
        "traces": [
            {
                "trace_id": "trace-1",
                "capture_status": "error",
                "error": {
                    "type": "RuntimeError",
                    "message": "Langfuse failed for curator@example.org api_key=fixture-value",
                },
                "raw_trace": {
                    "authorization": "redacted authorization fixture",
                },
            },
        ],
        "error_summary": {
            "trace_error_count": 1,
            "message": "Trace failed for curator@example.org token=fixture-value",
            "raw_payload": "not returned",
        },
    }
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")

    service = _feedback_service_module().FeedbackService(db=db)
    detail = _get_debug_detail(service, report.id)

    assert detail["feedback_id"] == "feedback-1"
    assert detail["feedback_debug_url"] == "/api/feedback/feedback-1/debug"
    assert detail["trace_review_session_url"] == (
        "/api/traces/sessions/session-1/export?source=remote"
    )
    assert detail["processing_error"] == (
        "Notification error for [redacted-email] token=[redacted]"
    )
    assert detail["transcript"] == {
        "available": True,
        "message_count": 2,
        "captured_at": "2026-04-25T12:00:00+00:00",
        "session_id": "session-1",
        "chat_kind": "assistant_chat",
        "title": "Stored title",
        "effective_title": "Stored title",
        "session_matches_feedback": True,
    }
    assert detail["trace_data"]["status"] == "partial"
    assert detail["trace_data"]["stale"] is False
    assert detail["trace_data"]["omitted_trace_id_count"] is None
    assert detail["trace_data"]["error_summary"] == {
        "trace_error_count": 1,
        "message": "Trace failed for [redacted-email] token=[redacted]",
    }
    assert detail["trace_data"]["errors"] == [
        {
            "trace_id": "trace-1",
            "type": "RuntimeError",
            "message": "Langfuse failed for [redacted-email] api_key=[redacted]",
        }
    ]
    assert "raw_trace" not in str(detail)
    assert "raw transcript is not returned" not in str(detail)
    assert "fixture-value" not in str(detail)
    assert "curator@example.org" not in detail["processing_error"]
    assert "curator@example.org" not in str(detail["trace_data"])


def test_get_feedback_debug_detail_returns_none_when_not_found(monkeypatch):
    db = MagicMock()
    db.query.return_value = _QueryChain(None)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)

    detail = _get_debug_detail(service, "missing-feedback")

    assert detail is None


def test_get_feedback_debug_detail_denies_cross_curator(monkeypatch):
    service_module = _feedback_service_module()
    report = _report()
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = service_module.FeedbackService(db=db)

    with pytest.raises(service_module.FeedbackDebugDetailForbidden):
        _get_debug_detail(
            service,
            report.id,
            user_auth_sub="other-auth-sub",
            authenticated_curator_email="other@example.org",
        )


def test_get_feedback_debug_detail_allows_admin_cross_curator(monkeypatch):
    report = _report()
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)

    detail = _get_debug_detail(
        service,
        report.id,
        user_auth_sub="admin-auth-sub",
        authenticated_curator_email="admin@example.org",
        allow_admin_debug_access=True,
    )

    assert detail["feedback_id"] == report.id
    assert detail["curator_id"] == "curator@example.org"


def test_get_feedback_debug_detail_marks_missing_and_stale_trace_data(monkeypatch):
    db = MagicMock()
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)

    missing_report = _report("missing-feedback")
    db.query.return_value = _QueryChain(missing_report)
    missing_detail = _get_debug_detail(service, "missing-feedback")

    assert missing_detail["trace_data"]["available"] is False
    assert missing_detail["trace_data"]["status"] == "missing"
    assert missing_detail["trace_data"]["expected_trace_ids"] == ["trace-1"]
    assert missing_detail["trace_data"]["omitted_trace_id_count"] is None

    stale_report = _report("stale-feedback")
    stale_report.trace_data = {
        "schema_version": 1,
        "capture_status": "success",
        "feedback": {
            "session_id": "old-session",
            "trace_ids": ["old-trace"],
        },
        "traces": [],
    }
    db.query.return_value = _QueryChain(stale_report)
    stale_detail = _get_debug_detail(service, "stale-feedback")

    assert stale_detail["trace_data"]["available"] is True
    assert stale_detail["trace_data"]["status"] == "stale"
    assert stale_detail["trace_data"]["stale"] is True
    assert stale_detail["trace_data"]["capture_status"] == "success"
    assert stale_detail["trace_data"]["stored_trace_ids"] == ["old-trace"]


def test_get_feedback_debug_detail_marks_missing_capture_status(monkeypatch):
    report = _report()
    report.trace_data = {
        "schema_version": 1,
        "feedback": {
            "session_id": "session-1",
            "trace_ids": ["trace-1"],
        },
        "traces": [],
    }
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)

    detail = _get_debug_detail(service, report.id)

    assert detail["trace_data"]["available"] is True
    assert detail["trace_data"]["status"] == "capture_status_missing"
    assert detail["trace_data"]["capture_status"] is None


def test_get_feedback_debug_detail_surfaces_corrupt_numeric_metadata(monkeypatch):
    report = _report()
    report.trace_data = {
        "schema_version": "not-an-integer",
        "capture_status": "success",
        "feedback": {
            "session_id": "session-1",
            "trace_ids": ["trace-1"],
        },
        "traces": [],
    }
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)

    with pytest.raises(ValueError):
        _get_debug_detail(service, report.id)


def test_get_feedback_debug_detail_surfaces_wrong_trace_data_shapes(monkeypatch):
    report = _report()
    report.trace_data = {
        "schema_version": 1,
        "capture_status": "success",
        "feedback": "not-an-object",
        "traces": [],
    }
    db = MagicMock()
    db.query.return_value = _QueryChain(report)
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    service = _feedback_service_module().FeedbackService(db=db)

    with pytest.raises(TypeError):
        _get_debug_detail(service, report.id)
