"""
Streaming runner for OpenAI Agents SDK with Langfuse observability.

This module provides a streaming runner that adapts OpenAI Agents SDK
streaming events to SSE-compatible events for the existing frontend.

Langfuse Integration:
    Uses manual span management with start_span() and explicit span.end()
    to avoid OTEL context cleanup issues in async generators. Context managers
    can cause "Failed to detach context" errors when the generator is garbage
    collected in a different async context than where the span was created.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, Any, Optional, List

from agents import Agent, Runner, RunConfig, set_default_openai_client, set_default_openai_api
from agents.models.openai_provider import OpenAIProvider
from openai.types.responses import (
    ResponseTextDeltaEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseReasoningSummaryTextDeltaEvent
)
from pydantic import ValidationError

# Import Langfuse-wrapped OpenAI client for automatic tracing
from langfuse.openai import AsyncOpenAI as LangfuseAsyncOpenAI

from .langfuse_client import (
    flush_langfuse,
    is_langfuse_configured,
    get_langfuse,
    flush_agent_configs,
    clear_pending_configs
)
from .config import get_api_key, get_base_url, is_gemini_provider

# Prompt context tracking for execution logging
from src.lib.prompts.context import clear_prompt_context, commit_pending_prompts, get_used_prompts
from src.lib.prompts.service import PromptService
from src.models.sql.database import SessionLocal

# Request-scoped context for tools (trace_id captured via closure)
from src.lib.context import set_current_trace_id

# Logger must be defined early since _create_openai_client_kwargs uses it at module load
logger = logging.getLogger(__name__)


def _configure_api_mode():
    """Configure the OpenAI API mode based on the provider.

    Gemini's OpenAI compatibility layer only supports the Chat Completions API,
    not the newer Responses API. When using Gemini, we must switch to chat_completions mode.
    """
    if is_gemini_provider():
        set_default_openai_api("chat_completions")
        logger.info("[LLM Client] Using chat_completions API mode for Gemini compatibility")
    else:
        # OpenAI supports the Responses API (default)
        logger.info("[LLM Client] Using responses API mode (OpenAI default)")


# Configure API mode at module load
_configure_api_mode()


def _create_openai_client_kwargs() -> dict:
    """Build kwargs for OpenAI client based on configured provider.

    For Gemini, sets api_key and base_url for OpenAI compatibility mode.
    For OpenAI, returns empty dict (uses defaults from environment).
    """
    kwargs = {}

    api_key = get_api_key()
    base_url = get_base_url()

    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    provider = "gemini" if is_gemini_provider() else "openai"
    logger.info(f"[LLM Client] Using provider: {provider}, base_url: {base_url or 'default'}")

    return kwargs


class SafeLangfuseAsyncOpenAI(LangfuseAsyncOpenAI):
    """Wrapper that ensures metadata is always a dict before passing to Langfuse.

    The OpenAI Agents SDK sometimes passes metadata=None, which Langfuse rejects.
    This wrapper ensures compatibility between the two libraries.

    Supports both OpenAI and Gemini (via OpenAI compatibility mode) based on
    the LLM_PROVIDER environment variable.

    Note: Trace context is handled automatically via OpenTelemetry context propagation.
    When used inside a start_as_current_span() context, all calls are automatically
    nested under that parent span.
    """

    def __init__(self, *args, **kwargs):
        # Merge provider-specific kwargs with any passed kwargs
        provider_kwargs = _create_openai_client_kwargs()
        merged_kwargs = {**provider_kwargs, **kwargs}
        super().__init__(*args, **merged_kwargs)
        self._wrap_responses_api()
        self._wrap_chat_api()

    def _wrap_responses_api(self):
        """Wrap responses.create to sanitize metadata."""
        if hasattr(self, 'responses') and hasattr(self.responses, 'create'):
            original_create = self.responses.create

            async def safe_create(*args, **kwargs):
                # Ensure metadata is a dict (SDK sometimes passes None)
                if 'metadata' in kwargs and not isinstance(kwargs.get('metadata'), dict):
                    kwargs['metadata'] = kwargs['metadata'] if kwargs['metadata'] else {}
                return await original_create(*args, **kwargs)

            self.responses.create = safe_create

    def _wrap_chat_api(self):
        """Wrap chat.completions.create to sanitize metadata."""
        if hasattr(self, 'chat') and hasattr(self.chat, 'completions'):
            original_create = self.chat.completions.create

            async def safe_create(*args, **kwargs):
                # Ensure metadata is a dict (SDK sometimes passes None)
                if 'metadata' in kwargs and not isinstance(kwargs.get('metadata'), dict):
                    kwargs['metadata'] = kwargs['metadata'] if kwargs['metadata'] else {}
                return await original_create(*args, **kwargs)

            self.chat.completions.create = safe_create


# Set our SafeLangfuseAsyncOpenAI as the default client for all agents
# This ensures nested agent runs via as_tool() also use our safe wrapper
# that handles metadata=None gracefully
_default_client = SafeLangfuseAsyncOpenAI()
set_default_openai_client(_default_client)


def _now_iso() -> str:
    """Return current UTC time in ISO format for audit events."""
    return datetime.now(timezone.utc).isoformat()


def _log_used_prompts_to_db(
    trace_id: str,
    session_id: Optional[str] = None,
    span: Optional[Any] = None,
) -> int:
    """Log all used prompts to the database and Langfuse trace.

    Called after agent execution completes to record which prompt versions
    were used in this request for audit trail.

    Args:
        trace_id: Langfuse trace ID for correlation
        session_id: Chat session ID (optional)
        span: Langfuse span to add prompt version metadata (optional)

    Returns:
        Number of prompts logged
    """
    used_prompts = get_used_prompts()
    if not used_prompts:
        logger.debug("[Prompt Logging] No prompts to log")
        return 0

    # Add prompt version metadata to Langfuse span if provided
    # Note: Using span.update(metadata=...) since span.event() only exists on trace objects
    if span:
        try:
            prompt_versions = [
                {
                    "agent": p.agent_name,
                    "type": p.prompt_type,
                    "group": p.group_id,
                    "version": p.version,
                    "id": str(p.id),
                }
                for p in used_prompts
            ]
            span.update(
                metadata={
                    "prompts_used": prompt_versions,
                    "prompt_count": len(prompt_versions),
                }
            )
            logger.debug(f"[Langfuse] Added {len(prompt_versions)} prompt versions to span metadata")
        except Exception as e:
            # Non-critical - continue even if Langfuse update fails
            logger.warning(f"[Langfuse] Failed to add prompt versions to span: {e}")

    try:
        db = SessionLocal()
        try:
            service = PromptService(db)
            entries = service.log_all_used_prompts(
                prompts=used_prompts,
                trace_id=trace_id,
                session_id=session_id,
            )
            db.commit()
            logger.info(
                f"[Prompt Logging] Logged {len(entries)} prompt usages to database "
                f"(trace_id={trace_id[:16]}...)"
            )
            return len(entries)
        finally:
            db.close()
    except Exception as e:
        # Log error but don't fail the request - prompt logging is non-critical
        logger.error(f"[Prompt Logging] Failed to log prompts: {e}", exc_info=True)
        return 0

from .agents.supervisor_agent import create_supervisor_agent
from .config import get_max_turns
from .guardrails import enforce_uncited_negative_guardrail
from .models import Answer
from .streaming_tools import (
    get_collected_events,
    clear_collected_events,
    set_live_event_list,
    get_live_event_list,
    reset_consecutive_call_tracker,
    SpecialistOutputError,
)

# Note: logger is defined earlier in the file (line 39) for use during module initialization


async def _run_agent_with_tracing(
    agent: Agent,
    input_items: List[Dict[str, Any]],
    user_id: str,
    document_id: Optional[str],
    document_name: Optional[str],
    user_message: str,
    trace_id: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Internal generator that runs the agent within Langfuse trace context.

    This function is called from within a start_as_current_span() context,
    so all OpenAI calls made by the agent are automatically nested.

    REAL-TIME STREAMING:
    Uses an asyncio.Queue to receive specialist events in real-time.
    Events are drained from the queue concurrently with SDK event processing,
    giving immediate visibility into specialist agent activity.
    """
    full_response = ""
    structured_result = None
    tools_called: List[str] = []
    tool_calls_count = 0
    current_agent = agent.name
    agents_used = [agent.name]
    is_generating = False  # Track if we've emitted AGENT_GENERATING for current generation phase

    # Create Langfuse-wrapped OpenAI client
    # It will automatically pick up the active span context via OpenTelemetry
    langfuse_openai_client = SafeLangfuseAsyncOpenAI()
    langfuse_provider = OpenAIProvider(openai_client=langfuse_openai_client)
    run_config = RunConfig(
        model_provider=langfuse_provider,
        tracing_disabled=True,  # Disable OpenAI SDK's built-in tracing, using Langfuse
    )

    # Create live event list for real-time specialist event streaming
    # Events appended to this list are yielded during stream processing
    live_events: List[Dict[str, Any]] = []
    set_live_event_list(live_events)
    logger.info("[OpenAI Agents] Live event list enabled for real-time streaming")

    # max_turns from config gives agents more time to think and process complex queries
    max_turns = get_max_turns()
    result = Runner.run_streamed(agent, input=input_items, max_turns=max_turns, run_config=run_config)

    # Track position in live_events list for yielding new events
    live_events_yielded = 0

    # Create a concurrent event generator using separate tasks
    # This allows live events to be yielded even while SDK is executing tools
    async def interleaved_events():
        """
        Interleave SDK stream events with live specialist events using concurrent tasks.

        The SDK stream runs in a background task, putting events onto a queue.
        The main loop polls both the queue and live_events list, allowing
        real-time visibility into specialist activity during tool execution.
        """
        nonlocal live_events_yielded

        # Queue for SDK events from background task
        sdk_queue: asyncio.Queue = asyncio.Queue()

        async def sdk_producer():
            """Background task that consumes SDK events and puts them on the queue."""
            try:
                async for event in result.stream_events():
                    await sdk_queue.put(("sdk", event))
            except Exception as e:
                logger.error(f"[OpenAI Agents] SDK producer error: {e}")
                await sdk_queue.put(("error", e))
            finally:
                await sdk_queue.put(None)  # Sentinel to signal completion

        # Start SDK producer as background task
        sdk_task = asyncio.create_task(sdk_producer())
        logger.info("[OpenAI Agents] Started SDK producer task for concurrent streaming")

        try:
            while True:
                # First, yield any new live events that have accumulated
                # This runs every iteration, even when SDK is blocked on tools
                while live_events_yielded < len(live_events):
                    specialist_event = live_events[live_events_yielded]
                    live_events_yielded += 1
                    logger.info(f"[OpenAI Agents] Yielding live specialist event: {specialist_event.get('type')}")
                    yield ("live", specialist_event)

                # Try to get SDK event with short timeout
                # Timeout allows us to re-check live_events periodically
                try:
                    item = await asyncio.wait_for(sdk_queue.get(), timeout=0.05)
                    if item is None:
                        # SDK stream completed
                        logger.info("[OpenAI Agents] SDK stream completed")
                        break
                    if item[0] == "error":
                        # CRITICAL: Yield remaining live events BEFORE re-raising
                        # This ensures SPECIALIST_RETRY warnings are visible to users
                        # even when the retry ultimately fails
                        while live_events_yielded < len(live_events):
                            specialist_event = live_events[live_events_yielded]
                            live_events_yielded += 1
                            logger.info(
                                f"[OpenAI Agents] Yielding live event before error: "
                                f"{specialist_event.get('type')}"
                            )
                            yield ("live", specialist_event)

                        # Now re-raise SDK errors
                        raise item[1]
                    yield item
                except asyncio.TimeoutError:
                    # No SDK event yet, loop continues to check live_events
                    pass

            # Yield any remaining live events after stream ends
            while live_events_yielded < len(live_events):
                specialist_event = live_events[live_events_yielded]
                live_events_yielded += 1
                logger.info(f"[OpenAI Agents] Yielding final live specialist event: {specialist_event.get('type')}")
                yield ("live", specialist_event)

        finally:
            # Clean up background task
            if not sdk_task.done():
                sdk_task.cancel()
                try:
                    await sdk_task
                except asyncio.CancelledError:
                    pass

    try:
        async for event_source, event in interleaved_events():
            # Handle live events (specialist internal tools)
            if event_source == "live":
                yield event
                continue

            # Handle SDK events
            event_type = getattr(event, "type", None)

            if event_type == "raw_response_event":
                # Handle raw LLM response events
                data = getattr(event, "data", None)
                if data is not None:
                    # Only stream TEXT deltas to chat (not function call arguments)
                    if isinstance(data, ResponseTextDeltaEvent):
                        delta = getattr(data, "delta", None)
                        if delta:
                            # Emit AGENT_GENERATING once when text streaming starts
                            if not is_generating:
                                is_generating = True
                                logger.info(f"[OpenAI Agents] Agent generating response: {current_agent}")
                                yield {
                                    "type": "AGENT_GENERATING",
                                    "timestamp": _now_iso(),
                                    "details": {
                                        "agentRole": current_agent,
                                        "agentDisplayName": current_agent,
                                        "message": "Agent reasoning"
                                    }
                                }
                            full_response += delta
                            yield {
                                "type": "TEXT_MESSAGE_CONTENT",
                                "data": {"delta": delta}
                            }
                    elif isinstance(data, ResponseFunctionCallArgumentsDeltaEvent):
                        # Function call arguments - send to audit panel only
                        delta = getattr(data, "delta", None)
                        if delta:
                            yield {
                                "type": "TOOL_CALL_ARGS",
                                "data": {"delta": delta}
                            }
                    elif isinstance(data, ResponseReasoningSummaryTextDeltaEvent):
                        # Reasoning summary text - show in audit panel
                        delta = getattr(data, "delta", None)
                        if delta:
                            yield {
                                "type": "AGENT_THINKING",
                                "timestamp": _now_iso(),
                                "details": {
                                    "agentRole": current_agent,
                                    "agentDisplayName": current_agent,
                                    "message": delta
                                }
                            }

            elif event_type == "run_item_stream_event":
                # Handle structured events (tool calls, outputs, messages)
                item = getattr(event, "item", None)
                if item is not None:
                    item_type = getattr(item, "type", None)

                    if item_type == "tool_call_item":
                        tool_calls_count += 1
                        is_generating = False  # Reset for next generation phase after tool completes
                        # Try multiple attributes to get tool name
                        tool_name = (
                            getattr(item, "name", None) or
                            getattr(item, "tool_name", None) or
                            getattr(getattr(item, "raw_item", None), "name", None) or
                            "tool"
                        )
                        # Try to get tool arguments
                        tool_args = None
                        raw_item = getattr(item, "raw_item", None)
                        if raw_item:
                            tool_args_str = getattr(raw_item, "arguments", None)
                            if tool_args_str:
                                try:
                                    tool_args = json.loads(tool_args_str)
                                except Exception:
                                    pass
                        tools_called.append(tool_name)
                        logger.info(f"[OpenAI Agents] Tool call started: {tool_name}")
                        # Audit event: TOOL_START
                        yield {
                            "type": "TOOL_START",
                            "timestamp": _now_iso(),
                            "details": {
                                "toolName": tool_name,
                                "friendlyName": f"Calling {tool_name}...",
                                "agent": current_agent,
                                "toolArgs": tool_args
                            }
                        }

                    elif item_type == "tool_call_output_item":
                        output = getattr(item, "output", "")
                        # Truncate long outputs for the preview
                        output_preview = str(output)[:300]
                        if len(str(output)) > 300:
                            output_preview += "..."
                        logger.info(f"[OpenAI Agents] Tool call completed, output length: {len(str(output))}")
                        # Get last tool name for the completion event
                        last_tool = tools_called[-1] if tools_called else "tool"

                        # Emit any remaining collected specialist events (fallback for batch mode)
                        # Most events should have been streamed via queue, this catches any stragglers
                        specialist_events = get_collected_events()
                        if specialist_events:
                            logger.info(f"[OpenAI Agents] Emitting {len(specialist_events)} remaining specialist events")
                            for specialist_event in specialist_events:
                                yield specialist_event
                            clear_collected_events()

                        # Audit event: TOOL_COMPLETE
                        yield {
                            "type": "TOOL_COMPLETE",
                            "timestamp": _now_iso(),
                            "details": {
                                "toolName": last_tool,
                                "friendlyName": f"{last_tool} complete",
                                "success": True
                            }
                        }

                        # Check if chat_output agent completed (for flow termination)
                        # This signals that a chat-based flow has produced its final output
                        if last_tool == "ask_chat_output_specialist":
                            logger.info("[OpenAI Agents] Chat output agent completed")
                            yield {
                                "type": "CHAT_OUTPUT_READY",
                                "timestamp": _now_iso(),
                                "details": {
                                    "output_preview": output_preview,
                                    "output_length": len(str(output)),
                                }
                            }

                        # Check if tool output contains FileInfo (file download)
                        # export_to_file and file formatter tools return FileInfo as JSON
                        if output:
                            try:
                                output_data = json.loads(str(output)) if isinstance(output, str) else output
                                # Check for FileInfo signature: must have file_id and download_url
                                if (
                                    isinstance(output_data, dict) and
                                    output_data.get("file_id") and
                                    output_data.get("download_url") and
                                    output_data.get("filename")
                                ):
                                    logger.info(
                                        f"[OpenAI Agents] File output detected: "
                                        f"{output_data.get('filename')} ({output_data.get('format')})"
                                    )
                                    yield {
                                        "type": "FILE_READY",
                                        "timestamp": _now_iso(),
                                        "details": {
                                            "file_id": output_data.get("file_id"),
                                            "filename": output_data.get("filename"),
                                            "format": output_data.get("format"),
                                            "size_bytes": output_data.get("size_bytes"),
                                            "mime_type": output_data.get("mime_type"),
                                            "download_url": output_data.get("download_url"),
                                            "created_at": output_data.get("created_at"),
                                        }
                                    }
                            except (json.JSONDecodeError, TypeError, AttributeError):
                                # Not JSON or not FileInfo - ignore
                                pass

                    elif item_type == "message_output_item":
                        # Final message output - extract text if not already captured
                        try:
                            from agents.items import ItemHelpers
                            message_text = ItemHelpers.text_message_output(item)
                            if message_text and not full_response:
                                full_response = message_text
                        except Exception:
                            pass

                    elif item_type == "handoff_call_item":
                        # Handle handoff to another agent
                        target_agent = getattr(item, "target_agent", None)
                        if target_agent:
                            target_name = getattr(target_agent, "name", "unknown")
                            logger.info(f"[OpenAI Agents] Handoff to: {target_name}")
                            yield {
                                "type": "HANDOFF_START",
                                "data": {
                                    "from_agent": current_agent,
                                    "to_agent": target_name
                                }
                            }

                    elif item_type == "handoff_output_item":
                        # Handoff completed
                        source_agent = getattr(item, "source_agent", None)
                        if source_agent:
                            source_name = getattr(source_agent, "name", "unknown")
                            logger.info(f"[OpenAI Agents] Handoff completed from: {source_name}")

            elif event_type == "agent_updated_stream_event":
                # Handle agent switches during handoffs
                new_agent = getattr(event, "new_agent", None)
                if new_agent:
                    new_agent_name = getattr(new_agent, "name", "unknown")
                    logger.info(f"[OpenAI Agents] Agent switched to: {new_agent_name}")
                    # Emit completion for previous agent
                    yield {
                        "type": "AGENT_COMPLETE",
                        "timestamp": _now_iso(),
                        "details": {
                            "agentRole": current_agent,
                            "agentDisplayName": current_agent
                        }
                    }
                    current_agent = new_agent_name
                    is_generating = False  # Reset for new agent's generation phase
                    if new_agent_name not in agents_used:
                        agents_used.append(new_agent_name)
                    # Audit event: CREW_START for new agent
                    yield {
                        "type": "CREW_START",
                        "timestamp": _now_iso(),
                        "details": {
                            "crewName": new_agent_name,
                            "crewDisplayName": new_agent_name,
                            "agents": [new_agent_name]
                        }
                    }

        # Yield any remaining live events after stream completes
        while live_events_yielded < len(live_events):
            yield live_events[live_events_yielded]
            live_events_yielded += 1

    finally:
        # Clear the live event list reference
        set_live_event_list(None)

    # Get final output if not captured from streaming
    if hasattr(result, "final_output"):
        final_output = result.final_output
        if final_output:
            if hasattr(final_output, "model_dump"):
                structured_result = final_output.model_dump()
            elif isinstance(final_output, dict):
                structured_result = final_output
            if not full_response:
                full_response = str(final_output)

    logger.info(
        f"[OpenAI Agents] Run completed. Response length: {len(full_response)}, "
        f"Tool calls: {tool_calls_count}, Agents used: {agents_used}, trace_id: {trace_id}"
    )

    # Run robust uncited-negative guardrail using actual tool calls (if structured Answer)
    if structured_result is not None:
        try:
            parsed_answer = Answer.model_validate(structured_result)
            guardrail_message = enforce_uncited_negative_guardrail(parsed_answer, tools_called)
            if guardrail_message:
                yield {
                    "type": "RUN_ERROR",
                    "data": {
                        "message": guardrail_message,
                        "error_type": "GuardrailTriggered",
                        "trace_id": trace_id
                    }
                }
                return
        except ValidationError:
            pass

        yield {
            "type": "STRUCTURED_RESULT",
            "data": {
                "result": structured_result,
                "trace_id": trace_id
            }
        }

    # Audit event: SUPERVISOR_COMPLETE
    yield {
        "type": "SUPERVISOR_COMPLETE",
        "timestamp": _now_iso(),
        "details": {
            "message": "Query completed successfully",
            "totalSteps": len(agents_used)
        }
    }

    # Emit completion event with summary for updating the span
    yield {
        "type": "RUN_FINISHED",
        "data": {
            "response": full_response,
            "response_length": len(full_response),
            "tool_calls": tool_calls_count,
            "agents_used": agents_used,
            "trace_id": trace_id
        }
    }


