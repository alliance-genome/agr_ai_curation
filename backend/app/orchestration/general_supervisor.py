"""LangGraph supervisor that wraps the general PydanticAI orchestrator."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from app.agents.main_orchestrator import GeneralOrchestrator


class PDFQAState(BaseModel):
    """Conversation state passed between LangGraph nodes."""

    session_id: UUID
    pdf_id: UUID
    question: str
    intent: Optional[str] = None
    answer: Optional[str] = None
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    specialists_invoked: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


async def analyze_intent(state: PDFQAState) -> Dict[str, Any]:
    """Determine which route to take inside the graph.

    For now, all questions go through the general path. The field is still
    recorded so we can branch later without rewriting callers.
    """

    return {
        "intent": state.intent or "general",
        "specialists_invoked": list(state.specialists_invoked),
    }


def build_general_supervisor(
    *,
    orchestrator: GeneralOrchestrator,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """Compile the general LangGraph supervisor.

    Parameters
    ----------
    orchestrator:
        Existing PydanticAI orchestrator used to build responses.
    checkpointer:
        Optional LangGraph checkpointer. Uses in-memory saver by default.
    """

    workflow = StateGraph(PDFQAState)

    workflow.add_node("intent_router", analyze_intent)

    async def general_answer(state: PDFQAState) -> Dict[str, Any]:
        result = await orchestrator.answer_question(
            pdf_id=state.pdf_id, query=state.question
        )
        metadata = dict(state.metadata)
        metadata.update(result.metadata)
        specialists = list(state.specialists_invoked)
        specialists.append("general")
        updates: Dict[str, Any] = {
            "answer": result.answer,
            "citations": list(result.citations),
            "metadata": metadata,
            "specialists_invoked": specialists,
        }
        if state.intent is None:
            updates["intent"] = "general"
        return updates

    workflow.add_node("general_answer", general_answer)

    workflow.add_edge(START, "intent_router")
    workflow.add_edge("intent_router", "general_answer")
    workflow.add_edge("general_answer", END)

    return workflow.compile(checkpointer=checkpointer or MemorySaver())


__all__ = ["PDFQAState", "analyze_intent", "build_general_supervisor"]
