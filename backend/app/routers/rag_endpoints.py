"""Endpoints for managing RAG sessions and questions."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Dict, List
from uuid import UUID

from contextlib import suppress
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ChatSession, Message, MessageType, PDFDocument
from app.orchestration.general_supervisor import PDFQAState
from app.repositories.langgraph_runs import LangGraphRunRepository
from app.services.orchestrator_service import (
    get_langgraph_runner,
    get_general_orchestrator,
)
from app.services.langsmith_service import LangSmithService
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
    specialist_results: Dict[str, Any]
    specialists_invoked: List[str]


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
    http_request: Request,
    runner=Depends(get_langgraph_runner),
    db: Session = Depends(get_db),
):
    """Process a question with full LangSmith tracing."""

    # Add request metadata
    LangSmithService.add_metadata_to_current_trace(
        {
            "session_id": str(session_id),
            "question_length": len(request.question),
            "streaming_requested": "text/event-stream"
            in http_request.headers.get("accept", "").lower(),
        }
    )

    session_obj = db.get(ChatSession, session_id)
    if session_obj is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Log session metadata
    LangSmithService.add_metadata_to_current_trace(
        {
            "pdf_id": str(session_obj.pdf_id),
            "total_messages": session_obj.total_messages,
            "rag_config": session_obj.rag_config or {},
        }
    )

    state = PDFQAState(
        session_id=session_obj.id,
        pdf_id=session_obj.pdf_id,
        question=request.question,
    )
    repo = LangGraphRunRepository(db)
    run = repo.start_run(
        session_id=session_obj.id,
        pdf_id=session_obj.pdf_id,
        workflow_name="general_supervisor",
        question=request.question,
        run_metadata={"rag_config": session_obj.rag_config or {}},
    )

    accepts = http_request.headers.get("accept", "").lower()
    wants_stream = "text/event-stream" in accepts

    if wants_stream:
        start = perf_counter()
        final_state_holder: Dict[str, PDFQAState | None] = {"state": None}
        error_holder: Dict[str, Any | None] = {"error": None}

        async def event_stream():
            final_sent = False
            try:
                yield _encode_sse({"type": "start"})

                # First, run LangGraph to get routing and context (non-streaming)
                prepared_state = await runner.run(state)

                # Now stream the answer generation directly from orchestrator
                orchestrator = get_general_orchestrator()

                # Prepare prompt with specialist context if any
                specialist_context_parts = []
                if prepared_state.specialist_results:
                    for name, payload in prepared_state.specialist_results.items():
                        if not isinstance(payload, dict):
                            continue
                        section_lines = [f"[{name}] specialist findings:"]
                        answer = payload.get("answer")
                        if answer:
                            section_lines.append(str(answer))
                        entries = payload.get("entries") or []
                        for entry in entries[:5]:
                            if not isinstance(entry, dict):
                                continue
                            term_id = entry.get("term_id")
                            definition = entry.get("definition")
                            if term_id or definition:
                                section_lines.append(f"- {term_id}: {definition}")
                        specialist_context_parts.append("\n".join(section_lines))

                prompt = prepared_state.prepared_prompt or ""
                augmented_prompt = prompt
                if specialist_context_parts:
                    augmented_prompt += "\n\nSpecialist Findings:\n" + "\n\n".join(
                        specialist_context_parts
                    )

                deps = prepared_state.prepared_deps or {
                    "query": prepared_state.question,
                    "context": prepared_state.retrieved_context or "",
                }
                if specialist_context_parts:
                    augmented_context = deps.get("context", "")
                    augmented_context += "\n\nSpecialist Findings:\n" + "\n\n".join(
                        specialist_context_parts
                    )
                    deps = dict(deps)
                    deps["context"] = augmented_context

                # Stream from orchestrator
                accumulated_text = ""
                final_result = None

                async for chunk in orchestrator.stream_with_serialized(
                    prompt=augmented_prompt,
                    deps=deps,
                    citations=list(prepared_state.citations),
                    metadata=dict(prepared_state.metadata),
                ):
                    if chunk.get("type") == "delta":
                        text_chunk = chunk.get("content", "")
                        accumulated_text += text_chunk
                        yield _encode_sse({"type": "delta", "content": text_chunk})
                    elif chunk.get("type") == "final":
                        final_result = chunk.get("result")

                # Update state with final answer
                if final_result:
                    prepared_state = prepared_state.model_copy(
                        update={
                            "answer": final_result.answer,
                            "citations": final_result.citations,
                            "metadata": {
                                **prepared_state.metadata,
                                **final_result.metadata,
                            },
                        }
                    )
                else:
                    prepared_state = prepared_state.model_copy(
                        update={"answer": accumulated_text}
                    )

                final_state_holder["state"] = prepared_state
                final_sent = True
                yield _encode_sse(
                    {
                        "type": "final",
                        "answer": prepared_state.answer or accumulated_text,
                        "citations": prepared_state.citations,
                        "metadata": prepared_state.metadata,
                        "specialist_results": prepared_state.specialist_results,
                        "specialists_invoked": prepared_state.specialists_invoked,
                    }
                )

            except Exception as exc:  # pragma: no cover - propagated via SSE
                error_holder["error"] = exc
                yield _encode_sse({"type": "error", "message": str(exc)})
            finally:
                latency_ms = int((perf_counter() - start) * 1000)
                final_state = final_state_holder["state"]
                if final_state is not None:
                    repo.complete_run(
                        run,
                        state_snapshot=final_state.model_dump(mode="json"),
                        specialists_invoked=final_state.specialists_invoked or [],
                        latency_ms=latency_ms,
                    )
                    repo.commit()
                    _store_messages(db, session_obj, request.question, final_state)
                else:
                    error_message = error_holder["error"]
                    repo.complete_run(
                        run,
                        state_snapshot={
                            "error": str(error_message) if error_message else "unknown"
                        },
                        specialists_invoked=[],
                        latency_ms=latency_ms,
                        status="FAILED",
                    )
                    repo.commit()

                yield _encode_sse({"type": "end"})

        headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
        return StreamingResponse(
            event_stream(), media_type="text/event-stream", headers=headers
        )

    start = perf_counter()
    final_state = await runner.run(state)
    latency_ms = int((perf_counter() - start) * 1000)

    repo.complete_run(
        run,
        state_snapshot=final_state.model_dump(mode="json"),
        specialists_invoked=final_state.specialists_invoked or [],
        latency_ms=latency_ms,
    )
    repo.commit()

    _store_messages(db, session_obj, request.question, final_state)

    # Log final metrics before returning
    LangSmithService.add_metadata_to_current_trace(
        {
            "latency_ms": latency_ms,
            "specialists_invoked": final_state.specialists_invoked or [],
            "answer_generated": bool(final_state.answer),
            "streaming_completed": False,
        }
    )

    return QuestionResponse(
        answer=final_state.answer or "",
        citations=final_state.citations,
        metadata=final_state.metadata,
        specialist_results=final_state.specialist_results,
        specialists_invoked=final_state.specialists_invoked,
    )


def _store_messages(
    db: Session,
    session_obj: ChatSession,
    question: str,
    result_state: PDFQAState,
) -> None:
    user_message = Message(
        session_id=session_obj.id,
        message_type=MessageType.USER_QUESTION,
        content=question,
    )
    answer_message = Message(
        session_id=session_obj.id,
        message_type=MessageType.AI_RESPONSE,
        content=result_state.answer or "",
        citations=result_state.citations,
        retrieval_stats={
            **(result_state.metadata or {}),
            "specialist_results": result_state.specialist_results or {},
            "specialists_invoked": result_state.specialists_invoked or [],
        },
    )
    session_obj.total_messages += 2
    db.add_all([user_message, answer_message])
    db.commit()


def _encode_sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


__all__ = ["router"]
