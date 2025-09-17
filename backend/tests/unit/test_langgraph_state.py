import asyncio
from uuid import uuid4

import pytest

from app.agents.main_orchestrator import OrchestratorResult


@pytest.fixture()
def dummy_orchestrator():
    class _Orchestrator:
        async def answer_question(self, *, pdf_id, query):
            return OrchestratorResult(
                answer=f"Answer for {query}",
                citations=[{"page": 1, "text": "Example"}],
                metadata={"pdf_id": str(pdf_id)},
            )

    return _Orchestrator()


@pytest.mark.asyncio
async def test_pdfqa_state_defaults(dummy_orchestrator):
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

    app = build_general_supervisor(orchestrator=dummy_orchestrator)

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


@pytest.mark.asyncio
async def test_build_supervisor_custom_checkpointer(dummy_orchestrator):
    from langgraph.checkpoint.memory import MemorySaver

    from app.orchestration.general_supervisor import (
        PDFQAState,
        build_general_supervisor,
    )

    custom_checkpointer = MemorySaver()
    app = build_general_supervisor(
        orchestrator=dummy_orchestrator, checkpointer=custom_checkpointer
    )

    state = PDFQAState(session_id=uuid4(), pdf_id=uuid4(), question="Hello")
    await app.ainvoke(state, config={"thread_id": str(state.session_id)})

    # Using the same checkpointer instance should preserve prior run metadata
    assert app.checkpointer is custom_checkpointer
