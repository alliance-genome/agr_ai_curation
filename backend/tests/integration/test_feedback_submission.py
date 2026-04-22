"""Integration coverage for durable feedback transcript capture."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from fastapi.testclient import TestClient

# Import database first to avoid the package init circular path when importing feedback models.
import src.models.sql.database  # noqa: F401
from src.lib.feedback.models import FeedbackReport, ProcessingStatus
from src.models.sql.database import SessionLocal


SESSION_PREFIX = "feedback-test-"
TRANSCRIPT_MESSAGES = [
    ("user", "First question"),
    ("assistant", "First answer"),
    ("user", "Second question"),
    ("assistant", "Second answer"),
    ("user", "Third question"),
    ("assistant", "Third answer"),
    ("user", "Fourth question"),
    ("assistant", "Fourth answer"),
]


def _ensure_feedback_report_table_exists(db) -> None:
    FeedbackReport.__table__.create(bind=db.get_bind(), checkfirst=True)


def _ensure_chat_history_tables_exist(db) -> None:
    from src.models.sql.chat_message import ChatMessage
    from src.models.sql.chat_session import ChatSession

    ChatSession.__table__.create(bind=db.get_bind(), checkfirst=True)
    ChatMessage.__table__.create(bind=db.get_bind(), checkfirst=True)


def _cleanup_feedback_state() -> None:
    from src.models.sql.chat_message import ChatMessage
    from src.models.sql.chat_session import ChatSession

    db = SessionLocal()
    try:
        _ensure_feedback_report_table_exists(db)
        _ensure_chat_history_tables_exist(db)
        db.execute(
            delete(FeedbackReport).where(FeedbackReport.session_id.like(f"{SESSION_PREFIX}%"))
        )
        db.execute(
            delete(ChatMessage).where(ChatMessage.session_id.like(f"{SESSION_PREFIX}%"))
        )
        db.execute(
            delete(ChatSession).where(ChatSession.session_id.like(f"{SESSION_PREFIX}%"))
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


def _seed_durable_chat_session(session_id: str, *, user_auth_sub: str) -> None:
    from src.lib.chat_history_repository import ASSISTANT_CHAT_KIND, ChatHistoryRepository

    db = SessionLocal()
    created_at = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    try:
        _ensure_chat_history_tables_exist(db)
        repository = ChatHistoryRepository(db)
        repository.create_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=ASSISTANT_CHAT_KIND,
            title="Saved title",
            created_at=created_at,
        )
        for index, (role, content) in enumerate(TRANSCRIPT_MESSAGES, start=1):
            repository.append_message(
                session_id=session_id,
                user_auth_sub=user_auth_sub,
                chat_kind=ASSISTANT_CHAT_KIND,
                role=role,
                content=content,
                turn_id=f"{session_id}-turn-{index}",
                created_at=created_at + timedelta(minutes=index),
            )
        db.commit()
    finally:
        db.close()


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
):
    session_id = f"{SESSION_PREFIX}happy-path"
    _seed_durable_chat_session(session_id, user_auth_sub=curator1_user["sub"])

    response = client.post(
        "/api/feedback/submit",
        json={
            "session_id": session_id,
            "curator_id": curator1_user["sub"],
            "feedback_text": "Transcript should be attached.",
            "trace_ids": ["trace-happy-1"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    report = _load_feedback_report(payload["feedback_id"])

    assert report.processing_status == ProcessingStatus.COMPLETED
    assert report.conversation_transcript is not None
    assert report.conversation_transcript["message_count"] == len(TRANSCRIPT_MESSAGES)
    assert report.conversation_transcript["session"]["session_id"] == session_id
    assert report.conversation_transcript["session"]["title"] == "Saved title"
    assert report.conversation_transcript["session"]["effective_title"] == "Saved title"
    assert [
        (message["role"], message["content"])
        for message in report.conversation_transcript["messages"]
    ] == TRANSCRIPT_MESSAGES

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
    def _raise_lookup_error(*_args, **_kwargs):
        raise SQLAlchemyError("lookup failed")

    _seed_durable_chat_session(session_id, user_auth_sub=curator1_user["sub"])
    monkeypatch.setattr(
        "src.lib.chat_history_repository.ChatHistoryRepository.get_session_detail",
        _raise_lookup_error,
    )

    response = client.post(
        "/api/feedback/submit",
        json={
            "session_id": session_id,
            "curator_id": curator1_user["sub"],
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
    curator1_user,
    captured_email_messages,
    monkeypatch,
):
    session_id = f"{SESSION_PREFIX}mismatched-curator"
    _seed_durable_chat_session(
        session_id,
        user_auth_sub=curator1_user["sub"],
    )

    def _unexpected_lookup(*_args, **_kwargs):
        raise AssertionError("curator mismatch should skip transcript lookup")

    monkeypatch.setattr(
        "src.lib.chat_history_repository.ChatHistoryRepository.get_session_detail",
        _unexpected_lookup,
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
    assert len(captured_email_messages) == 1
    assert "Conversation transcript" not in str(captured_email_messages[0].get_payload())


def test_feedback_submission_skips_transcript_for_cross_user_session(
    client,
    curator1_user,
    curator2_user,
    captured_email_messages,
):
    session_id = f"{SESSION_PREFIX}cross-user"
    _seed_durable_chat_session(session_id, user_auth_sub=curator2_user["sub"])

    response = client.post(
        "/api/feedback/submit",
        json={
            "session_id": session_id,
            "curator_id": curator1_user["sub"],
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


def test_feedback_submission_skips_transcript_when_session_missing(
    client,
    curator1_user,
    captured_email_messages,
):
    session_id = f"{SESSION_PREFIX}missing-session"

    response = client.post(
        "/api/feedback/submit",
        json={
            "session_id": session_id,
            "curator_id": curator1_user["sub"],
            "feedback_text": "Missing sessions should not block feedback.",
            "trace_ids": ["trace-missing-1"],
        },
    )

    assert response.status_code == 200
    report = _load_feedback_report(response.json()["feedback_id"])

    assert report.processing_status == ProcessingStatus.COMPLETED
    assert report.conversation_transcript is None
    assert len(captured_email_messages) == 1
    assert "Conversation transcript" not in str(captured_email_messages[0].get_payload())
