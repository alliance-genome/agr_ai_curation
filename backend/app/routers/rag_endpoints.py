"""Endpoints for managing RAG sessions and questions."""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ChatSession, Message, MessageType, PDFDocument
from app.services.orchestrator_service import get_general_orchestrator
from app.agents.main_orchestrator import OrchestratorResult

router = APIRouter(prefix="/api/rag", tags=["rag"])


class CreateSessionRequest(BaseModel):
    pdf_id: UUID
    session_name: str | None = None


class SessionResponse(BaseModel):
    session_id: UUID


class QuestionRequest(BaseModel):
    question: str


class QuestionResponse(BaseModel):
    answer: str
    citations: List[Dict[str, Any]]
    metadata: Dict[str, Any]


@router.post("/sessions", response_model=SessionResponse)
def create_session(
    request: CreateSessionRequest,
    db: Session = Depends(get_db),
) -> SessionResponse:
    pdf = db.get(PDFDocument, request.pdf_id)
    if not pdf:
        raise HTTPException(status_code=404, detail="PDF document not found")

    session = ChatSession(
        pdf_id=pdf.id,
        session_name=request.session_name,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    return SessionResponse(session_id=session.id)


@router.post("/sessions/{session_id}/question", response_model=QuestionResponse)
async def ask_question(
    session_id: UUID,
    request: QuestionRequest,
    orchestrator=Depends(get_general_orchestrator),
    db: Session = Depends(get_db),
) -> QuestionResponse:
    session_obj = db.get(ChatSession, session_id)
    if session_obj is None:
        raise HTTPException(status_code=404, detail="Session not found")

    result: OrchestratorResult = await orchestrator.answer_question(
        pdf_id=session_obj.pdf_id,
        query=request.question,
    )

    _store_messages(db, session_obj, request.question, result)

    return QuestionResponse(
        answer=result.answer,
        citations=result.citations,
        metadata=result.metadata,
    )


def _store_messages(
    db: Session,
    session_obj: ChatSession,
    question: str,
    result: OrchestratorResult,
) -> None:
    user_message = Message(
        session_id=session_obj.id,
        message_type=MessageType.USER_QUESTION,
        content=question,
    )
    answer_message = Message(
        session_id=session_obj.id,
        message_type=MessageType.AI_RESPONSE,
        content=result.answer,
        citations=result.citations,
        retrieval_stats=result.metadata,
    )
    session_obj.total_messages += 2
    db.add_all([user_message, answer_message])
    db.commit()


__all__ = ["router"]
