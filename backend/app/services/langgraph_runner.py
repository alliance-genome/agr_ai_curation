"""Utilities for executing LangGraph supervisors."""

from __future__ import annotations

from contextlib import suppress
from time import time
from typing import Any, AsyncIterator, Dict, Optional

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

    async def stream(self, state: PDFQAState) -> AsyncIterator[Dict[str, Any]]:
        """Yield high-level events produced by the graph."""

        if not hasattr(self._graph, "astream_events"):
            raise RuntimeError(
                "Compiled graph does not expose 'astream_events'; upgrade LangGraph or "
                "fall back to synchronous execution."
            )

        config = {"thread_id": str(state.session_id)}
        final_state: Optional[PDFQAState] = None
        final_emitted = False

        async for event in self._graph.astream_events(
            state, config=config, version="v2"
        ):
            event_type = event.get("event")
            node_name = event.get("name") or "unknown"
            data = event.get("data", {}) or {}

            if event_type == "on_node_start":
                yield {
                    "type": "agent_start",
                    "agent": node_name,
                    "timestamp": time(),
                }
                continue

            if event_type == "on_chat_model_stream":
                chunk = data.get("chunk")
                text = ""
                if isinstance(chunk, str):
                    text = chunk
                elif isinstance(chunk, dict):
                    text = (
                        chunk.get("content")
                        or chunk.get("text")
                        or "".join(
                            part.get("text", "") for part in chunk.get("content", [])
                        )
                    )
                if text:
                    yield {
                        "type": "delta",
                        "content": text,
                        "agent": node_name,
                        "timestamp": time(),
                    }
                continue

            if event_type == "on_node_error":
                error_payload = data.get("error") or event.get("error")
                yield {
                    "type": "error",
                    "agent": node_name,
                    "message": str(error_payload) if error_payload else "unknown error",
                    "timestamp": time(),
                }
                continue

            if event_type == "on_node_finish":
                node_output = data.get("output") or event.get("output")
                candidate_state: Optional[PDFQAState] = None
                with suppress(Exception):
                    if isinstance(node_output, PDFQAState):
                        candidate_state = node_output
                    elif isinstance(node_output, dict):
                        candidate_state = state.model_copy(update=node_output)
                    elif node_output is not None:
                        candidate_state = PDFQAState.model_validate(node_output)

                if candidate_state and candidate_state.answer:
                    final_state = candidate_state
                    final_emitted = True
                    yield {
                        "type": "final",
                        "answer": candidate_state.answer or "",
                        "citations": candidate_state.citations,
                        "metadata": candidate_state.metadata,
                        "state": candidate_state,
                    }
                    yield {
                        "type": "agent_finish",
                        "agent": node_name,
                        "timestamp": time(),
                    }
                    break

                yield {
                    "type": "agent_finish",
                    "agent": node_name,
                    "timestamp": time(),
                }
                continue

            if event_type in {"on_chain_end", "on_graph_end"}:
                output = data.get("output") or event.get("output")
                candidate_state = None
                with suppress(Exception):
                    if isinstance(output, PDFQAState):
                        candidate_state = output
                    elif isinstance(output, dict):
                        candidate_state = state.model_copy(update=output)
                    elif output is not None:
                        candidate_state = PDFQAState.model_validate(output)

                if candidate_state and not final_emitted:
                    final_state = candidate_state
                    final_emitted = True
                    yield {
                        "type": "final",
                        "answer": candidate_state.answer or "",
                        "citations": candidate_state.citations,
                        "metadata": candidate_state.metadata,
                        "state": candidate_state,
                    }
                    break

        if not final_emitted:
            final_state = final_state or await self.run(state)
            yield {
                "type": "final",
                "answer": final_state.answer or "",
                "citations": final_state.citations,
                "metadata": final_state.metadata,
                "state": final_state,
            }


__all__ = ["LangGraphRunner"]
