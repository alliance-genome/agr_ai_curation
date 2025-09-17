from uuid import uuid4

import pytest

from app.orchestration.general_supervisor import PDFQAState, analyze_intent


@pytest.mark.parametrize(
    "question,expected_intent",
    [
        ("What is the summary?", "general"),
        ("List disease findings", "general"),
    ],
)
@pytest.mark.asyncio
async def test_analyze_intent_defaults_to_general(question, expected_intent):
    state = PDFQAState(session_id=uuid4(), pdf_id=uuid4(), question=question)
    updated = await analyze_intent(state)
    assert updated["intent"] == expected_intent
    assert updated["specialists_invoked"] == []
