"""Integration coverage for durable feedback transcript capture."""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

# Import database first to avoid the package init circular path when importing feedback models.
import src.models.sql.database  # noqa: F401
from src.lib.feedback.models import FeedbackReport, ProcessingStatus
from src.models.sql.database import SessionLocal


SESSION_PREFIX = "feedback-test-"


def _ensure_feedback_report_table_exists(db) -> None:
    FeedbackReport.__table__.create(bind=db.get_bind(), checkfirst=True)


def _cleanup_feedback_state() -> None:
    db = SessionLocal()
    try:
        _ensure_feedback_report_table_exists(db)
        db.execute(
            delete(FeedbackReport).where(FeedbackReport.session_id.like(f"{SESSION_PREFIX}%"))
        )
        db.commit()
    finally:
        db.close()


def _load_feedback_report(feedback_id: str) -> FeedbackReport:
    db = SessionLocal()
    try:
        _ensure_feedback_report_table_exists(db)
        report = db.query(FeedbackReport).filter(FeedbackReport.id == feedback_id).first()
        assert report is not None
        return report
    finally:
        db.close()


def _sample_transcript(session_id: str, *, chat_kind: str | None = None) -> dict:
    session_payload = {
        "session_id": session_id,
        "title": "Saved title",
    }
    if chat_kind is not None:
        session_payload["chat_kind"] = chat_kind

    return {
        "message_count": 8,
        "session": session_payload,
        "messages": [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
            {"role": "assistant", "content": "Second answer"},
            {"role": "user", "content": "Third question"},
            {"role": "assistant", "content": "Third answer"},
            {"role": "user", "content": "Fourth question"},
            {"role": "assistant", "content": "Fourth answer"},
        ],
    }


@pytest.fixture(autouse=True)
def cleanup_feedback_state():
    _cleanup_feedback_state()
    yield
    _cleanup_feedback_state()


@pytest.fixture
def client(get_auth_mock, monkeypatch):
    get_auth_mock.set_user("chat1")

    modules_to_clear = [
        name for name in list(sys.modules.keys())
        if name == "main" or name.startswith("src.")
    ]
    for module_name in modules_to_clear:
        del sys.modules[module_name]

    from main import app
    from src.api.auth import _get_user_from_cookie_impl
    from src.api import feedback as feedback_api

    app.dependency_overrides[_get_user_from_cookie_impl] = get_auth_mock.get_user
    monkeypatch.setattr(
        feedback_api,
        "dispatch_feedback_report_processing",
        lambda feedback_id: feedback_api._run_feedback_processing_in_background(feedback_id),
    )

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def captured_email_messages(monkeypatch):
    messages = []
    monkeypatch.setenv("FEEDBACK_USE_SNS", "false")
    monkeypatch.setattr(
        "src.lib.feedback.email_notifier.EmailNotifier._send_email",
        lambda _self, message: messages.append(message),
    )
    return messages


def test_feedback_submission_captures_transcript_and_includes_email_excerpt(
    client,
    curator1_user,
    captured_email_messages,
    monkeypatch,
):
    session_id = f"{SESSION_PREFIX}happy-path"
    monkeypatch.setattr(
        "src.lib.feedback.service.capture_feedback_conversation_transcript",
        lambda **_kwargs: _sample_transcript(session_id),
    )

    response = client.post(
        "/api/feedback/submit",
        json={
            "session_id": session_id,
            "curator_id": curator1_user.email,
            "feedback_text": "Transcript should be attached.",
            "trace_ids": ["trace-happy-1"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    report = _load_feedback_report(payload["feedback_id"])

    assert report.processing_status == ProcessingStatus.COMPLETED
    assert report.conversation_transcript == _sample_transcript(session_id)

    assert len(captured_email_messages) == 1
    body = str(captured_email_messages[0].get_payload())
    normalized_body = body.replace("=\n", "")
    assert "Conversation transcript excerpt:" in normalized_body
    assert f"Full durable transcript stored on feedback report {report.id}." in normalized_body
    assert "1. User: First question" in normalized_body
    assert "... 2 middle turns omitted ..." in normalized_body
    assert "8. Assistant: Fourth answer" in normalized_body


def test_feedback_submission_logs_lookup_failure_but_still_succeeds(
    client,
    curator1_user,
    captured_email_messages,
    monkeypatch,
):
    session_id = f"{SESSION_PREFIX}lookup-failure"

    def _raise_lookup_error(**_kwargs):
        raise RuntimeError("lookup failed")

    monkeypatch.setattr(
        "src.lib.feedback.service.capture_feedback_conversation_transcript",
        _raise_lookup_error,
    )

    response = client.post(
        "/api/feedback/submit",
        json={
            "session_id": session_id,
            "curator_id": curator1_user.email,
            "feedback_text": "Lookup failures should not block feedback.",
            "trace_ids": ["trace-failure-1"],
        },
    )

    assert response.status_code == 200
    report = _load_feedback_report(response.json()["feedback_id"])

    assert report.processing_status == ProcessingStatus.COMPLETED
    assert report.conversation_transcript is None
    assert len(captured_email_messages) == 1
    assert "Conversation transcript" not in str(captured_email_messages[0].get_payload())


def test_feedback_submission_skips_transcript_when_curator_id_does_not_match_auth_user(
    client,
    captured_email_messages,
    monkeypatch,
):
    session_id = f"{SESSION_PREFIX}mismatched-curator"
    mocked_capture = []

    def _capture(**_kwargs):
        mocked_capture.append("called")
        return _sample_transcript(session_id)

    monkeypatch.setattr(
        "src.lib.feedback.service.capture_feedback_conversation_transcript",
        _capture,
    )

    response = client.post(
        "/api/feedback/submit",
        json={
            "session_id": session_id,
            "curator_id": "someone-else@alliancegenome.org",
            "feedback_text": "Mismatched curator_id should skip transcript capture.",
            "trace_ids": ["trace-mismatch-1"],
        },
    )

    assert response.status_code == 200
    report = _load_feedback_report(response.json()["feedback_id"])

    assert report.processing_status == ProcessingStatus.COMPLETED
    assert report.conversation_transcript is None
    assert mocked_capture == []
    assert len(captured_email_messages) == 1
    assert "Conversation transcript" not in str(captured_email_messages[0].get_payload())


def test_feedback_submission_skips_transcript_for_cross_user_session(
    client,
    curator1_user,
    captured_email_messages,
    monkeypatch,
):
    session_id = f"{SESSION_PREFIX}cross-user"
    monkeypatch.setattr(
        "src.lib.feedback.service.capture_feedback_conversation_transcript",
        lambda **_kwargs: None,
    )

    response = client.post(
        "/api/feedback/submit",
        json={
            "session_id": session_id,
            "curator_id": curator1_user.email,
            "feedback_text": "Cross-user transcript access should be blocked.",
            "trace_ids": ["trace-cross-user-1"],
        },
    )

    assert response.status_code == 200
    report = _load_feedback_report(response.json()["feedback_id"])

    assert report.processing_status == ProcessingStatus.COMPLETED
    assert report.conversation_transcript is None
    assert len(captured_email_messages) == 1
    assert "Conversation transcript" not in str(captured_email_messages[0].get_payload())
