"""TDD-RED: Tests for general RAG orchestrator."""

from __future__ import annotations

from typing import List
from uuid import uuid4

import pytest

from app.agents.main_orchestrator import GeneralOrchestrator, OrchestratorConfig
from app.agents.pipeline_models import GeneralPipelineChunk, GeneralPipelineOutput


class FakePipeline:
    def __init__(self, output: GeneralPipelineOutput) -> None:
        self._output = output
        self.calls: list[tuple[str, uuid4]] = []

    async def run(self, *, pdf_id: uuid4, query: str) -> GeneralPipelineOutput:
        self.calls.append((query, pdf_id))
        return self._output


class FakeLLM:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: List[dict] = []

    async def generate(
        self, *, prompt: str, context: List[GeneralPipelineChunk]
    ) -> str:
        self.calls.append({"prompt": prompt, "context": context})
        return self._response


@pytest.mark.asyncio
async def test_general_orchestrator_runs_pipeline_and_llm():
    pdf_id = uuid4()
    pipeline_output = GeneralPipelineOutput.from_chunks(
        query="What is BRCA1?",
        pdf_id=pdf_id,
        chunks=[
            GeneralPipelineChunk(
                chunk_id=uuid4(),
                text="BRCA1 is associated with DNA repair",
                score=0.9,
                source="reranker",
                citation={"page": 3},
            )
        ],
    )
    pipeline = FakePipeline(pipeline_output)
    llm = FakeLLM("BRCA1 repairs DNA.")

    orchestrator = GeneralOrchestrator(
        pipeline=pipeline,
        llm=llm,
        config=OrchestratorConfig(confidence_threshold=0.0),
    )

    result = await orchestrator.answer_question(pdf_id=pdf_id, query="What is BRCA1?")

    assert pipeline.calls[0][0] == "What is BRCA1?"
    assert "BRCA1" in llm.calls[0]["prompt"]
    assert result.answer == "BRCA1 repairs DNA."
    assert result.citations[0]["page"] == 3
