"""Utilities for executing LangGraph supervisors."""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.orchestration.general_supervisor import PDFQAState


class LangGraphRunner:
    """Lightweight wrapper around a LangGraph compiled app."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def run(self, state: PDFQAState) -> PDFQAState:
        """Execute the graph and return the final state."""
        config = {"thread_id": str(state.session_id)}
        result = await self._graph.ainvoke(state, config=config)
        if isinstance(result, PDFQAState):
            return result
        return PDFQAState.model_validate(result)

    async def stream(self, state: PDFQAState) -> AsyncIterator[PDFQAState]:
        """Yield interim chunks. Currently returns a single final chunk."""
        config = {"thread_id": str(state.session_id)}
        result = await self._graph.ainvoke(state, config=config)
        if not isinstance(result, PDFQAState):
            result = PDFQAState.model_validate(result)
        yield result


__all__ = ["LangGraphRunner"]
