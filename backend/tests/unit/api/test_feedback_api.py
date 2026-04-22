"""Unit tests for feedback submission API."""

from fastapi import BackgroundTasks
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


def test_submit_feedback_success_and_background_task(monkeypatch):
    calls = {"process_called": False}

    class _FakeService:
        def __init__(self, _db):
            pass

        def create_feedback_payload(self, **kwargs):
            calls["create_feedback_kwargs"] = kwargs
            return "feedback-123"

        def process_feedback_report(self, feedback_id):
            assert feedback_id == "feedback-123"
            calls["process_called"] = True

    class _FakeBgDb:
        def close(self):
            calls["bg_closed"] = True

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)

    import src.models.sql.database as db_module
    monkeypatch.setattr(db_module, "FeedbackSessionLocal", lambda: _FakeBgDb())

    background_tasks = BackgroundTasks()
    response = feedback_api.submit_feedback(
        submission=_submission(),
        background_tasks=background_tasks,
        db=object(),
        user={"sub": "user-123"},
    )
    assert response.status == "success"
    assert response.feedback_id == "feedback-123"
    assert len(background_tasks.tasks) == 1
    assert calls["create_feedback_kwargs"]["user_auth_sub"] == "user-123"
    assert calls["create_feedback_kwargs"]["authenticated_user_email"] is None

    # Execute queued background task synchronously for verification.
    background_tasks.tasks[0].func()
    assert calls["process_called"] is True
    assert calls["bg_closed"] is True


def test_submit_feedback_returns_400_on_validation_error(monkeypatch):
    class _FakeService:
        def __init__(self, _db):
            pass

        def create_feedback_payload(self, **_kwargs):
            raise ValueError("feedback invalid")

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)

    response = feedback_api.submit_feedback(
        submission=_submission(),
        background_tasks=BackgroundTasks(),
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
        background_tasks=BackgroundTasks(),
        db=object(),
        user={"sub": "user-123"},
    )
    assert isinstance(response, JSONResponse)
    assert response.status_code == 500
    assert b"Failed to save feedback to database" in response.body


def test_submit_feedback_background_errors_are_swallowed(monkeypatch):
    class _FakeService:
        def __init__(self, _db):
            pass

        def create_feedback_payload(self, **_kwargs):
            return "feedback-456"

        def process_feedback_report(self, _feedback_id):
            raise RuntimeError("background failure")

    class _FakeBgDb:
        def close(self):
            return None

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)

    import src.models.sql.database as db_module
    monkeypatch.setattr(db_module, "FeedbackSessionLocal", lambda: _FakeBgDb())

    background_tasks = BackgroundTasks()
    response = feedback_api.submit_feedback(
        submission=_submission(),
        background_tasks=background_tasks,
        db=object(),
        user={"sub": "user-123"},
    )
    assert response.status == "success"
    assert len(background_tasks.tasks) == 1

    # Should not raise even if background processing fails.
    background_tasks.tasks[0].func()


def test_submit_feedback_uses_authenticated_email_for_curator_verification(monkeypatch):
    calls = {}

    class _FakeService:
        def __init__(self, _db):
            pass

        def create_feedback_payload(self, **kwargs):
            calls["kwargs"] = kwargs
            return "feedback-789"

        def process_feedback_report(self, _feedback_id):
            return None

    class _FakeBgDb:
        def close(self):
            return None

    monkeypatch.setattr(feedback_api, "FeedbackService", _FakeService)

    import src.models.sql.database as db_module
    monkeypatch.setattr(db_module, "FeedbackSessionLocal", lambda: _FakeBgDb())

    response = feedback_api.submit_feedback(
        submission=_submission(),
        background_tasks=BackgroundTasks(),
        db=object(),
        user={"sub": "user-123", "email": "curator@example.org"},
    )

    assert response.status == "success"
    assert calls["kwargs"]["authenticated_user_email"] == "curator@example.org"
