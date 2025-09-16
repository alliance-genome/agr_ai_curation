"""Unit tests for RAG session and question endpoints."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.main import app
from app.database import SessionLocal
from app.models import Base, ChatSession, PDFDocument
from app.database import engine as db_engine
from app.services.orchestrator_service import get_general_orchestrator
from app.agents.main_orchestrator import OrchestratorResult

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    session = SessionLocal()
    try:
        Base.metadata.create_all(bind=db_engine)
        try:
            session.execute(
                text(
                    "TRUNCATE TABLE messages, chat_sessions, pdf_embeddings, pdf_chunks, pdf_documents RESTART IDENTITY CASCADE"
                )
            )
            session.commit()
        except ProgrammingError:
            session.rollback()
    finally:
        session.close()


def _create_pdf(session) -> PDFDocument:
    pdf = PDFDocument(
        filename="test.pdf",
        file_path="/tmp/test.pdf",
        file_hash=uuid4().hex[:32],
        content_hash_normalized=uuid4().hex[:32],
        file_size=1024,
        page_count=1,
    )
    session.add(pdf)
    session.commit()
    session.refresh(pdf)
    return pdf


def test_create_session_endpoint():
    session = SessionLocal()
    try:
        pdf = _create_pdf(session)
    finally:
        session.close()

    response = client.post(
        "/api/rag/sessions",
        json={"pdf_id": str(pdf.id)},
    )

    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data

    session = SessionLocal()
    try:
        stored = session.get(ChatSession, data["session_id"])
        assert stored is not None
    finally:
        session.close()


class FakeOrchestrator:
    async def answer_question(self, *, pdf_id, query):
        return OrchestratorResult(
            answer="Test answer",
            citations=[{"page": 1}],
            metadata={"confidence": 0.9},
        )


def test_question_endpoint_returns_answer(monkeypatch):
    session = SessionLocal()
    try:
        pdf = _create_pdf(session)
        chat_session = ChatSession(pdf_id=pdf.id)
        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)
    finally:
        session.close()

    app.dependency_overrides[get_general_orchestrator] = lambda: FakeOrchestrator()
    try:
        response = client.post(
            f"/api/rag/sessions/{chat_session.id}/question",
            json={"question": "What is the main finding?"},
        )
    finally:
        app.dependency_overrides.pop(get_general_orchestrator, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "Test answer"
    assert payload["citations"][0]["page"] == 1
