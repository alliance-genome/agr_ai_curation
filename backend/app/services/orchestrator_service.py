"""Factories for building orchestrators used in API endpoints."""

from __future__ import annotations

from functools import lru_cache

from app.agents.main_orchestrator import (
    GeneralOrchestrator,
    OrchestratorConfig,
    build_pydantic_agent,
)
from app.config import get_settings
from lib.pipelines import build_general_pipeline


@lru_cache
def get_general_orchestrator() -> GeneralOrchestrator:
    settings = get_settings()
    pipeline = build_general_pipeline()
    agent = build_pydantic_agent(
        settings.default_model,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
    )
    config = OrchestratorConfig(
        confidence_threshold=settings.rag_confidence_threshold,
        top_k=settings.rag_rerank_top_k,
    )
    return GeneralOrchestrator(pipeline=pipeline, agent=agent, config=config)


__all__ = ["get_general_orchestrator"]
