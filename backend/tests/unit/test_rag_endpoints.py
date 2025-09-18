"""Unit tests for RAG session and question endpoints."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

pytest.importorskip("pydantic_ai")

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.main import app
from app.database import SessionLocal
from app.models import Base, ChatSession, PDFDocument
from app.database import engine as db_engine
from app.services.orchestrator_service import get_langgraph_runner
from app.orchestration.general_supervisor import IntentAnalysis, PDFQAState

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


class FakeRunner:
    async def run(self, state: PDFQAState) -> PDFQAState:
        state.answer = "Test answer"
        state.citations = [{"page": 1}]
        state.metadata = {"confidence": 0.9}
        state.specialists_invoked = ["disease_ontology", "general"]
        state.specialist_results = {
            "disease_ontology": {
                "answer": "Test disease answer",
                "entries": [],
            }
        }
        return state


class StreamingRunner(FakeRunner):
    async def stream(self, state: PDFQAState):
        updated_state = state.model_copy(
            update={
                "answer": "Streamed answer",
                "citations": [{"page": 2}],
                "specialists_invoked": ["disease_ontology", "general"],
                "specialist_results": {
                    "disease_ontology": {
                        "answer": "Streamed disease answer",
                        "entries": [],
                    }
                },
            }
        )

        async def _iter():
            yield {"type": "agent_start", "agent": "retrieve_context"}
            yield {"type": "delta", "content": "partial", "agent": "general_answer"}
            yield {
                "type": "final",
                "answer": "Streamed answer",
                "citations": [{"page": 2}],
                "metadata": {"confidence": 0.7},
                "state": updated_state,
            }

        async for event in _iter():
            yield event


def test_question_endpoint_returns_answer(monkeypatch):
    _patch_intent_router(monkeypatch)
    session = SessionLocal()
    try:
        pdf = _create_pdf(session)
        chat_session = ChatSession(pdf_id=pdf.id)
        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)
    finally:
        session.close()

    app.dependency_overrides[get_langgraph_runner] = lambda: FakeRunner()
    try:
        response = client.post(
            f"/api/rag/sessions/{chat_session.id}/question",
            json={"question": "What is the main finding?"},
        )
    finally:
        app.dependency_overrides.pop(get_langgraph_runner, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "Test answer"
    assert payload["citations"][0]["page"] == 1
    # ensure specialist info surfaces
    assert payload["specialists_invoked"] == ["disease_ontology", "general"]
    assert "disease_ontology" in payload["specialist_results"]


def test_question_endpoint_streams_events(monkeypatch):
    router_stub = _patch_intent_router(monkeypatch)
    router_stub.intent = "disease"
    router_stub.confidence = 0.8
    session = SessionLocal()
    try:
        pdf = _create_pdf(session)
        chat_session = ChatSession(pdf_id=pdf.id)
        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)
    finally:
        session.close()

    app.dependency_overrides[get_langgraph_runner] = lambda: StreamingRunner()
    try:
        with client.stream(
            "POST",
            f"/api/rag/sessions/{chat_session.id}/question",
            headers={"accept": "text/event-stream"},
            json={"question": "Need stream"},
        ) as response:
            events = []
            for raw in response.iter_lines():
                if not raw:
                    continue
                raw_text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                assert raw_text.startswith("data: ")
                payload = json.loads(raw_text[6:])
                events.append(payload)

    finally:
        app.dependency_overrides.pop(get_langgraph_runner, None)

    assert [event["type"] for event in events] == [
        "start",
        "agent_start",
        "delta",
        "final",
        "end",
    ]
    final_event = events[-2]
    assert final_event["answer"] == "Streamed answer"
    assert final_event["citations"] == [{"page": 2}]
    assert final_event["specialists_invoked"] == ["disease_ontology", "general"]
    assert "disease_ontology" in final_event["specialist_results"]


def _patch_intent_router(monkeypatch):
    class RouterStub:
        def __init__(self):
            self.intent = "general"
            self.confidence = 0.6
            self.detected_entities = {}
            self.query_mode = "vector_search"

        async def run(self, prompt: str):
            return type(
                "Result",
                (),
                {
                    "output": IntentAnalysis(
                        primary_intent=self.intent,
                        confidence=self.confidence,
                        reasoning="stub",
                        requires_specialists=list(self.detected_entities.keys()),
                        detected_entities=self.detected_entities,
                        query_mode=self.query_mode,
                    )
                },
            )

    stub = RouterStub()
    monkeypatch.setattr(
        "app.orchestration.general_supervisor.get_intent_router",
        lambda: stub,
    )
    return stub
