"""Factories for building orchestrators used in API endpoints."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import List

from openai import OpenAI

from app.agents.main_orchestrator import GeneralOrchestrator, OrchestratorConfig
from app.agents.pipeline_models import GeneralPipelineChunk
from app.config import get_settings
from lib.pipelines import build_general_pipeline


class OpenAIChatLLM:
    """LLM wrapper that generates answers using OpenAI's chat API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> None:
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be configured to generate answers.")
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def generate(
        self, *, prompt: str, context: List[GeneralPipelineChunk]
    ) -> str:
        messages = [
            {
                "role": "system",
                "content": "You are a helpful assistant answering questions about a scientific PDF. Use the provided excerpts to ground every answer and cite supporting evidence.",
            },
            {"role": "user", "content": prompt},
        ]

        def _call():
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
            return response.choices[0].message["content"].strip()

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _call)


@lru_cache
def get_general_orchestrator() -> GeneralOrchestrator:
    settings = get_settings()
    pipeline = build_general_pipeline()
    llm = OpenAIChatLLM(
        api_key=settings.openai_api_key,
        model=settings.default_model,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
    )
    config = OrchestratorConfig(
        confidence_threshold=settings.rag_confidence_threshold,
        top_k=settings.rag_rerank_top_k,
    )
    return GeneralOrchestrator(pipeline=pipeline, llm=llm, config=config)


__all__ = ["get_general_orchestrator", "OpenAIChatLLM"]