async def run_agent_streamed(
    user_message: str,
    user_id: str,
    session_id: Optional[str] = None,
    document_id: Optional[str] = None,
    document_name: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    active_groups: Optional[List[str]] = None,
    agent: Optional[Agent] = None,
    doc_context: Optional["DocumentContext"] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Run an agent with streaming output.

    This function runs either a provided agent or creates a supervisor agent
    that routes to specialized domain agents (PDF, Disease Ontology, Gene
    Curation, Chemical Ontology). It yields SSE-compatible event dictionaries.

    All agent settings (model, temperature, reasoning) are configured via
    environment variables. See config.py for available settings.

    Langfuse Tracing:
        Uses start_as_current_span() to set the ACTIVE context. The Langfuse
        OpenAI wrapper uses OpenTelemetry context propagation to automatically
        nest all LLM calls under this parent span, creating a proper hierarchy.

    Args:
        user_message: The user's question
        user_id: The user's user ID for tenant isolation
        session_id: Optional chat session UUID for Langfuse trace grouping
        document_id: Optional UUID of the PDF document (enables PDF specialist)
        document_name: Optional name of the document for context
        conversation_history: Optional list of previous messages
        active_groups: Optional list of group IDs (e.g., ["MGI", "FB"]) for injecting
                       group-specific rules into agent prompts
        agent: Optional pre-built agent to use instead of creating a supervisor.
               Use this for flow execution with custom flow supervisors.
               If None, creates the standard supervisor agent.
        doc_context: Optional pre-fetched DocumentContext. If provided, avoids
                     redundant Weaviate queries. Used by flow executor for optimization.

    Yields:
        SSE-compatible event dictionaries with types:
        - RUN_STARTED: Start of agent execution
        - AGENT_UPDATED: Agent handoff occurred
        - TOOL_CALL_START: Tool invocation started
        - TOOL_CALL_END: Tool completed with output preview
        - TEXT_MESSAGE_CONTENT: Text response delta
        - RUN_FINISHED: Agent execution complete
        - ERROR: Error occurred during execution
    """
    doc_info = f"document {document_id[:8]}..." if document_id else "no document"
    logger.info(
        f"[OpenAI Agents] Starting streamed run for {doc_info}, "
        f"query='{user_message[:50]}...'"
    )

    # Clear any leftover data from previous runs
    clear_collected_events()
    clear_pending_configs()  # Clear agent configs from previous requests
    reset_consecutive_call_tracker()  # Reset batching nudge tracker for new query
    clear_prompt_context()  # Clear prompt tracking for new request

    # Use pre-fetched document context if provided, otherwise fetch
    # This optimization avoids redundant Weaviate queries when called from flow executor
    from src.lib.document_context import DocumentContext

    hierarchy = None
    abstract = None
    if doc_context is not None:
        # Use pre-fetched context (optimization path from flow executor)
        hierarchy = doc_context.hierarchy
        abstract = doc_context.abstract
        logger.debug(f"[OpenAI Agents] Using pre-fetched document context: {doc_context.section_count()} sections")
    elif document_id and user_id:
        # Fetch fresh (standard chat path)
        doc_context = DocumentContext.fetch(document_id, user_id, document_name)
        hierarchy = doc_context.hierarchy
        abstract = doc_context.abstract

    # Use provided agent OR create the supervisor agent with all domain specialists
    # All agent settings come from environment variables (see config.py)
    if agent is None:
        agent = create_supervisor_agent(
            document_id=document_id,
            user_id=user_id,
            document_name=document_name,
            hierarchy=hierarchy,
            abstract=abstract,
            active_groups=active_groups,
        )
        agent_name = agent.name
    else:
        # Custom agent provided (e.g., flow supervisor)
        agent_name = getattr(agent, 'name', 'Custom Agent')
        logger.info(f"[OpenAI Agents] Using provided agent: {agent_name}")

    # Commit pending prompts for whichever agent we're using
    # (supervisor runs immediately after creation, unlike specialists which are on-demand)
    commit_pending_prompts(agent_name)

    # Build input with history if provided
    input_items: List[Dict[str, Any]] = []
    if conversation_history:
        for msg in conversation_history:
            input_items.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })
    input_items.append({"role": "user", "content": user_message})

    # Generate a fallback trace ID (used when Langfuse not configured)
    doc_prefix = document_id[:8] if document_id else "nodoc"
    fallback_trace_id = f"chat-{doc_prefix}-{uuid.uuid4().hex[:8]}"

    # Get Langfuse client for tracing
    langfuse = get_langfuse()

    # hierarchy is already fetched above and passed to supervisor agent
    # Log if we'll be adding it to trace metadata
    if hierarchy:
        logger.info(f"[Langfuse] Adding document hierarchy to trace: {len(hierarchy.get('sections', []))} sections")

    if langfuse:
        # Use start_as_current_span() to SET THE ACTIVE CONTEXT
        # All OpenAI calls inside will automatically be nested under this span
        try:
            # Build trace metadata with optional hierarchy
            trace_metadata = {
                "supervisor_agent": agent.name,
                "supervisor_model": agent.model,
                "has_document": document_id is not None,
                "active_groups": active_groups or [],  # Group-specific rules applied to this session
            }
            if hierarchy:
                # Add hierarchy summary to metadata (full structure for trace analysis)
                trace_metadata["document_hierarchy"] = {
                    "top_level_sections": hierarchy.get("top_level_sections", []),
                    "sections": hierarchy.get("sections", []),
                    "section_count": len(hierarchy.get("sections", [])),
                }
            if abstract:
                # Add abstract info to metadata (length only, not full text)
                trace_metadata["document_abstract"] = {
                    "has_abstract": True,
                    "abstract_length": len(abstract),
                }

            # Use start_as_current_span() to set OTEL context - this is CRITICAL
            # for the langfuse.openai wrapper to auto-nest GENERATION observations
            # under our trace. Without this, OpenAI calls create orphaned traces.
            #
            # NOTE: We manually call __enter__() and __exit__() because this is an
            # async generator - we can't use a simple `with` block as the generator
            # can be suspended across yield statements. The context manager sets up
            # the OTEL span context on __enter__ and cleans it up on __exit__.
            # Create a short query preview for trace naming (first 50 chars)
            query_preview = user_message[:50] + "..." if len(user_message) > 50 else user_message
            trace_name = f"chat: {query_preview}"

            logger.info(f"[Langfuse] Creating trace: name='{trace_name}', session_id={session_id}, user_id={user_id}")

            span_context_manager = langfuse.start_as_current_span(
                name="chat-flow",
                input={"query": user_message, "document_id": document_id, "document_name": document_name},
                metadata=trace_metadata
            )
            root_span = span_context_manager.__enter__()

            try:
                # Build trace tags - include group tags for easy filtering
                trace_tags = ["chat", "openai-agents"]
                if active_groups:
                    # Add group:MGI, group:FB, etc. for each active group
                    trace_tags.extend([f"group:{grp}" for grp in active_groups])

                # Update trace-level attributes including the trace NAME
                # This ensures the trace is properly named (not just the span)
                root_span.update_trace(
                    name=trace_name,  # Set trace name explicitly to prevent agent config names from overwriting
                    user_id=user_id,
                    session_id=session_id,  # Group all chats for same chat session together
                    tags=trace_tags,
                )

                # Use Langfuse trace_id for frontend (enables trace extraction)
                trace_id = root_span.trace_id

                # Set trace_id in context for tools (enables closure capture)
                set_current_trace_id(trace_id)

                logger.info(
                    f"[Langfuse] Trace created: trace_id={trace_id}, "
                    f"session_id={session_id}, tags={trace_tags}, "
                    f"document_id={document_id}"
                )

                # Flush queued agent configs to the trace as EVENT observations
                # These were collected during agent creation before trace existed
                config_count = flush_agent_configs(root_span)
                logger.info(f"[Langfuse] Flushed {config_count} agent configs to trace {trace_id[:8]}...")

                # Emit start event AFTER we have the Langfuse trace_id
                yield {
                    "type": "RUN_STARTED",
                    "data": {
                        "agent": agent.name,
                        "model": agent.model,
                        "document_id": document_id,
                        "trace_id": trace_id
                    }
                }
                # Audit event: SUPERVISOR_START
                yield {
                    "type": "SUPERVISOR_START",
                    "timestamp": _now_iso(),
                    "details": {"message": f"Processing query with {agent.name}"}
                }

                try:
                    # Run agent inside the active span context
                    # All OpenAI calls will automatically be children of root_span
                    async for event in _run_agent_with_tracing(
                        agent=agent,
                        input_items=input_items,
                        user_id=user_id,
                        document_id=document_id,
                        document_name=document_name,
                        user_message=user_message,
                        trace_id=trace_id,
                    ):
                        # Capture completion data to update span
                        if event.get("type") == "RUN_FINISHED":
                            data = event.get("data", {})
                            root_span.update(
                                output={
                                    "response_length": data.get("response_length", 0),
                                    "tool_calls": data.get("tool_calls", 0),
                                    "agents_used": data.get("agents_used", []),
                                }
                            )
                            logger.info(
                                f"[Langfuse] Trace completed: trace_id={trace_id}, "
                                f"response_length={data.get('response_length', 0)}, "
                                f"tool_calls={data.get('tool_calls', 0)}, "
                                f"agents_used={data.get('agents_used', [])}"
                            )
                            # Note: Prompt logging moved to finally block for guaranteed execution
                        yield event

                except SpecialistOutputError as e:
                    # Specialist failed to produce structured output after retry
                    # This is a specific error that provides clear context to the user
                    logger.error(
                        f"[OpenAI Agents] Specialist output error: {e}, "
                        f"specialist={e.specialist_name}, output_type={e.output_type_name}, "
                        f"trace_id: {trace_id}",
                        exc_info=True
                    )
                    root_span.update(
                        output={
                            "error": str(e),
                            "error_type": "SpecialistOutputError",
                            "specialist_name": e.specialist_name,
                            "output_type": e.output_type_name,
                        },
                        level="ERROR",
                        status_message=str(e),
                        metadata={"specialist_retry_failed": True}
                    )
                    # Audit event: SPECIALIST_ERROR (more specific than SUPERVISOR_ERROR)
                    yield {
                        "type": "SPECIALIST_ERROR",
                        "timestamp": _now_iso(),
                        "details": {
                            "specialist": e.specialist_name,
                            "output_type": e.output_type_name,
                            "error": str(e),
                            "message": (
                                f"The {e.specialist_name} was unable to produce a response. "
                                f"Please report this failure using the feedback option (⋮ menu on messages) "
                                f"so we can investigate. You can also try rephrasing your question or "
                                f"breaking it into smaller parts."
                            )
                        }
                    }
                    # Note: Prompt logging moved to finally block for guaranteed execution
                    yield {
                        "type": "RUN_ERROR",
                        "data": {
                            "message": (
                                f"The {e.specialist_name} encountered an issue. "
                                f"Please report this using the feedback option (⋮ menu), then try your query again."
                            ),
                            "error_type": "SpecialistOutputError",
                            "trace_id": trace_id
                        }
                    }

                except Exception as e:
                    logger.error(f"[OpenAI Agents] Run error: {e}, trace_id: {trace_id}", exc_info=True)
                    root_span.update(
                        output={"error": str(e), "error_type": type(e).__name__},
                        level="ERROR",
                        status_message=str(e)
                    )
                    # Audit event: SUPERVISOR_ERROR
                    yield {
                        "type": "SUPERVISOR_ERROR",
                        "timestamp": _now_iso(),
                        "details": {
                            "error": str(e),
                            "context": type(e).__name__
                        }
                    }
                    # Note: Prompt logging moved to finally block for guaranteed execution
                    yield {
                        "type": "RUN_ERROR",
                        "data": {
                            "message": str(e),
                            "error_type": type(e).__name__,
                            "trace_id": trace_id
                        }
                    }

            finally:
                # CRITICAL: Log prompts regardless of how the generator exits (success, error, or client disconnect)
                # This ensures audit trail is complete even if client disconnects mid-stream
                _log_used_prompts_to_db(trace_id=trace_id, session_id=session_id, span=root_span)

                # Exit the context manager to properly clean up OTEL context
                # This ensures proper cleanup regardless of how the generator exits
                # We pass (None, None, None) to indicate no exception occurred in the finally
                #
                # NOTE: The __exit__() may fail with "Token was created in a different Context"
                # when the async generator is abandoned (GeneratorExit) because OTEL contextvars
                # track context per-task, and the cleanup runs in a different async context than
                # where __enter__() ran. This is a known limitation with async generators.
                # The trace is still captured correctly - the error is just about cleanup.
                try:
                    span_context_manager.__exit__(None, None, None)
                    logger.info(f"[Langfuse] Span context closed for trace_id={trace_id}")
                except ValueError as e:
                    if "Token was created in a different Context" in str(e):
                        # Expected when generator is abandoned or suspended across async boundaries
                        logger.debug(f"[Langfuse] OTEL context detach skipped (async boundary): {e}")
                    else:
                        # Unexpected ValueError - log but don't crash
                        logger.warning(f"[Langfuse] Unexpected error during span cleanup: {e}")
                flush_langfuse()
                logger.info(f"[Langfuse] Flushed trace data for trace_id={trace_id}")

        except Exception as e:
            logger.error(f"[Langfuse] Failed to create span context: {e}", exc_info=True)
            # Fall back to running without tracing
            # Set fallback trace_id in context for tools
            set_current_trace_id(fallback_trace_id)
            yield {
                "type": "RUN_STARTED",
                "data": {
                    "agent": agent.name,
                    "model": agent.model,
                    "document_id": document_id,
                    "trace_id": fallback_trace_id
                }
            }
            yield {
                "type": "SUPERVISOR_START",
                "timestamp": _now_iso(),
                "details": {"message": f"Processing query with {agent.name}"}
            }
            try:
                async for event in _run_agent_with_tracing(
                    agent=agent,
                    input_items=input_items,
                    user_id=user_id,
                    document_id=document_id,
                    document_name=document_name,
                    user_message=user_message,
                    trace_id=fallback_trace_id,
                ):
                    yield event
            finally:
                # Guarantee prompt logging even on client disconnect
                _log_used_prompts_to_db(trace_id=fallback_trace_id, session_id=session_id)
    else:
        # No Langfuse configured, run without tracing
        logger.info("[Langfuse] Not configured, running without tracing")
        # Set fallback trace_id in context for tools
        set_current_trace_id(fallback_trace_id)
        yield {
            "type": "RUN_STARTED",
            "data": {
                "agent": agent.name,
                "model": agent.model,
                "document_id": document_id,
                "trace_id": fallback_trace_id
            }
        }
        yield {
            "type": "SUPERVISOR_START",
            "timestamp": _now_iso(),
            "details": {"message": f"Processing query with {agent.name}"}
        }
        try:
            async for event in _run_agent_with_tracing(
                agent=agent,
                input_items=input_items,
                user_id=user_id,
                document_id=document_id,
                document_name=document_name,
                user_message=user_message,
                trace_id=fallback_trace_id,
            ):
                yield event
        finally:
            # Guarantee prompt logging even on client disconnect
            _log_used_prompts_to_db(trace_id=fallback_trace_id, session_id=session_id)
