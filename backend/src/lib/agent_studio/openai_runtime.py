"""OpenAI Agents SDK runtime for Agent Studio authoring conversations.

This module deliberately owns only provider orchestration. Authentication,
authorization, business-tool dispatch, durable chat rows, and curator approval
remain application concerns supplied by the API layer.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Sequence

from agents import (
    Agent,
    FunctionTool,
    ModelBehaviorError,
    ModelRefusalError,
    ModelSettings,
    RunConfig,
    Runner,
    ToolSearchTool,
    tool_namespace,
)
from openai.types.responses import ResponseTextDeltaEvent

from src.lib.observability.sentry import (
    gen_ai_conversation_scope,
    gen_ai_invoke_agent_span,
    set_redacted_ai_span_data,
    set_sentry_span_status,
)
from src.lib.openai_agents.config import build_model_settings
from src.lib.openai_agents.langfuse_client import is_openai_agents_tracing_enabled
from src.lib.openai_agents.runner import (
    build_owned_openai_responses_resources,
    close_owned_openai_resources,
)

logger = logging.getLogger(__name__)

AGENT_STUDIO_OPENAI_MODEL = "gpt-5.6-sol"
AGENT_STUDIO_REASONING_EFFORT = "medium"


@dataclass(frozen=True)
class ToolExecutionResult:
    """Application tool result in UI-complete and provider-bounded forms."""

    full_output: Any
    provider_output: str


ToolExecutor = Callable[
    [str, dict[str, Any], str | None],
    Awaitable[ToolExecutionResult],
]


@dataclass(frozen=True)
class ExecutedTool:
    tool_name: str
    call_id: str | None
    arguments: dict[str, Any]
    output: Any


@dataclass
class AgentStudioRunState:
    """Mutable result populated while the SDK-owned run is streamed."""

    trace_id: str
    assistant_text_parts: list[str] = field(default_factory=list)
    executed_tools: list[ExecutedTool] = field(default_factory=list)
    response_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    tool_search_calls: int = 0
    tool_search_outputs: int = 0
    tool_search_loaded_tools: int = 0

    @property
    def assistant_text(self) -> str:
        return "".join(self.assistant_text_parts)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(exclude_unset=True)
        if isinstance(dumped, Mapping):
            return dict(dumped)
    return {}


def _tool_call_details(item: Any) -> tuple[str, str | None, dict[str, Any]]:
    raw_item = getattr(item, "raw_item", None)
    raw = _mapping(raw_item)
    wire_tool_name = str(
        getattr(item, "tool_name", None)
        or raw.get("name")
        or getattr(raw_item, "name", None)
        or "tool"
    )
    # Hosted search exposes deferred functions as ``namespace.tool_name``.
    # Keep that provider-only namespace out of the stable application SSE and
    # audit contracts, which use the registered application tool name.
    tool_name = wire_tool_name.rsplit(".", 1)[-1]
    call_id_value = (
        getattr(item, "call_id", None)
        or raw.get("call_id")
        or raw.get("id")
        or getattr(raw_item, "call_id", None)
        or getattr(raw_item, "id", None)
    )
    call_id = str(call_id_value) if call_id_value is not None else None
    arguments_value = raw.get("arguments", getattr(raw_item, "arguments", None))
    if isinstance(arguments_value, str):
        try:
            parsed = json.loads(arguments_value)
        except json.JSONDecodeError:
            parsed = {}
    elif isinstance(arguments_value, Mapping):
        parsed = dict(arguments_value)
    else:
        parsed = {}
    return tool_name, call_id, parsed if isinstance(parsed, dict) else {}


def _execution_for_call(
    state: AgentStudioRunState,
    call_id: str | None,
) -> ExecutedTool | None:
    if call_id is not None:
        for execution in reversed(state.executed_tools):
            if execution.call_id == call_id:
                return execution
    return state.executed_tools[-1] if state.executed_tools else None


def _build_function_tool(
    definition: Mapping[str, Any],
    *,
    executor: ToolExecutor,
    state: AgentStudioRunState,
    defer_loading: bool,
) -> FunctionTool:
    name = str(definition.get("name") or "").strip()
    description = str(definition.get("description") or "").strip()
    schema = definition.get("input_schema")
    if not name or not isinstance(schema, Mapping):
        raise ValueError("Agent Studio tool definitions require name and input_schema")

    async def invoke(context: Any, input_json: str) -> str:
        try:
            parsed = json.loads(input_json or "{}")
        except json.JSONDecodeError as exc:
            parsed = {"_invalid_json": str(exc)}
        if not isinstance(parsed, dict):
            parsed = {"_invalid_input_type": type(parsed).__name__}
        call_id_value = getattr(context, "tool_call_id", None)
        call_id = str(call_id_value) if call_id_value is not None else None
        result = await executor(name, parsed, call_id)
        state.executed_tools.append(
            ExecutedTool(
                tool_name=name,
                call_id=call_id,
                arguments=parsed,
                output=result.full_output,
            )
        )
        return result.provider_output

    return FunctionTool(
        name=name,
        description=description,
        params_json_schema=dict(schema),
        on_invoke_tool=invoke,
        strict_json_schema=False,
        defer_loading=defer_loading,
    )


def build_agent_studio_tools(
    definitions: Sequence[Mapping[str, Any]],
    *,
    executor: ToolExecutor,
    state: AgentStudioRunState,
    namespace_for_tool: Callable[[str], tuple[str, str]],
    forced_tool_name: str | None = None,
) -> tuple[list[Any], dict[str, int]]:
    """Build one hosted-search surface from an already authorized tool universe."""

    eager: list[FunctionTool] = []
    grouped: dict[tuple[str, str], list[FunctionTool]] = {}
    for definition in definitions:
        name = str(definition.get("name") or "").strip()
        is_forced = bool(forced_tool_name and name == forced_tool_name)
        tool = _build_function_tool(
            definition,
            executor=executor,
            state=state,
            defer_loading=not is_forced,
        )
        if is_forced:
            eager.append(tool)
            continue
        namespace = namespace_for_tool(name)
        grouped.setdefault(namespace, []).append(tool)

    deferred: list[FunctionTool] = []
    for (namespace_name, namespace_description), tools in grouped.items():
        deferred.extend(
            tool_namespace(
                name=namespace_name,
                description=namespace_description,
                tools=tools,
            )
        )

    tools: list[Any] = [*eager, *deferred]
    if deferred:
        tools.insert(
            0,
            ToolSearchTool(
                execution="server",
            ),
        )
    return tools, {
        "candidate_count": len(definitions),
        "eager_count": len(eager),
        "deferred_count": len(deferred),
        "namespace_count": len(grouped),
    }


def build_agent_studio_model_settings(
    *,
    max_output_tokens: int,
    tool_choice: str | None = None,
) -> ModelSettings:
    """Return the exact OpenAI Responses settings required by Agent Studio."""

    shared_settings = build_model_settings(
        model=AGENT_STUDIO_OPENAI_MODEL,
        reasoning_effort=AGENT_STUDIO_REASONING_EFFORT,
        tool_choice=tool_choice,
        parallel_tool_calls=False,
        include_usage=True,
        provider_override="openai",
    )
    return replace(
        shared_settings,
        truncation="auto",
        max_tokens=max_output_tokens,
        store=True,
    )


def _run_config(
    *,
    state: AgentStudioRunState,
    session_id: str,
    user_id: str,
    model_provider: Any,
) -> RunConfig:
    return RunConfig(
        model_provider=model_provider,
        tracing_disabled=not is_openai_agents_tracing_enabled(),
        trace_include_sensitive_data=True,
        workflow_name="Agent Studio AI Chat",
        group_id=session_id,
        trace_metadata={
            "langfuse_trace_id": state.trace_id,
            "session_id": session_id,
            "user_id": user_id,
            "provider": "openai",
            "model": AGENT_STUDIO_OPENAI_MODEL,
            "reasoning_effort": AGENT_STUDIO_REASONING_EFFORT,
        },
    )


def _capture_terminal_metadata(result: Any, state: AgentStudioRunState) -> None:
    state.response_id = getattr(result, "last_response_id", None)
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    if usage is None:
        return
    state.input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    state.output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    state.cached_input_tokens = int(getattr(input_details, "cached_tokens", 0) or 0)
    state.reasoning_tokens = int(getattr(output_details, "reasoning_tokens", 0) or 0)


@contextmanager
def _tracked_agent_span(**kwargs: Any):
    """Give the manual Sentry span a sanitized terminal outcome and status."""

    with gen_ai_invoke_agent_span(**kwargs) as span:
        try:
            yield span
        except BaseException as exc:
            if isinstance(exc, ModelRefusalError):
                outcome = "refusal"
                status = "ok"
            elif isinstance(exc, ModelBehaviorError) and "response.incomplete" in str(exc).lower():
                outcome = "incomplete"
                status = "internal_error"
            elif type(exc).__name__ == "CancelledError":
                outcome = "cancelled"
                status = "cancelled"
            else:
                outcome = "error"
                status = "internal_error"
            if span is not None:
                set_sentry_span_status(span, status)
                set_redacted_ai_span_data(
                    span,
                    "ai_curation.agent_studio.outcome",
                    outcome,
                )
                set_redacted_ai_span_data(
                    span,
                    "ai_curation.error.detail",
                    {
                        "phase": str(kwargs.get("workflow") or "agent_studio"),
                        "error_type": type(exc).__name__,
                    },
                )
            raise
        else:
            if span is not None:
                set_sentry_span_status(span, "ok")
                set_redacted_ai_span_data(
                    span,
                    "ai_curation.agent_studio.outcome",
                    "completed",
                )


async def stream_agent_studio_run(
    *,
    instructions: str,
    input_items: list[dict[str, Any]],
    tools: list[Any],
    state: AgentStudioRunState,
    session_id: str,
    user_id: str,
    max_turns: int,
    model_settings: ModelSettings,
) -> AsyncIterator[dict[str, Any]]:
    """Run the SDK-owned authoring loop and emit provider-neutral Studio events."""

    resources = build_owned_openai_responses_resources()
    run_config = _run_config(
        state=state,
        session_id=session_id,
        user_id=user_id,
        model_provider=resources.provider,
    )
    agent = Agent(
        name="Agent Studio Authoring Assistant",
        instructions=instructions,
        model=AGENT_STUDIO_OPENAI_MODEL,
        model_settings=model_settings,
        tools=tools,
    )
    pending_calls: dict[str, tuple[str, dict[str, Any]]] = {}
    try:
        with gen_ai_conversation_scope(session_id):
            with _tracked_agent_span(
                agent_name="Agent Studio Authoring Assistant",
                model=AGENT_STUDIO_OPENAI_MODEL,
                conversation_id=session_id,
                provider_name="openai",
                response_streaming=True,
                workflow="agent_studio_authoring",
                agent_key="agent_studio_authoring",
                agent_source="runtime",
                trace_id=state.trace_id,
                span_data={
                    "ai_curation.agent_studio.input_item_count": len(input_items),
                    "ai_curation.agent_studio.tool_count": len(tools),
                },
            ):
                result = Runner.run_streamed(
                    agent,
                    input=input_items,
                    max_turns=max_turns,
                    run_config=run_config,
                )
                async for event in result.stream_events():
                    if getattr(event, "type", None) == "raw_response_event":
                        data = getattr(event, "data", None)
                        if isinstance(data, ResponseTextDeltaEvent):
                            delta = str(getattr(data, "delta", "") or "")
                            if delta:
                                state.assistant_text_parts.append(delta)
                                yield {"type": "TEXT_DELTA", "delta": delta}
                        continue

                    if getattr(event, "type", None) != "run_item_stream_event":
                        continue
                    event_name = getattr(event, "name", None)
                    item = getattr(event, "item", None)
                    if event_name == "tool_search_called":
                        state.tool_search_calls += 1
                        yield {"type": "TOOL_SEARCH", "status": "searching"}
                    elif event_name == "tool_search_output_created":
                        state.tool_search_outputs += 1
                        raw = _mapping(getattr(item, "raw_item", None))
                        loaded_tools = raw.get("tools")
                        loaded_count = len(loaded_tools) if isinstance(loaded_tools, list) else 0
                        state.tool_search_loaded_tools += loaded_count
                        yield {
                            "type": "TOOL_SEARCH_RESULT",
                            "status": "loaded",
                            "loaded_tool_count": loaded_count,
                        }
                    elif getattr(item, "type", None) == "tool_call_item":
                        tool_name, call_id, arguments = _tool_call_details(item)
                        if call_id:
                            pending_calls[call_id] = (tool_name, arguments)
                        yield {
                            "type": "TOOL_USE",
                            "tool_name": tool_name,
                            "tool_input": arguments,
                            "call_id": call_id,
                        }
                    elif getattr(item, "type", None) == "tool_call_output_item":
                        call_id_value = getattr(item, "call_id", None)
                        call_id = str(call_id_value) if call_id_value is not None else None
                        execution = _execution_for_call(state, call_id)
                        pending = pending_calls.pop(call_id, None) if call_id else None
                        yield {
                            "type": "TOOL_RESULT",
                            "tool_name": (
                                execution.tool_name
                                if execution is not None
                                else (pending[0] if pending else "tool")
                            ),
                            "result": (
                                execution.output
                                if execution is not None
                                else getattr(item, "output", None)
                            ),
                            "call_id": call_id,
                        }
                if not state.assistant_text:
                    final_output = getattr(result, "final_output", None)
                    if isinstance(final_output, str) and final_output:
                        state.assistant_text_parts.append(final_output)
                _capture_terminal_metadata(result, state)
    finally:
        await close_owned_openai_resources(
            resources,
            trace_id=state.trace_id,
            user_id=user_id,
        )


async def run_forced_agent_studio_tool(
    *,
    instructions: str,
    input_items: list[dict[str, Any]],
    tool_definition: Mapping[str, Any],
    executor: ToolExecutor,
    state: AgentStudioRunState,
    session_id: str,
    user_id: str,
    max_turns: int,
    max_output_tokens: int,
) -> ExecutedTool | None:
    """Run a bounded SDK turn that must execute one named application tool."""

    tool_name = str(tool_definition.get("name") or "").strip()
    tools, _ = build_agent_studio_tools(
        [tool_definition],
        executor=executor,
        state=state,
        namespace_for_tool=lambda _name: ("studio_submission", "Suggestion submission"),
        forced_tool_name=tool_name,
    )
    resources = build_owned_openai_responses_resources()
    run_config = _run_config(
        state=state,
        session_id=session_id,
        user_id=user_id,
        model_provider=resources.provider,
    )
    agent = Agent(
        name="Agent Studio Suggestion Assistant",
        instructions=instructions,
        model=AGENT_STUDIO_OPENAI_MODEL,
        model_settings=build_agent_studio_model_settings(
            max_output_tokens=max_output_tokens,
            tool_choice=tool_name,
        ),
        tools=tools,
        tool_use_behavior="stop_on_first_tool",
    )
    try:
        with gen_ai_conversation_scope(session_id):
            with _tracked_agent_span(
                agent_name="Agent Studio Suggestion Assistant",
                model=AGENT_STUDIO_OPENAI_MODEL,
                conversation_id=session_id,
                provider_name="openai",
                response_streaming=False,
                workflow="agent_studio_suggestion",
                agent_key="agent_studio_suggestion",
                agent_source="runtime",
                trace_id=state.trace_id,
                span_data={
                    "ai_curation.agent_studio.input_item_count": len(input_items),
                    "ai_curation.agent_studio.tool_count": len(tools),
                },
            ):
                result = await Runner.run(
                    agent,
                    input=input_items,
                    max_turns=max_turns,
                    run_config=run_config,
                )
        _capture_terminal_metadata(result, state)
        return state.executed_tools[-1] if state.executed_tools else None
    finally:
        await close_owned_openai_resources(
            resources,
            trace_id=state.trace_id,
            user_id=user_id,
        )
