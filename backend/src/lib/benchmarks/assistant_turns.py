"""Benchmark Chat producer using the existing executable-run observation manager."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any
from uuid import uuid4

from anyio.to_thread import run_sync

from src.lib.agent_studio.openai_runtime import (
    AGENT_STUDIO_OPENAI_MODEL, AGENT_STUDIO_REASONING_EFFORT, AgentStudioRunState,
)
from src.lib.benchmarks.assistant_history import complete_assistant_turn
from src.lib.benchmarks.assistant_runtime import stream_benchmark_assistant
from src.lib.benchmarks.observability import sanitized_benchmark_error
from src.lib.benchmarks.assistant_tool_bridge import AssistantToolBridge, PAPER_DRAFT_TOOL, PAPER_PROPOSAL_TOOL
from src.lib.chat_history_repository import ChatHistoryRepository
from src.lib.openai_agents.config import get_executable_run_event_replay_limit
from src.lib.observability.runtime import report_runtime_exception
from src.lib.executable_runs import executable_run_manager
from src.models.sql.database import SessionLocal

# Live tool replies share the existing run manager's single-worker lifecycle.
# Never use this map as a durable lease or as an authentication mechanism.
active_bridges: dict[str, AssistantToolBridge] = {}


def run_id(session_id: str, turn_id: str) -> str:
    return f"benchmark_assistant_turn:{session_id}:{turn_id}"


def event(session_id: str, turn_id: str, kind: str, **values: Any) -> str:
    return "data: " + json.dumps({
        "type": kind, "session_id": session_id, "turn_id": turn_id, **values,
    }, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n\n"


def _persist(*, owner: str, session_id: str, turn_id: str, state: AgentStudioRunState,
             status: str, elapsed: float) -> None:
    usage = {name: getattr(state, name) if state.usage_observed else None for name in (
        "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens",
    )}
    with SessionLocal() as db:
        complete_assistant_turn(
            ChatHistoryRepository(db), subject=owner, session_id=session_id, turn_id=turn_id,
            message=state.assistant_text or {
                "cancelled": "Stopped at your request.",
                "failed": "This turn could not be completed.",
                "completed": "No response was produced.",
            }[status], trace_id=state.trace_id,
            payload={
                "status": status, "assistant_usage": {
                    **usage, "cost_usd": None, "cost_status": "unknown",
                    "elapsed_seconds": elapsed, "model": AGENT_STUDIO_OPENAI_MODEL,
                    "reasoning_effort": AGENT_STUDIO_REASONING_EFFORT,
                    "accounting_scope": "benchmark_assistance",
                },
                "tool_calls": [{"name": item.tool_name, "call_id": item.call_id,
                                "arguments": item.arguments, "output": item.output}
                               for item in state.executed_tools],
            },
        )
        db.commit()


async def produce_turn(*, owner: str, session_id: str, turn_id: str,
                       input_items: list[dict[str, Any]], cancel_event: asyncio.Event) -> AsyncIterator[str]:
    bridge = AssistantToolBridge(owner=owner, cancel_event=cancel_event)
    key = run_id(session_id, turn_id)
    if key in active_bridges:
        raise RuntimeError("Assistant producer already exists")
    active_bridges[key] = bridge
    state = AgentStudioRunState(trace_id=uuid4().hex)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
        maxsize=get_executable_run_event_replay_limit(),
    )
    started = time.monotonic()
    status = "completed"

    async def tools():
        while True:
            await queue.put(await bridge.requests.get())

    async def model():
        nonlocal status
        try:
            async for item in stream_benchmark_assistant(
                input_items=input_items, definitions=[PAPER_DRAFT_TOOL, PAPER_PROPOSAL_TOOL], executor=bridge.execute,
                state=state, session_id=session_id, user_id=owner, cancel_event=cancel_event,
            ):
                await queue.put(item)
            if cancel_event.is_set():
                status = "cancelled"
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception as exc:
            # Raw provider/tool exceptions may contain paper text or credentials.
            # The persisted status and trace identity retain a safe investigation handle.
            status = "failed"
            report_runtime_exception(
                sanitized_benchmark_error("assistant_turn", type(exc).__name__),
                component="benchmark_assistant", operation="assistant_turn_failed",
                context={"usage_observed": state.usage_observed},
            )
            await queue.put({"type": "ERROR", "code": "assistant_turn_failed",
                             "message": "Chat could not finish this turn. Your draft was not changed."})
        finally:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(None)
            else:
                await queue.put(None)

    model_task = asyncio.create_task(model())
    tool_task = asyncio.create_task(tools())
    try:
        yield event(session_id, turn_id, "STARTED", trace_id=state.trace_id)
        while (item := await queue.get()) is not None:
            kind = item["type"]
            yield event(session_id, turn_id, kind, **{k: v for k, v in item.items() if k != "type"})
        saved = False
        try:
            await run_sync(lambda: _persist(
                owner=owner, session_id=session_id, turn_id=turn_id, state=state,
                status=status, elapsed=time.monotonic() - started,
            ))
            saved = True
        except Exception:
            pass
        if not saved:
            # Raise outside the raw SQL exception context; manager logs are not
            # allowed to include parameters containing private conversation data.
            raise RuntimeError("Assistant history save failed")
        if status == "failed":
            await executable_run_manager.set_outcome_status(key, "failed")
        yield event(session_id, turn_id, "CANCELLED" if status == "cancelled" else "DONE",
                    status=status, trace_id=state.trace_id)
    finally:
        bridge.close()
        active_bridges.pop(key, None)
        tool_task.cancel()
        model_task.cancel()
        for task in (tool_task, model_task):
            with suppress(asyncio.CancelledError):
                await task
