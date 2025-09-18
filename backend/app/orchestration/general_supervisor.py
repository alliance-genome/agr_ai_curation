"""LangGraph supervisor that wraps the general PydanticAI orchestrator."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.agents.main_orchestrator import GeneralOrchestrator
from app.agents.disease_ontology_agent import DiseaseOntologyAgent


class IntentAnalysis(BaseModel):
    primary_intent: str
    confidence: float
    reasoning: str
    requires_specialists: List[str] = Field(default_factory=list)
    detected_entities: Dict[str, List[str]] = Field(default_factory=dict)
    query_mode: str = Field(default="vector_search")


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
    retrieved_chunks: List[Dict[str, Any]] = Field(default_factory=list)
    retrieved_context: Optional[str] = None
    chunk_texts: List[str] = Field(default_factory=list)
    chunk_count: int = 0
    routing_confidence: Optional[float] = None
    routing_reasoning: Optional[str] = None
    specialist_results: Dict[str, Any] = Field(default_factory=dict)
    prepared_prompt: Optional[str] = None
    prepared_deps: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


async def analyze_intent(state: PDFQAState) -> Dict[str, Any]:
    router = get_intent_router()

    prompt = """
You are an intent classifier for a biomedical PDF Q&A system. Review the question, retrieved
document context, and sampled chunks. Decide which specialist should handle the request.

Available specialists: "general", "disease".

Output JSON with fields: primary_intent (general|disease), confidence (0-1), reasoning,
requires_specialists (list of strings), detected_entities (dict from category -> list of strings).
query_mode should be one of "vector_search", "term_lookup", or "hierarchy_lookup".
Use term_lookup when the user references a specific ontology term ID and wants details.
Use hierarchy_lookup when the user asks explicitly about parents/children/relationships for a term.
Otherwise default to vector_search. If unsure, choose "general" and explain why.

Question: {question}

Context:
{context}

