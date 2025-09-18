from dataclasses import dataclass
from uuid import uuid4

import pytest

pytest.importorskip("langgraph")

from app.orchestration.general_supervisor import (
    IntentAnalysis,
    PDFQAState,
    build_general_supervisor,
)


@dataclass
class OrchestratorResult:
    answer: str
    citations: list
    metadata: dict


@pytest.fixture()
def dummy_orchestrator():
    class DummyDeps:
        def __init__(self, query: str, context: str) -> None:
            self.query = query
            self.context = context

        def model_dump(self):
            return {"query": self.query, "context": self.context}

    class DummyPrepared:
        def __init__(self, pdf_id, query, context) -> None:
            self.prompt = f"Prompt for {query}"
            self.deps = DummyDeps(query=query, context=context)
            self.citations = []
            self.metadata = {"pdf_id": str(pdf_id)}
            self.chunks = []
            self.chunk_texts = []
            self.chunk_count = 0

    class _Orchestrator:
        def __init__(self, context: str) -> None:
            self._context = context

        async def prepare(self, *, pdf_id, query):
            return DummyPrepared(pdf_id, query, self._context)

        async def run_with_serialized(self, *, prompt, deps, citations, metadata):
            return OrchestratorResult(
                answer="fallback",
                citations=citations,
                metadata=metadata,
            )

    return _Orchestrator("context")


@pytest.fixture()
def dummy_disease_agent():
    class _Agent:
        def __init__(self) -> None:
            self.calls = []

        async def lookup_diseases(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "status": "ready",
                "entries": [
                    {
                        "term_id": "DOID:1234",
                        "name": "Example",
                        "definition": "Sample",
                        "score": 0.9,
                    }
                ],
                "answer": "disease answer",
                "citations": [{"type": "ontology", "term_id": "DOID:1234"}],
            }

    return _Agent()


@pytest.fixture()
def intent_router_stub(monkeypatch):
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


@pytest.mark.asyncio
async def test_intent_preserved_when_set(
    dummy_orchestrator, dummy_disease_agent, intent_router_stub
):
    app = build_general_supervisor(
        orchestrator=dummy_orchestrator, disease_agent=dummy_disease_agent
    )
    initial_state = PDFQAState(
        session_id=uuid4(),
        pdf_id=uuid4(),
        question="Q",
        intent="disease",
        routing_confidence=0.8,
    )

    result_state = await app.ainvoke(
        initial_state, config={"thread_id": str(initial_state.session_id)}
    )
    final_state = (
        result_state
        if isinstance(result_state, PDFQAState)
        else PDFQAState.model_validate(result_state)
    )

    assert final_state.intent == "disease"
    assert final_state.routing_confidence == pytest.approx(0.8)
    assert final_state.specialists_invoked == ["disease_ontology", "general"]
    assert "disease_ontology" in final_state.specialist_results
    assert dummy_disease_agent.calls  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_detected_entities_trigger_disease(
    dummy_orchestrator, dummy_disease_agent, intent_router_stub
):
    intent_router_stub.intent = "disease"
    intent_router_stub.confidence = 0.9
    intent_router_stub.detected_entities = {"diseases": ["cancer"]}
    intent_router_stub.query_mode = "hierarchy_lookup"

    app = build_general_supervisor(
        orchestrator=dummy_orchestrator, disease_agent=dummy_disease_agent
    )
    initial_state = PDFQAState(
        session_id=uuid4(),
        pdf_id=uuid4(),
        question="What diseases are mentioned?",
    )

    result_state = await app.ainvoke(
        initial_state, config={"thread_id": str(initial_state.session_id)}
    )
    final_state = (
        result_state
        if isinstance(result_state, PDFQAState)
        else PDFQAState.model_validate(result_state)
    )

    assert final_state.intent == "disease"
    assert final_state.specialists_invoked[0] == "disease_ontology"
    assert final_state.metadata.get("query_mode") == "hierarchy_lookup"
