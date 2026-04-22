"""Unit tests for feedback submission API."""

import pytest
from fastapi.responses import JSONResponse

from src.api import feedback as feedback_api
from src.schemas.feedback import FeedbackSubmission


def _submission():
    return FeedbackSubmission(
        session_id="session-123",
        curator_id="curator@example.org",
        feedback_text="Great extraction, but one annotation was incorrect.",
        trace_ids=["trace-1"],
    )


def test_submit_feedback_success_dispatches_background_processing(monkeypatch):
    calls = {}

    class _FakeService:
        def __init__(self, _db):
            pass

        def create_feedback_payload(self, **kwargs):
            calls["create_feedback_kwargs"] = kwargs
            return "feedback-123"

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)
    monkeypatch.setattr(
        feedback_api,
        "dispatch_feedback_report_processing",
        lambda feedback_id: calls.setdefault("dispatched_feedback_id", feedback_id),
    )

    response = feedback_api.submit_feedback(
        submission=_submission(),
        db=object(),
        user={"sub": "user-123"},
    )
    assert response.status == "success"
    assert response.feedback_id == "feedback-123"
    assert calls["dispatched_feedback_id"] == "feedback-123"
    assert calls["create_feedback_kwargs"]["user_auth_sub"] == "user-123"


def test_submit_feedback_returns_400_on_validation_error(monkeypatch):
    class _FakeService:
        def __init__(self, _db):
            pass

        def create_feedback_payload(self, **_kwargs):
            raise ValueError("feedback invalid")

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)

    response = feedback_api.submit_feedback(
        submission=_submission(),
        db=object(),
        user={"sub": "user-123"},
    )
    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    assert b"Validation error" in response.body


def test_submit_feedback_returns_500_on_unexpected_error(monkeypatch):
    class _FakeService:
        def __init__(self, _db):
            pass

        def create_feedback_payload(self, **_kwargs):
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)

    response = feedback_api.submit_feedback(
        submission=_submission(),
        db=object(),
        user={"sub": "user-123"},
    )
    assert isinstance(response, JSONResponse)
    assert response.status_code == 500
    assert b"Failed to save feedback to database" in response.body


def test_submit_feedback_logs_dispatch_failures_but_returns_success(monkeypatch):
    class _FakeService:
        def __init__(self, _db):
            pass

        def create_feedback_payload(self, **_kwargs):
            return "feedback-123"

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)
    monkeypatch.setattr(
        feedback_api,
        "dispatch_feedback_report_processing",
        lambda _feedback_id: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
    )

    response = feedback_api.submit_feedback(
        submission=_submission(),
        db=object(),
        user={"sub": "user-123"},
    )

    assert response.status == "success"
    assert response.feedback_id == "feedback-123"


def test_submit_feedback_propagates_unexpected_dispatch_errors(monkeypatch):
    class _FakeService:
        def __init__(self, _db):
            pass

        def create_feedback_payload(self, **_kwargs):
            return "feedback-123"

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)
    monkeypatch.setattr(
        feedback_api,
        "dispatch_feedback_report_processing",
        lambda _feedback_id: (_ for _ in ()).throw(ValueError("unexpected failure")),
    )

    with pytest.raises(ValueError, match="unexpected failure"):
        feedback_api.submit_feedback(
            submission=_submission(),
            db=object(),
            user={"sub": "user-123"},
        )


def test_run_feedback_processing_in_background_uses_new_session(monkeypatch):
    calls = {"process_called": False}

    class _FakeService:
        def __init__(self, _db):
            pass

        def process_feedback_report(self, feedback_id):
            assert feedback_id == "feedback-456"
            calls["process_called"] = True

    class _FakeBgDb:
        def close(self):
            calls["bg_closed"] = True

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)

    import src.models.sql.database as db_module
    monkeypatch.setattr(db_module, "FeedbackSessionLocal", lambda: _FakeBgDb())

    feedback_api._run_feedback_processing_in_background("feedback-456")
    assert calls["process_called"] is True
    assert calls["bg_closed"] is True


def test_run_feedback_processing_in_background_swallows_errors(monkeypatch):
    calls = {}

    class _FakeService:
        def __init__(self, _db):
            pass

        def process_feedback_report(self, _feedback_id):
            raise RuntimeError("background failure")

    class _FakeBgDb:
        def close(self):
            calls["bg_closed"] = True

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)

    import src.models.sql.database as db_module
    monkeypatch.setattr(db_module, "FeedbackSessionLocal", lambda: _FakeBgDb())

    feedback_api._run_feedback_processing_in_background("feedback-456")
    assert calls["bg_closed"] is True


def test_dispatch_feedback_report_processing_starts_daemon_thread(monkeypatch):
    calls = {}

    class _FakeThread:
        def __init__(self, *, target, kwargs, name, daemon):
            calls["target"] = target
            calls["kwargs"] = kwargs
            calls["name"] = name
            calls["daemon"] = daemon

        def start(self):
            calls["started"] = True

    monkeypatch.setattr(feedback_api.threading, "Thread", _FakeThread)

    thread = feedback_api.dispatch_feedback_report_processing("feedback-456")

    assert isinstance(thread, _FakeThread)
    assert calls["target"] == feedback_api._run_feedback_processing_in_background
    assert calls["kwargs"] == {"feedback_id": "feedback-456"}
    assert calls["name"] == "feedback-report-feedback-456"
    assert calls["daemon"] is True
    assert calls["started"] is True


def test_submit_feedback_uses_authenticated_sub_for_authorization(monkeypatch):
    calls = {}

    class _FakeService:
        def __init__(self, _db):
            pass

        def create_feedback_payload(self, **kwargs):
            calls["kwargs"] = kwargs
            return "feedback-789"

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)
    monkeypatch.setattr(
        feedback_api,
        "dispatch_feedback_report_processing",
        lambda _feedback_id: None,
    )

    response = feedback_api.submit_feedback(
        submission=_submission(),
        db=object(),
        user={"sub": "user-123", "email": "curator@example.org"},
    )

    assert response.status == "success"
    assert calls["kwargs"]["user_auth_sub"] == "user-123"