Sampled chunks:
{chunks}
""".format(
        question=state.question,
        context=(state.retrieved_context or "")[:2000],
        chunks="\n\n".join(state.chunk_texts[:3])[:2000],
    )

    try:
        router_result = await router.run(prompt)
        analysis: IntentAnalysis = router_result.output
    except Exception:  # pragma: no cover - guardrail for router failures
        analysis = IntentAnalysis(
            primary_intent=state.intent or "general",
            confidence=0.0,
            reasoning="intent router failed",
        )

    intent = state.intent or analysis.primary_intent or "general"
    confidence = max(state.routing_confidence or 0.0, analysis.confidence or 0.0)
    reasoning = state.routing_reasoning or analysis.reasoning

    metadata = dict(state.metadata)
    if analysis.detected_entities:
        merged = dict(metadata.get("detected_entities", {}))
        for key, values in analysis.detected_entities.items():
            current = merged.get(key, [])
            for value in values:
                if value not in current:
                    current.append(value)
            merged[key] = current
        metadata["detected_entities"] = merged
    if analysis.requires_specialists:
        metadata["requires_specialists"] = analysis.requires_specialists

    return {
        "intent": intent,
        "routing_confidence": confidence,
        "routing_reasoning": reasoning,
        "metadata": {**metadata, "query_mode": analysis.query_mode},
    }


def build_general_supervisor(
    *,
    orchestrator: GeneralOrchestrator,
    checkpointer: BaseCheckpointSaver | None = None,
    disease_agent: DiseaseOntologyAgent | None = None,
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
    disease_agent = disease_agent or DiseaseOntologyAgent()

    async def retrieve_context(state: PDFQAState) -> Dict[str, Any]:
        prepared = await orchestrator.prepare(pdf_id=state.pdf_id, query=state.question)

        chunk_payloads = [
            {
                "chunk_id": str(chunk.chunk_id),
                "text": chunk.text,
                "score": chunk.score,
                "citation": chunk.citation,
                "metadata": chunk.metadata,
            }
            for chunk in prepared.chunks
        ]

        combined_metadata = dict(state.metadata)
        combined_metadata.update(prepared.metadata)

        return {
            "retrieved_context": prepared.deps.context,
            "retrieved_chunks": chunk_payloads,
            "chunk_texts": prepared.chunk_texts,
            "chunk_count": prepared.chunk_count,
            "citations": list(prepared.citations),
            "metadata": combined_metadata,
            "prepared_prompt": prepared.prompt,
            "prepared_deps": prepared.deps.model_dump(),
        }

    async def general_answer(state: PDFQAState) -> Dict[str, Any]:
        specialist_context_parts: List[str] = []
        if state.specialist_results:
            for name, payload in state.specialist_results.items():
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

        prompt = state.prepared_prompt or ""
        augmented_prompt = prompt
        if specialist_context_parts:
            augmented_prompt += "\n\nSpecialist Findings:\n" + "\n\n".join(
                specialist_context_parts
            )

        deps = state.prepared_deps or {
            "query": state.question,
            "context": state.retrieved_context or "",
        }
        if specialist_context_parts:
            augmented_context = deps.get("context", "")
            augmented_context += "\n\nSpecialist Findings:\n" + "\n\n".join(
                specialist_context_parts
            )
            deps = dict(deps)
            deps["context"] = augmented_context

        result = await orchestrator.run_with_serialized(
            prompt=augmented_prompt,
            deps=deps,
            citations=list(state.citations),
            metadata=dict(state.metadata),
        )
        specialists = list(state.specialists_invoked)
        specialists.append("general")
        merged_metadata = dict(state.metadata)
        merged_metadata.update(result.metadata)
        combined_citations = list(state.citations)
        for citation in result.citations:
            if citation not in combined_citations:
                combined_citations.append(citation)
        updates: Dict[str, Any] = {
            "answer": result.answer,
            "citations": combined_citations,
            "metadata": merged_metadata,
            "specialists_invoked": specialists,
            "specialist_results": dict(state.specialist_results),
        }
        if state.intent is None:
            updates["intent"] = "general"
        return updates

    workflow.add_node("retrieve_context", retrieve_context)
    workflow.add_node("analyze_intent", analyze_intent)

    async def disease_specialist(state: PDFQAState) -> Dict[str, Any]:
        detected_entities = (
            state.metadata.get("detected_entities", {}).get("diseases", [])
            if state.metadata
            else []
        )
        agent_output = await disease_agent.lookup_diseases(
            question=state.question,
            context=state.retrieved_context or "",
            detected_entities=list(detected_entities),
        )

        specialists = list(state.specialists_invoked)
        specialists.append("disease_ontology")

        citations = list(state.citations)
        for citation in agent_output.get("citations", []):
            if citation not in citations:
                citations.append(citation)

        specialist_results = dict(state.specialist_results)
        specialist_results["disease_ontology"] = agent_output

        metadata = dict(state.metadata)

        return {
            "specialists_invoked": specialists,
            "specialist_results": specialist_results,
            "citations": citations,
            "metadata": metadata,
        }

    workflow.add_node("disease_specialist", disease_specialist)
    workflow.add_node("general_answer", general_answer)

    workflow.add_edge(START, "retrieve_context")
    workflow.add_edge("retrieve_context", "analyze_intent")

    def _route_intent(state: PDFQAState) -> str:
        intent = (state.intent or "general").lower()
        return intent if intent in {"disease", "general"} else "general"

    workflow.add_conditional_edges(
        "analyze_intent",
        _route_intent,
        {
            "disease": "disease_specialist",
            "general": "general_answer",
        },
    )
    workflow.add_edge("disease_specialist", "general_answer")
    workflow.add_edge("general_answer", END)

    return workflow.compile(checkpointer=checkpointer or MemorySaver())


@lru_cache
def get_intent_router() -> Agent[Any, IntentAnalysis]:
    from app.config import get_settings

    settings = get_settings()
    model_name = getattr(settings, "intent_router_model", settings.default_model)
    system_prompt = """
You decide which specialist agent should answer a biomedical PDF question.
Return JSON matching the IntentAnalysis schema. Favor "general" unless disease-specific
information is clearly requested.
"""

    return Agent(
        model=model_name,
        output_type=IntentAnalysis,
        system_prompt=system_prompt,
        retries=2,
    )


__all__ = [
    "IntentAnalysis",
    "PDFQAState",
    "analyze_intent",
    "build_general_supervisor",
    "get_intent_router",
]
