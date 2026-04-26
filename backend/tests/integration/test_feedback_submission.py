"""Integration coverage for durable feedback transcript capture."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, text
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
    from src.models.sql.pdf_document import PDFDocument

    PDFDocument.__table__.create(bind=db.get_bind(), checkfirst=True)
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
    from src.lib.feedback import service as feedback_service

    app.dependency_overrides[_get_user_from_cookie_impl] = get_auth_mock.get_user
    monkeypatch.setattr(
        feedback_api,
        "dispatch_feedback_report_processing",
        lambda feedback_id: feedback_api._run_feedback_processing_in_background(feedback_id),
    )

    async def _default_trace_context_unavailable(_trace_id):
        raise RuntimeError("Langfuse disabled in feedback integration test")

    monkeypatch.setattr(
        feedback_service,
        "get_trace_context_for_explorer",
        _default_trace_context_unavailable,
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
    _seed_durable_chat_session(session_id, user_auth_sub=curator1_user["sub"])

    async def _get_trace_context(trace_id):
        return SimpleNamespace(
            trace_id=trace_id,
            session_id=session_id,
            timestamp=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
            user_query="Feedback trace for curator@example.org token=super-secret",
            final_response_preview="Final answer",
            prompts_executed=[
                SimpleNamespace(
                    agent_id="gene_extractor",
                    agent_name="Gene Extractor",
                    prompt_preview="system prompt should not be stored",
                    group_applied="WB",
                    model="gpt-test",
                    tokens_used=111,
                )
            ],
            routing_decisions=[],
            tool_calls=[
                SimpleNamespace(
                    name="search_document",
                    input={"secret": "do-not-store"},
                    output_preview="raw tool output should not be stored",
                    duration_ms=55,
                    status="completed",
                )
            ],
            total_duration_ms=1200,
            total_tokens=222,
            agent_count=1,
        )

    from src.lib.feedback import service as feedback_service

    monkeypatch.setattr(feedback_service, "get_trace_context_for_explorer", _get_trace_context)

    response = client.post(
        "/api/feedback/submit",
        json={
            "session_id": session_id,
            "curator_id": curator1_user["email"],
            "feedback_text": "Transcript should be attached.",
            "trace_ids": ["trace-happy-1"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {"status", "feedback_id", "message"}
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
    assert report.trace_data is not None
    assert report.trace_data["capture_status"] == "success"
    assert report.trace_data["feedback"]["session_id"] == session_id
    assert report.trace_data["feedback"]["trace_ids"] == ["trace-happy-1"]
    trace_snapshot = report.trace_data["traces"][0]
    assert trace_snapshot["session_matches_feedback"] is True
    assert trace_snapshot["previews"]["user_query"] == (
        "Feedback trace for [redacted-email] token=[redacted]"
    )
    assert trace_snapshot["metrics"]["tool_call_count"] == 1
    assert trace_snapshot["prompts_executed"][0]["agent_id"] == "gene_extractor"
    assert "prompt_preview" not in trace_snapshot["prompts_executed"][0]
    assert trace_snapshot["tool_calls"] == [
        {"name": "search_document", "duration_ms": 55, "status": "completed"}
    ]
    assert "super-secret" not in str(report.trace_data)

    assert len(captured_email_messages) == 1
    body = str(captured_email_messages[0].get_payload())
    normalized_body = body.replace("=\n", "").replace("=3D", "=")
    assert "AI Curation feedback debug:" in normalized_body
    assert f"/api/feedback/{report.id}/debug" in normalized_body
    assert "TraceReview session bundle:" in normalized_body
    assert f"/api/traces/sessions/{session_id}/export?source=remote" in normalized_body
    assert "Conversation transcript excerpt:" in normalized_body
    assert f"Full durable transcript stored on feedback report {report.id}." in normalized_body
    assert "1. User: First question" in normalized_body
    assert "... 2 middle turns omitted ..." in normalized_body
    assert "8. Assistant: Fourth answer" in normalized_body

    debug_response = client.get(f"/api/feedback/{report.id}/debug")
    assert debug_response.status_code == 200
    debug_payload = debug_response.json()
    assert debug_payload["feedback_id"] == report.id
    assert debug_payload["feedback_debug_url"] == f"/api/feedback/{report.id}/debug"
    assert debug_payload["trace_review_session_url"] == (
        f"/api/traces/sessions/{session_id}/export?source=remote"
    )
    assert debug_payload["transcript"] == {
        "available": True,
        "message_count": len(TRANSCRIPT_MESSAGES),
        "captured_at": report.conversation_transcript["captured_at"],
        "session_id": session_id,
        "chat_kind": "assistant_chat",
        "title": "Saved title",
        "effective_title": "Saved title",
        "session_matches_feedback": True,
    }
    assert debug_payload["trace_data"]["available"] is True
    assert debug_payload["trace_data"]["status"] == "success"
    assert debug_payload["trace_data"]["expected_trace_ids"] == ["trace-happy-1"]
    assert debug_payload["trace_data"]["stored_trace_ids"] == ["trace-happy-1"]
    assert "traces" not in debug_payload["trace_data"]
    assert "prompt_preview" not in str(debug_payload)
    assert "super-secret" not in str(debug_payload)


def test_feedback_submission_logs_lookup_failure_but_still_succeeds(
    client,
    curator1_user,
    captured_email_messages,
    monkeypatch,
):
    session_id = f"{SESSION_PREFIX}lookup-failure"

    def _raise_lookup_error(self, *_args, **_kwargs):
        self._db.execute(text("SELECT * FROM feedback_lookup_missing_table"))

    _seed_durable_chat_session(session_id, user_auth_sub=curator1_user["sub"])
    monkeypatch.setattr(
        "src.lib.chat_history_repository.ChatHistoryRepository.get_session_detail",
        _raise_lookup_error,
    )

    response = client.post(
        "/api/feedback/submit",
        json={
            "session_id": session_id,
            "curator_id": curator1_user["email"],
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


def test_feedback_submission_persists_trace_capture_failure_metadata(
    client,
    curator1_user,
    captured_email_messages,
):
    session_id = f"{SESSION_PREFIX}trace-capture-failure"
    _seed_durable_chat_session(session_id, user_auth_sub=curator1_user["sub"])

    response = client.post(
        "/api/feedback/submit",
        json={
            "session_id": session_id,
            "curator_id": curator1_user["email"],
            "feedback_text": "Trace capture failures should be durable.",
            "trace_ids": ["trace-capture-failure-1"],
        },
    )

    assert response.status_code == 200
    report = _load_feedback_report(response.json()["feedback_id"])

    assert report.processing_status == ProcessingStatus.COMPLETED
    assert report.trace_data is not None
    assert report.trace_data["capture_status"] == "error"
    assert report.trace_data["feedback"] == {
        "session_id": session_id,
        "trace_ids": ["trace-capture-failure-1"],
    }
    assert report.trace_data["error_summary"]["trace_error_count"] == 1
    trace_error = report.trace_data["traces"][0]["error"]
    assert trace_error["type"] == "RuntimeError"
    assert trace_error["message"] == "Langfuse disabled in feedback integration test"
    assert len(captured_email_messages) == 1

    debug_response = client.get(f"/api/feedback/{report.id}/debug")
    assert debug_response.status_code == 200
    debug_payload = debug_response.json()
    assert debug_payload["trace_data"]["status"] == "error"
    assert debug_payload["trace_data"]["error_summary"] == {
        "trace_error_count": 1,
        "message": "One or more trace snapshots could not be captured.",
    }
    assert debug_payload["trace_data"]["errors"] == [
        {
            "trace_id": "trace-capture-failure-1",
            "type": "RuntimeError",
            "message": "Langfuse disabled in feedback integration test",
        }
    ]


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
            "curator_id": curator1_user["email"],
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
            "curator_id": curator1_user["email"],
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
