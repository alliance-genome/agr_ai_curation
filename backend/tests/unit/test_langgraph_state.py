import asyncio
from dataclasses import dataclass
from uuid import uuid4

import pytest

pytest.importorskip("langgraph")


@dataclass
class OrchestratorResult:
    answer: str
    citations: list
    metadata: dict


from app.orchestration.general_supervisor import IntentAnalysis


@pytest.fixture()
def dummy_orchestrator():
    class DummyDeps:
        def __init__(self, query: str, context: str) -> None:
            self.query = query
            self.context = context

        def model_dump(self):
            return {"query": self.query, "context": self.context}

    class DummyPrepared:
        def __init__(self, pdf_id, query) -> None:
            self.prompt = f"Prompt for {query}"
            self.deps = DummyDeps(query=query, context="Sample context")
            self.citations = [{"page": 1, "text": "Example"}]
            self.metadata = {"pdf_id": str(pdf_id)}
            self.chunks = [
                type(
                    "Chunk",
                    (),
                    {
                        "chunk_id": uuid4(),
                        "text": "Chunk body",
                        "score": 0.9,
                        "citation": {"page": 1},
                        "metadata": {},
                    },
                )()
            ]
            self.chunk_texts = ["Chunk body"]
            self.chunk_count = 1

    class _Orchestrator:
        async def prepare(self, *, pdf_id, query):
            return DummyPrepared(pdf_id, query)

        async def run_with_serialized(self, *, prompt, deps, citations, metadata):
            return OrchestratorResult(
                answer=f"Answer for {deps['query']}",
                citations=citations,
                metadata=metadata,
            )

    return _Orchestrator()


@pytest.fixture()
def dummy_disease_agent():
    class _Agent:
        def __init__(self) -> None:
            self.calls = []

        async def lookup_diseases(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "status": "ready",
                "entries": [],
                "answer": "no-op",
                "citations": [],
            }

    return _Agent()


@pytest.fixture()
def intent_router_stub(monkeypatch):
    class RouterStub:
        def __init__(self):
            self.intent = "general"
            self.confidence = 0.6
            self.detected_entities: Dict[str, List[str]] = {}
            self.calls: List[str] = []
            self.query_mode = "vector_search"

        async def run(self, prompt: str):
            self.calls.append(prompt)
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
async def test_pdfqa_state_defaults(
    dummy_orchestrator, dummy_disease_agent, intent_router_stub
):
    from app.orchestration.general_supervisor import (
        PDFQAState,
        build_general_supervisor,
    )

    initial_state = PDFQAState(
        session_id=uuid4(),
        pdf_id=uuid4(),
        question="What is the finding?",
    )

    assert initial_state.answer is None
    assert initial_state.citations == []
    assert initial_state.metadata == {}
    assert initial_state.specialists_invoked == []

    app = build_general_supervisor(
        orchestrator=dummy_orchestrator, disease_agent=dummy_disease_agent
    )

    result = await app.ainvoke(
        initial_state, config={"thread_id": str(initial_state.session_id)}
    )
    from app.orchestration.general_supervisor import PDFQAState

    final_state = (
        result if isinstance(result, PDFQAState) else PDFQAState.model_validate(result)
    )

    assert final_state.answer == "Answer for What is the finding?"
    assert final_state.citations == [{"page": 1, "text": "Example"}]
    assert final_state.metadata["pdf_id"] == str(initial_state.pdf_id)
    assert final_state.specialists_invoked == ["general"]
    assert final_state.intent == "general"
    assert final_state.retrieved_context == "Sample context"
    assert final_state.chunk_count == 1


@pytest.mark.asyncio
async def test_build_supervisor_custom_checkpointer(
    dummy_orchestrator, dummy_disease_agent, intent_router_stub
):
    from langgraph.checkpoint.memory import MemorySaver

    from app.orchestration.general_supervisor import (
        PDFQAState,
        build_general_supervisor,
    )

    custom_checkpointer = MemorySaver()
    app = build_general_supervisor(
        orchestrator=dummy_orchestrator,
        checkpointer=custom_checkpointer,
        disease_agent=dummy_disease_agent,
    )

    state = PDFQAState(session_id=uuid4(), pdf_id=uuid4(), question="Hello")
    await app.ainvoke(state, config={"thread_id": str(state.session_id)})

    # Using the same checkpointer instance should preserve prior run metadata
    assert app.checkpointer is custom_checkpointer


@pytest.mark.asyncio
async def test_detected_disease_routes_to_specialist(
    dummy_orchestrator,
    dummy_disease_agent,
    intent_router_stub,
):
    from app.orchestration.general_supervisor import (
        PDFQAState,
        build_general_supervisor,
    )

    app = build_general_supervisor(
        orchestrator=dummy_orchestrator, disease_agent=dummy_disease_agent
    )

    intent_router_stub.intent = "disease"
    intent_router_stub.confidence = 0.8
    intent_router_stub.detected_entities = {"diseases": ["flu"]}
    intent_router_stub.query_mode = "term_lookup"

    state = PDFQAState(
        session_id=uuid4(),
        pdf_id=uuid4(),
        question="Any disease mentions?",
    )

    result = await app.ainvoke(state, config={"thread_id": str(state.session_id)})
    final_state = (
        result if isinstance(result, PDFQAState) else PDFQAState.model_validate(result)
    )

    assert final_state.specialists_invoked == ["disease_ontology", "general"]
    assert final_state.metadata.get("query_mode") == "term_lookup"
    assert dummy_disease_agent.calls  # type: ignore[attr-defined]
