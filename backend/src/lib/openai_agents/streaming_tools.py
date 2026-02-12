"""
Streaming tool wrappers for specialist agents.

This module provides custom tool wrappers that expose internal agent activity.
Unlike `as_tool()` which runs agents as black boxes, these wrappers use
`Runner.run_streamed()` to capture internal tool calls and report them.

REAL-TIME EVENT STREAMING:
Events can be pushed to a live queue for immediate emission to the audit panel,
or collected in a context variable for batch emission after completion.

When a live queue is set via `set_live_event_queue()`, events are pushed
immediately, allowing real-time visibility into specialist agent activity.
"""

import asyncio
import json
import logging
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from agents import Agent, Runner, RunConfig
from agents.models.openai_provider import OpenAIProvider

from .config import get_max_turns

# Prompt context tracking for execution logging
from src.lib.prompts.context import commit_pending_prompts

logger = logging.getLogger(__name__)


# =============================================================================
# EXCEPTION CLASSES
# =============================================================================

class SpecialistOutputError(Exception):
    """
    Raised when a specialist agent fails to produce required structured output after retry.

    This error indicates that the specialist completed its tool calls but did not generate
    the expected Pydantic model output, even after being given a second chance with a
    nudge prompt.
    """
    def __init__(self, specialist_name: str, output_type_name: str, message: str = None):
        self.specialist_name = specialist_name
        self.output_type_name = output_type_name
        super().__init__(message or f"{specialist_name} failed to produce {output_type_name} after retry")


# =============================================================================
# BATCHING NUDGE CONFIGURATION
# =============================================================================
# When the supervisor calls the same specialist multiple times in a row,
# we gently remind it that batching is available. This helps prevent
# inefficient patterns like calling ask_gene_specialist 20 times for
# individual genes instead of once with all genes.

BATCHING_NUDGE_CONFIG = {
    "ask_gene_specialist": {
        "example": 'ask_gene_specialist("Look up these genes: daf-16, lin-3, unc-54, ...")',
        "entity": "genes",
    },
    "ask_allele_specialist": {
        "example": 'ask_allele_specialist("Look up these alleles: e1370, n765, tm1234, ...")',
        "entity": "alleles",
    },
    "ask_disease_specialist": {
        "example": 'ask_disease_specialist("Look up these diseases: Alzheimer disease, diabetes mellitus, ...")',
        "entity": "diseases",
    },
    "ask_chemical_specialist": {
        "example": 'ask_chemical_specialist("Look up these chemicals: glucose, ATP, ethanol, ...")',
        "entity": "chemicals",
    },
    "ask_ontology_mapping_specialist": {
        "example": 'ask_ontology_mapping_specialist("Map these labels: pharynx, L3 larval stage, nucleus, ...")',
        "entity": "terms",
    },
    "ask_gene_ontology_specialist": {
        "example": 'ask_gene_ontology_specialist("Look up these GO terms: apoptotic process, kinase activity, ...")',
        "entity": "GO terms",
    },
    "ask_go_annotations_specialist": {
        "example": 'ask_go_annotations_specialist("Get GO annotations for these genes: WB:WBGene00000912, WB:WBGene00001234, ...")',
        "entity": "genes",
    },
}

# Threshold for triggering the nudge (3 consecutive calls to same specialist)
BATCHING_NUDGE_THRESHOLD = 3


def get_batching_config() -> Dict[str, Any]:
    """
    Generate batching config from AGENT_REGISTRY.

    Returns dict keyed by supervisor tool name (e.g., "ask_gene_specialist")
    with entity and example for batching nudge prompts.

    Falls back to hardcoded BATCHING_NUDGE_CONFIG if registry is not available.
    """
    try:
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
    except ImportError:
        # Fallback to hardcoded if registry not available
        return BATCHING_NUDGE_CONFIG

    config: Dict[str, Any] = {}
    for agent_id, entry in AGENT_REGISTRY.items():
        batching = entry.get("batching")
        if not batching:
            continue

        # Get tool name from supervisor config (single source of truth)
        supervisor = entry.get("supervisor", {})
        tool_name = supervisor.get("tool_name")
        if not tool_name:
            continue

        config[tool_name] = {
            "entity": batching["entity"],
            "example": batching["example"],
        }

    return config


# Track consecutive specialist calls for batching nudge (per-request isolation via ContextVar)
# Format: {"last_tool": "tool_name", "count": N}
# Using ContextVar ensures thread-safety for concurrent requests
_consecutive_call_tracker: ContextVar[Dict[str, Any]] = ContextVar(
    'consecutive_call_tracker',
    default={"last_tool": None, "count": 0}
)


def reset_consecutive_call_tracker():
    """Reset the consecutive call tracker. Call this at the start of a new conversation."""
    _consecutive_call_tracker.set({"last_tool": None, "count": 0})
    logger.debug("Tracker reset for new request")


def _track_specialist_call(tool_name: str) -> int:
    """
    Track a specialist call and return the consecutive count.

    Thread-safe via ContextVar - each request has isolated state.

    Args:
        tool_name: The tool being called (e.g., "ask_gene_specialist")

    Returns:
        The number of consecutive calls to this tool (1 = first call)
    """
    tracker = _consecutive_call_tracker.get()

    if tracker["last_tool"] == tool_name:
        new_count = tracker["count"] + 1
    else:
        new_count = 1

    # Update the tracker with new state
    _consecutive_call_tracker.set({"last_tool": tool_name, "count": new_count})

    logger.debug("%s called, consecutive count: %s", tool_name, new_count)
    return new_count


def _generate_batching_nudge(tool_name: str, consecutive_count: int) -> Optional[str]:
    """
    Generate a batching nudge message if appropriate.

    Only generates a nudge if:
    - The tool supports batching (is in BATCHING_NUDGE_CONFIG)
    - This is exactly the Nth consecutive call (threshold hit)

    Args:
        tool_name: The tool being called
        consecutive_count: How many times in a row this tool has been called

    Returns:
        A nudge message string, or None if no nudge needed
    """
    # Only nudge on exactly the threshold (not every call after)
    if consecutive_count != BATCHING_NUDGE_THRESHOLD:
        return None

    # Check if this tool supports batching (use registry-derived config)
    batching_config = get_batching_config()
    config = batching_config.get(tool_name)
    if not config:
        return None

    entity = config["entity"]
    example = config["example"]

    # Keep the message neutral and helpful
    nudge = f"""

---
Note: You've called this specialist {consecutive_count} times for individual {entity}. If you have more to look up, you can batch them in one call:

{example}

If separate calls are intentional for this task, no problem.
---"""

    logger.info("Generated nudge for %s after %s consecutive calls", tool_name, consecutive_count)
    return nudge


# Context variable to collect specialist internal events (legacy batch mode)
# This allows the supervisor's runner to access events after tool completion
_specialist_events: ContextVar[List[Dict[str, Any]]] = ContextVar(
    'specialist_events', default=[]
)

# ContextVar for live event list (real-time mode) - isolated per async context
# This replaces the previous module-level global that caused race conditions
# when multiple batch jobs ran concurrently (events leaked between batches).
#
# RACE CONDITION FIX (2026-01-23, KANBAN-935):
# The previous global variable allowed Batch A's FILE_READY events to be
# captured by Batch B when they ran concurrently. Using ContextVar ensures
# each batch execution has its own isolated list that cannot be overwritten
# by other concurrent executions.
#
# Note: The previous comment about "ContextVar creates task-local storage that
# doesn't work across SDK contexts" was incorrect - the issue was with the
# global being overwritten by concurrent batches, not ContextVar behavior.
_live_event_list_var: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar(
    'live_event_list', default=None
)


@dataclass
class SpecialistToolCall:
    """Represents an internal tool call made by a specialist."""
    tool_name: str
    tool_args: Optional[Dict[str, Any]] = None
    output_preview: Optional[str] = None
    duration_ms: Optional[int] = None


@dataclass
class SpecialistActivity:
    """Summary of a specialist agent's internal activity."""
    specialist_name: str
    tool_calls: List[SpecialistToolCall] = field(default_factory=list)
    total_duration_ms: Optional[int] = None


def get_collected_events() -> List[Dict[str, Any]]:
    """Get all events collected from specialist runs (batch mode)."""
    return _specialist_events.get()


def clear_collected_events():
    """Clear the collected specialist events."""
    _specialist_events.set([])


def set_live_event_list(event_list: Optional[List[Dict[str, Any]]]):
    """
    Set a live event list for real-time event streaming.

    When set, specialist events are appended immediately to this list
    instead of being collected in the ContextVar batch.

    Uses ContextVar for proper isolation between concurrent batch executions.
    Each batch's list is isolated to its own execution context.

    Args:
        event_list: A list to append events to, or None to disable
    """
    _live_event_list_var.set(event_list)
    logger.debug("Live event list set: %s", event_list is not None)


def get_live_event_list() -> Optional[List[Dict[str, Any]]]:
    """Get the current live event list, if any.

    Returns the list from the current execution context (ContextVar).
    """
    return _live_event_list_var.get()


def add_specialist_event(event: Dict[str, Any]):
    """
    Add an event - either push to live list or collect for batch emission.

    If a live list is set (via ContextVar), the event is appended immediately
    for real-time streaming. Otherwise, it's collected for batch emission
    after the specialist completes.

    Uses ContextVar for proper isolation - each concurrent batch execution
    has its own list that cannot be contaminated by other batches.
    """
    event_list = _live_event_list_var.get()
    if event_list is not None:
        # Real-time mode: append to list immediately
        # Python's list.append() is thread-safe (GIL protected)
        event_list.append(event)
        logger.debug("Appended event to live list: %s, list_len=%s", event.get("type"), len(event_list))
    else:
        # Batch mode: collect for later emission
        events = _specialist_events.get()
        events.append(event)
        _specialist_events.set(events)


def _emit_chunk_provenance_from_output(tool_name: str, output: str):
    """
    Parse PDF tool output and emit CHUNK_PROVENANCE events for PDF highlighting.

    This enables the frontend PDF viewer to highlight relevant sections based
    on what the agent read/searched.

    Args:
        tool_name: Name of the tool (search_document or read_section)
        output: JSON string output from the tool
    """
    try:
        # Parse the tool output JSON
        if isinstance(output, str):
            data = json.loads(output)
        elif hasattr(output, "model_dump"):
            data = output.model_dump()
        else:
            data = output if isinstance(output, dict) else {}

        if tool_name == "search_document":
            # ChunkSearchResult: {"summary": "...", "hits": [...]}
            hits = data.get("hits", [])
            for hit in hits:
                chunk_id = hit.get("chunk_id")
                if not chunk_id:
                    continue

                # Get doc_items with bounding boxes from the chunk (from Docling)
                # These contain page, bbox coordinates for PDF highlighting
                doc_items = hit.get("doc_items") or []

                if not doc_items:
                    # Fallback: create minimal doc_items if none available
                    page_number = hit.get("page_number")
                    if page_number:
                        doc_items = [{"page": page_number}]
                    else:
                        logger.debug("Chunk %s has no doc_items or page_number, skipping", chunk_id)
                        continue

                # Emit CHUNK_PROVENANCE event
                event_payload = {
                    "type": "CHUNK_PROVENANCE",
                    "message_id": str(uuid.uuid4()),
                    "chunk_id": chunk_id,
                    "doc_items": doc_items,
                    "source_tool": tool_name,
                }
                add_specialist_event(event_payload)

        elif tool_name == "read_section":
            # SectionReadResult: {"summary": "...", "section": {...}}
            section = data.get("section")
            if section:
                section_title = section.get("section_title")
                page_numbers = section.get("page_numbers", [])

                # Get doc_items with bounding boxes from all chunks in the section
                doc_items = section.get("doc_items") or []

                if not doc_items:
                    logger.debug("Section '%s' has no doc_items, skipping provenance", section_title)
                    return

                # Emit CHUNK_PROVENANCE event with the section's doc_items
                add_specialist_event({
                    "type": "CHUNK_PROVENANCE",
                    "message_id": str(uuid.uuid4()),
                    "chunk_id": f"section:{section_title}",
                    "doc_items": doc_items,
                    "source_tool": tool_name,
                })
                logger.debug(
                    "Emitted CHUNK_PROVENANCE for section '%s' with %s doc_items",
                    section_title,
                    len(doc_items),
                )

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse %s output for chunk provenance: %s", tool_name, e)
    except Exception as e:
        logger.warning("Error extracting chunk provenance from %s: %s", tool_name, e)


async def run_specialist_with_events(
    agent: Agent,
    input_text: str,
    specialist_name: str,
    run_config: Optional[RunConfig] = None,
    max_turns: Optional[int] = None,
    tool_name: Optional[str] = None,
) -> str:
    """
    Run a specialist agent and collect its internal tool call events.

    This function uses Runner.run_streamed() to capture internal activity
    and stores events that can be emitted by the supervisor's runner.

    Args:
        agent: The specialist agent to run
        input_text: The input/query for the specialist
        specialist_name: Human-readable name for logging
        run_config: Optional run configuration
        max_turns: Maximum turns for the specialist
        tool_name: The tool name (e.g., "ask_gene_specialist") for batching nudge tracking

    Returns:
        The specialist's final output as a string
    """
    start_time = datetime.now(timezone.utc)
    tool_calls: List[SpecialistToolCall] = []
    current_tool_start: Optional[datetime] = None
    current_tool_name: Optional[str] = None

    # Track consecutive calls for batching nudge
    consecutive_count = 0
    if tool_name:
        consecutive_count = _track_specialist_call(tool_name)
    else:
        logger.warning("tool_name is None for %s, skipping consecutive call tracking", specialist_name)

    # Use config default if not specified
    if max_turns is None:
        max_turns = get_max_turns()

    logger.info(
        "Starting specialist=%s (max_turns=%s)",
        specialist_name,
        max_turns,
        extra={"specialist_name": specialist_name, "tool_name": tool_name},
    )

    # Commit pending prompts for this specialist - moves from pending to used
    # This is where the agent ACTUALLY executes, so we log the prompts now
    commit_pending_prompts(agent.name)

    # Create a run config that disables tracing to avoid OpenTelemetry context conflicts
    # The parent supervisor run already has tracing enabled via Langfuse
    effective_config = run_config or RunConfig()
    effective_config = RunConfig(
        model_provider=effective_config.model_provider if hasattr(effective_config, 'model_provider') else None,
        tracing_disabled=True,  # Disable to avoid nested context issues
    )

    # Run with streaming to capture internal events
    result = Runner.run_streamed(
        agent,
        input=input_text,
        max_turns=max_turns,
        run_config=effective_config
    )

    # Event tracking for debugging
    total_event_count = 0
    event_type_counts: dict = {}
    is_generating = False  # Track if we've emitted AGENT_GENERATING

    try:
        async for event in result.stream_events():
            total_event_count += 1
            event_type = getattr(event, "type", None)

            # Count all event types for debugging summary
            event_type_key = event_type or "unknown"
            event_type_counts[event_type_key] = event_type_counts.get(event_type_key, 0) + 1

            # Log ALL events at debug level for comprehensive visibility
            if total_event_count <= 5 or total_event_count % 10 == 0:
                # Log first 5 events and then every 10th to avoid spam
                logger.debug(
                    "%s event #%s: type=%s, event_class=%s",
                    specialist_name,
                    total_event_count,
                    event_type,
                    type(event).__name__,
                )

            # Handle raw_response_event - shows model responses
            if event_type == "raw_response_event":
                data = getattr(event, "data", None)
                if data:
                    # Log response metadata
                    response_type = type(data).__name__

                    # Capture text from ResponseTextDeltaEvent - this shows what the model
                    # is writing when it generates text instead of structured output
                    if response_type == "ResponseTextDeltaEvent":
                        delta_text = getattr(data, "delta", "")
                        if delta_text:
                            # Emit AGENT_GENERATING once when text streaming starts
                            # This provides visual feedback in the audit panel
                            if not is_generating:
                                is_generating = True
                                logger.debug("%s generating response (emitting AGENT_GENERATING)", specialist_name)
                                add_specialist_event({
                                    "type": "AGENT_GENERATING",
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "details": {
                                        "agentRole": specialist_name,
                                        "agentDisplayName": specialist_name,
                                        "message": "Agent reasoning"
                                    }
                                })

                            # Accumulate text for logging (track in a variable)
                            if not hasattr(result, "_accumulated_text"):
                                result._accumulated_text = ""
                            result._accumulated_text += delta_text

                            # Log periodically (every 500 chars) to avoid spam
                            text_len = len(result._accumulated_text)
                            if text_len <= 200 or text_len % 500 < len(delta_text):
                                preview = result._accumulated_text[-200:] if text_len > 200 else result._accumulated_text
                                logger.debug("%s TEXT OUTPUT (%s chars): ...%s", specialist_name, text_len, preview)

                    # Capture reasoning summary delta events (GPT-5 reasoning mode)
                    elif response_type == "ResponseReasoningSummaryPartDoneEvent":
                        # This event contains a part of the reasoning summary
                        part = getattr(data, "part", None)
                        if part:
                            text = getattr(part, "text", None)
                            if text:
                                logger.debug(
                                    "%s REASONING SUMMARY PART: %s",
                                    specialist_name,
                                    text[:300] + ("..." if len(text) > 300 else ""),
                                )

                    elif response_type == "ResponseReasoningSummaryTextDeltaEvent":
                        # This event streams reasoning summary text deltas
                        delta = getattr(data, "delta", "")
                        if delta:
                            # Accumulate reasoning for logging
                            if not hasattr(result, "_accumulated_reasoning"):
                                result._accumulated_reasoning = ""
                            result._accumulated_reasoning += delta

                            # Log periodically
                            reasoning_len = len(result._accumulated_reasoning)
                            if reasoning_len <= 200 or reasoning_len % 500 < len(delta):
                                preview = result._accumulated_reasoning[-200:] if reasoning_len > 200 else result._accumulated_reasoning
                                logger.debug(
                                    "%s REASONING DELTA (%s chars): ...%s",
                                    specialist_name,
                                    reasoning_len,
                                    preview,
                                )

                    elif response_type == "ResponseReasoningSummaryTextDoneEvent":
                        # Final reasoning summary text
                        text = getattr(data, "text", "")
                        if text:
                            logger.debug(
                                "%s REASONING COMPLETE (%s chars): %s",
                                specialist_name,
                                len(text),
                                text[:500] + ("..." if len(text) > 500 else ""),
                            )

                    elif response_type == "ResponseTextDoneEvent":
                        # Log final text when text generation completes
                        full_text = getattr(data, "text", "")
                        if full_text:
                            logger.warning(
                                "%s GENERATED TEXT INSTEAD OF STRUCTURED OUTPUT! Length: %s chars. First 500: %s...",
                                specialist_name,
                                len(full_text),
                                full_text[:500],
                                extra={"specialist_name": specialist_name},
                            )
                    elif response_type not in ("ResponseFunctionCallArgumentsDeltaEvent",):
                        # Log other response types (but not the spammy argument deltas)
                        logger.debug("%s raw_response: type=%s", specialist_name, response_type)

                        # Extra logging for any Reasoning-related events we might have missed
                        if "Reasoning" in response_type:
                            logger.debug("%s REASONING EVENT: %s", specialist_name, response_type)
                            # Try to extract any useful content from the event data
                            for attr in ["delta", "text", "summary", "part", "content"]:
                                if hasattr(data, attr):
                                    value = getattr(data, attr, None)
                                    if value:
                                        logger.debug(
                                            "%s REASONING.%s: %s",
                                            specialist_name,
                                            attr,
                                            str(value)[:300] + ("..." if len(str(value)) > 300 else ""),
                                        )

                    # Check for output content in the response
                    if hasattr(data, "output"):
                        output_items = getattr(data, "output", [])
                        if output_items:
                            logger.debug("%s response has %s output items", specialist_name, len(output_items))
                            for i, item in enumerate(output_items[:3]):  # Log first 3 items
                                item_type = getattr(item, "type", type(item).__name__)
                                logger.debug("%s output[%s]: type=%s", specialist_name, i, item_type)

            if event_type == "run_item_stream_event":
                item = getattr(event, "item", None)
                if item is not None:
                    item_type = getattr(item, "type", None)

                    # Log ALL item types for debugging (not just tool calls)
                    if item_type not in ("tool_call_item", "tool_call_output_item"):
                        # Log non-tool item types at INFO level
                        logger.debug(
                            "%s item: type=%s, item_class=%s",
                            specialist_name,
                            item_type,
                            type(item).__name__,
                        )

                        # Special handling for reasoning_item - log the reasoning content
                        if item_type == "reasoning_item":
                            # Try multiple ways to extract reasoning content
                            reasoning_content = None
                            reasoning_summary = None

                            # Check for summary attribute (per OpenAI docs, this is the key attribute)
                            if hasattr(item, "summary"):
                                reasoning_summary = getattr(item, "summary", None)
                                if reasoning_summary:
                                    # Summary might be a list of text objects
                                    if isinstance(reasoning_summary, list):
                                        texts = []
                                        for s in reasoning_summary:
                                            if hasattr(s, "text"):
                                                texts.append(getattr(s, "text", ""))
                                            else:
                                                texts.append(str(s))
                                        reasoning_content = " ".join(texts)
                                    else:
                                        reasoning_content = str(reasoning_summary)

                            # Check raw_item for nested content
                            if not reasoning_content and hasattr(item, "raw_item"):
                                raw = getattr(item, "raw_item", None)
                                if raw:
                                    # Try to get summary from raw_item
                                    if hasattr(raw, "summary"):
                                        raw_summary = getattr(raw, "summary", None)
                                        if raw_summary:
                                            if isinstance(raw_summary, list):
                                                texts = []
                                                for s in raw_summary:
                                                    if hasattr(s, "text"):
                                                        texts.append(getattr(s, "text", ""))
                                                    else:
                                                        texts.append(str(s))
                                                reasoning_content = " ".join(texts)
                                            else:
                                                reasoning_content = str(raw_summary)
                                    # Try to serialize the raw item to see its structure
                                    if not reasoning_content:
                                        try:
                                            if hasattr(raw, "model_dump"):
                                                raw_dict = raw.model_dump()
                                                reasoning_content = str(raw_dict)
                                            elif hasattr(raw, "__dict__"):
                                                reasoning_content = str(raw.__dict__)
                                        except Exception:
                                            pass

                            if reasoning_content:
                                # Log reasoning content (truncate to 500 chars for readability)
                                content_preview = str(reasoning_content)[:500]
                                logger.debug(
                                    "%s REASONING ITEM (%s chars): %s",
                                    specialist_name,
                                    len(str(reasoning_content)),
                                    content_preview + ("..." if len(str(reasoning_content)) > 500 else ""),
                                )
                            else:
                                # Log all attributes of the item to understand its structure
                                attrs = [a for a in dir(item) if not a.startswith('_')]
                                logger.debug("%s reasoning_item attributes: %s", specialist_name, attrs)
                                # Also dump the item to see what's in it
                                try:
                                    if hasattr(item, "model_dump"):
                                        item_dict = item.model_dump()
                                        logger.debug("%s reasoning_item dump: %s", specialist_name, str(item_dict)[:500])
                                except Exception as e:
                                    logger.debug("Could not dump reasoning_item: %s", e)

                        # Try to extract any content from the item
                        if hasattr(item, "content"):
                            content = getattr(item, "content", None)
                            if content:
                                content_preview = str(content)[:100]
                                logger.debug("%s item content: %s...", specialist_name, content_preview)
                        if hasattr(item, "text"):
                            text = getattr(item, "text", None)
                            if text:
                                text_preview = str(text)[:100]
                                logger.debug("%s item text: %s...", specialist_name, text_preview)
                        if hasattr(item, "raw_item"):
                            raw = getattr(item, "raw_item", None)
                            if raw:
                                logger.debug("%s raw_item type: %s", specialist_name, type(raw).__name__)

                    if item_type == "tool_call_item":
                        # Reset is_generating flag - new tool call means a new generation phase after
                        is_generating = False

                        # Track tool call start
                        current_tool_start = datetime.now(timezone.utc)
                        current_tool_name = (
                            getattr(item, "name", None) or
                            getattr(item, "tool_name", None) or
                            getattr(getattr(item, "raw_item", None), "name", None) or
                            "unknown_tool"
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

                        logger.info(
                            "%s calling: %s",
                            specialist_name,
                            current_tool_name,
                            extra={"specialist_name": specialist_name, "tool_name": current_tool_name},
                        )

                        # Emit event for real-time visibility
                        # Use standard TOOL_START type so frontend can display it
                        add_specialist_event({
                            "type": "TOOL_START",
                            "timestamp": current_tool_start.isoformat(),
                            "details": {
                                "toolName": current_tool_name,
                                "friendlyName": f"{specialist_name}: {current_tool_name}",
                                "agent": specialist_name,
                                "toolArgs": tool_args,
                                "isSpecialistInternal": True  # Mark as internal specialist tool
                            }
                        })

                        # Start building the tool call record
                        tool_calls.append(SpecialistToolCall(
                            tool_name=current_tool_name,
                            tool_args=tool_args
                        ))

                    elif item_type == "tool_call_output_item":
                        # Track tool call completion
                        # Skip if we don't have a current tool (edge case - output without prior call)
                        if current_tool_name is None:
                            logger.debug(
                                "%s received tool output without prior tool call, skipping",
                                specialist_name,
                            )
                            continue

                        output = getattr(item, "output", "")
                        output_preview = str(output)[:200]
                        if len(str(output)) > 200:
                            output_preview += "..."

                        duration_ms = None
                        if current_tool_start:
                            duration = datetime.now(timezone.utc) - current_tool_start
                            duration_ms = int(duration.total_seconds() * 1000)

                        logger.info(
                            "%s %s complete (%sms)",
                            specialist_name,
                            current_tool_name,
                            duration_ms,
                            extra={
                                "specialist_name": specialist_name,
                                "tool_name": current_tool_name,
                                "duration_ms": duration_ms,
                                "operation": "specialist_tool_execution",
                            },
                        )

                        # Update the last tool call with output info
                        if tool_calls:
                            tool_calls[-1].output_preview = output_preview
                            tool_calls[-1].duration_ms = duration_ms

                        # Extract chunk provenance from PDF tool outputs for highlighting
                        if current_tool_name in ("search_document", "read_section"):
                            _emit_chunk_provenance_from_output(current_tool_name, output)

                        # Emit event for real-time visibility
                        # Use standard TOOL_COMPLETE type so frontend can display it
                        add_specialist_event({
                            "type": "TOOL_COMPLETE",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "details": {
                                "toolName": current_tool_name,
                                "friendlyName": f"{specialist_name}: {current_tool_name} complete",
                                "success": True,
                                "durationMs": duration_ms,
                                "isSpecialistInternal": True  # Mark as internal specialist tool
                            }
                        })

                        # Check if tool output contains FileInfo (file download)
                        # File formatter tools (save_csv_file, etc.) return FileInfo as JSON
                        if output:
                            try:
                                output_data = json.loads(str(output)) if isinstance(output, str) else output
                                # Check for FileInfo signature: must have file_id, download_url, filename
                                if (
                                    isinstance(output_data, dict) and
                                    output_data.get("file_id") and
                                    output_data.get("download_url") and
                                    output_data.get("filename")
                                ):
                                    logger.info(
                                        "File output detected from %s: %s (%s)",
                                        specialist_name,
                                        output_data.get("filename"),
                                        output_data.get("format"),
                                    )
                                    # Emit FILE_READY event for frontend to render FileDownloadCard
                                    add_specialist_event({
                                        "type": "FILE_READY",
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                        "details": {
                                            "file_id": output_data.get("file_id"),
                                            "filename": output_data.get("filename"),
                                            "format": output_data.get("format"),
                                            "size_bytes": output_data.get("size_bytes"),
                                            "mime_type": output_data.get("mime_type"),
                                            "download_url": output_data.get("download_url"),
                                            "created_at": output_data.get("created_at"),
                                        }
                                    })
                            except (json.JSONDecodeError, TypeError, AttributeError) as e:
                                # Not JSON or not FileInfo - this is normal for most tools
                                logger.debug("FileInfo detection skipped: %s", type(e).__name__)

                        current_tool_start = None
                        current_tool_name = None

        # Log comprehensive event summary for debugging
        logger.info(
            "%s stream completed normally. Total events: %s, Event types: %s",
            specialist_name,
            total_event_count,
            event_type_counts,
        )

    except Exception as e:
        logger.error(
            "%s stream error: %s: %s. Events before error: %s, Event types: %s",
            specialist_name,
            type(e).__name__,
            e,
            total_event_count,
            event_type_counts,
        )
        # Re-raise to propagate the error
        raise

    # Calculate total duration
    total_duration = datetime.now(timezone.utc) - start_time
    total_duration_ms = int(total_duration.total_seconds() * 1000)

    # Get final output - handle both structured and string outputs
    final_output = ""
    logger.info(
        "%s checking final_output: hasattr=%s, value=%s, type=%s",
        specialist_name,
        hasattr(result, "final_output"),
        getattr(result, "final_output", "N/A"),
        type(getattr(result, "final_output", None)),
    )

    if hasattr(result, "final_output") and result.final_output is not None:
        if hasattr(result.final_output, "model_dump"):
            # Structured output (Pydantic model)
            final_output = json.dumps(result.final_output.model_dump())
            logger.info("%s final_output is Pydantic model: %s...", specialist_name, final_output[:200])
        else:
            # String output
            final_output = str(result.final_output)
            logger.info("%s final_output is string: %s...", specialist_name, final_output[:200])
    else:
        logger.warning("%s has no final_output!", specialist_name)

        # =============================================================================
        # TEXT OUTPUT FALLBACK PARSING
        # =============================================================================
        # GPT-5 models with reasoning enabled may output JSON as plain text instead of
        # using the structured output mechanism. This fallback attempts to parse the
        # text output as JSON and validate it against the output_type schema.
        #
        # This is the PRIMARY extraction path for GPT-5 + reasoning mode.
        #
        # IMPORTANT: We use ONLY result.new_items for text extraction because:
        # 1. new_items contains COMPLETE output items after the stream finishes
        # 2. accumulated_text from streaming deltas is ALWAYS incomplete/truncated
        # 3. The SDK's as_tool() uses new_items for custom_output_extractor

        output_type = getattr(agent, 'output_type', None)

        # Extract complete text from result.new_items (the ONLY reliable source)
        text_from_items = None
        try:
            from agents.items import ItemHelpers
            if hasattr(result, 'new_items') and result.new_items:
                logger.info(
                    "%s Checking new_items for text output (%s items)",
                    specialist_name,
                    len(result.new_items),
                )
                for item in reversed(result.new_items):
                    item_type = getattr(item, 'type', None)
                    logger.debug("%s new_items item: type=%s", specialist_name, item_type)
                    if item_type == 'message_output_item':
                        text_from_items = ItemHelpers.text_message_output(item)
                        if text_from_items:
                            logger.info(
                                "%s Found complete text in new_items (%s chars). First 200: %s...",
                                specialist_name,
                                len(text_from_items),
                                text_from_items[:200],
                            )
                            break
            else:
                logger.warning(
                    "%s new_items is empty or missing! hasattr=%s, value=%s",
                    specialist_name,
                    hasattr(result, "new_items"),
                    getattr(result, "new_items", "N/A"),
                )
        except Exception as e:
            logger.warning(
                "%s Error extracting from new_items: %s: %s",
                specialist_name,
                type(e).__name__,
                e,
            )

        if text_from_items and output_type is not None:
            # Try to extract JSON from the text
            text_stripped = text_from_items.strip()

            logger.info(
                "%s TEXT FALLBACK: Extracting JSON from new_items (%s chars)",
                specialist_name,
                len(text_stripped),
            )

            # Find JSON object boundaries
            json_start = text_stripped.find('{')
            json_end = text_stripped.rfind('}')

            if json_start >= 0 and json_end > json_start:
                json_candidate = text_stripped[json_start:json_end + 1]
                logger.info(
                    "%s TEXT FALLBACK: Found JSON candidate from new_items (%s chars)",
                    specialist_name,
                    len(json_candidate),
                )

                try:
                    # Validate JSON syntax first
                    parsed_json = json.loads(json_candidate)

                    # Validate against Pydantic schema
                    validated_output = output_type.model_validate(parsed_json)

                    # Success! Convert to JSON string for consistency
                    final_output = json.dumps(validated_output.model_dump())

                    logger.info(
                        "%s TEXT FALLBACK SUCCESS! Parsed and validated %s from new_items. JSON length: %s chars",
                        specialist_name,
                        output_type.__name__,
                        len(final_output),
                    )

                    # Emit audit event for visibility
                    add_specialist_event({
                        "type": "SPECIALIST_TEXT_FALLBACK_SUCCESS",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "details": {
                            "specialist": specialist_name,
                            "output_type": output_type.__name__,
                            "json_length": len(final_output),
                            "extraction_method": "text_fallback_new_items",
                            "message": f"{specialist_name} output extracted from new_items (GPT-5 reasoning mode workaround)"
                        }
                    })

                except json.JSONDecodeError as e:
                    logger.warning(
                        "%s TEXT FALLBACK: JSON parsing failed from new_items: %s. JSON candidate length: %s, First 200 chars: %s...",
                        specialist_name,
                        e,
                        len(json_candidate),
                        json_candidate[:200],
                    )
                except Exception as e:
                    logger.warning(
                        "%s TEXT FALLBACK: Pydantic validation failed: %s: %s",
                        specialist_name,
                        type(e).__name__,
                        e,
                    )
            else:
                logger.warning(
                    "%s TEXT FALLBACK: No JSON object found in new_items (%s chars). First 200 chars: %s...",
                    specialist_name,
                    len(text_stripped),
                    text_stripped[:200],
                )
        elif text_from_items:
            # Plain text agent - use text_from_items directly as output
            logger.info(
                "%s: Using text from new_items as plain text output (%s chars)",
                specialist_name,
                len(text_from_items),
            )
            final_output = text_from_items
        elif output_type is not None:
            logger.warning(
                "%s: No text found in new_items, cannot extract %s",
                specialist_name,
                output_type.__name__,
            )

        # =============================================================================
        # STREAMING TEXT FALLBACK
        # =============================================================================
        # GPT-5 + reasoning mode may not include a message_output_item in new_items,
        # but the text IS streamed via ResponseTextDeltaEvent and accumulated in
        # result._accumulated_text. Use this as a last-resort fallback for plain text agents.
        if not final_output and hasattr(result, "_accumulated_text") and result._accumulated_text:
            accumulated_text = result._accumulated_text.strip()
            if accumulated_text:
                logger.info(
                    "%s STREAMING TEXT FALLBACK: Using accumulated text from stream (%s chars) since new_items had no message_output_item",
                    specialist_name,
                    len(accumulated_text),
                )
                final_output = accumulated_text

                # Emit audit event for visibility
                add_specialist_event({
                    "type": "SPECIALIST_TEXT_FALLBACK_SUCCESS",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "details": {
                        "specialist": specialist_name,
                        "text_length": len(final_output),
                        "extraction_method": "streaming_text_fallback",
                        "message": f"{specialist_name} output extracted from streaming deltas (GPT-5 reasoning mode workaround)"
                    }
                })

        # If text fallback succeeded, skip the retry mechanism
        if final_output:
            logger.info(
                "%s TEXT FALLBACK: Skipping retry mechanism - output successfully extracted from text",
                specialist_name,
            )
        else:
            # =============================================================================
            # RETRY MECHANISM FOR EMPTY OUTPUT
            # =============================================================================
            # When a specialist completes tool calls but fails to produce structured output,
            # attempt one retry with a nudge prompt asking the model to synthesize its findings.

            # output_type already fetched above for text fallback
            if output_type is not None:
                output_type_name = output_type.__name__

                logger.warning(
                    "%s produced no output but expects %s. Attempting retry with nudge prompt...",
                    specialist_name,
                    output_type_name,
                )

                # Emit retry audit event
                add_specialist_event({
                    "type": "SPECIALIST_RETRY",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "details": {
                        "specialist": specialist_name,
                        "reason": "empty_output",
                        "output_type": output_type_name,
                        "message": f"{specialist_name} completed tool calls but did not produce output. Retrying..."
                    }
                })

                # Nudge prompt - ask model to produce the required structured output
                nudge_prompt = (
                    f"You completed your tool calls but did not produce the required {output_type_name} structured output. "
                    f"Please synthesize your findings from the previous tool calls into the structured output now. "
                    f"You MUST produce the {output_type_name} before finishing."
                )

                try:
                    # Get conversation history from the failed run so the model knows what was searched
                    # This is CRITICAL - without history, the model has no context to synthesize
                    previous_items = result.to_input_list()

                    # Append nudge prompt to the conversation history
                    retry_input = previous_items + [{"role": "user", "content": nudge_prompt}]

                    logger.info(
                        "%s retry: including %s previous items plus nudge prompt",
                        specialist_name,
                        len(previous_items),
                    )

                    # Create a simplified "retry agent" WITHOUT output_guardrails
                    # The original agent's output_guardrail checks for tool calls, but during retry
                    # we're just asking for output synthesis (no new tool calls). This would cause
                    # the guardrail to trip and return final_output=None immediately.
                    # Solution: Create a minimal agent that only focuses on structured output generation.

                    # Get model from original agent, or fall back to default specialist model from config
                    from .config import DEFAULT_SPECIALIST_MODEL
                    retry_model = getattr(agent, 'model', DEFAULT_SPECIALIST_MODEL)

                    retry_agent = Agent(
                        name=f"{specialist_name} (Retry)",
                        instructions=(
                            f"You are completing the work of the {specialist_name}. "
                            f"You have already gathered information through tool calls (shown in the conversation history). "
                            f"Your ONLY task now is to synthesize this information into the required {output_type_name} structured output. "
                            f"Do NOT attempt to call any tools. Just analyze the previous tool results and produce the output."
                        ),
                        model=retry_model,
                        output_type=output_type,
                        # NO tools - we don't want new searches, just synthesis
                        tools=[],
                        # NO output_guardrails - the original guardrail would trip with 0 tool calls
                        output_guardrails=[],
                    )

                    logger.info(
                        "%s retry: created simplified retry agent without tools or guardrails for output synthesis",
                        specialist_name,
                    )

                    # Re-run with nudge (reduced max_turns since we just need output synthesis)
                    logger.info(
                        "%s retry: starting Runner.run_streamed with model=%s",
                        specialist_name,
                        retry_model,
                    )

                    retry_start_time = datetime.now(timezone.utc)
                    retry_result = Runner.run_streamed(
                        retry_agent,  # Use simplified retry agent, NOT original agent
                        input=retry_input,  # Include full conversation history
                        max_turns=5,  # Reduced turns - just need output synthesis
                        run_config=effective_config
                    )

                    # Consume the retry stream with debug logging
                    retry_event_count = 0
                    async for retry_event in retry_result.stream_events():
                        retry_event_count += 1
                        # Log every event type for debugging
                        event_type = getattr(retry_event, 'type', str(type(retry_event).__name__))
                        logger.debug("%s retry event %s: %s", specialist_name, retry_event_count, event_type)

                    retry_duration_ms = (datetime.now(timezone.utc) - retry_start_time).total_seconds() * 1000
                    logger.info(
                        "%s retry stream consumed: %s events in %.0fms",
                        specialist_name,
                        retry_event_count,
                        retry_duration_ms,
                    )

                    # Debug: Log retry_result attributes
                    logger.info(
                        "%s retry result inspection: has final_output attr=%s, final_output value=%s, type=%s",
                        specialist_name,
                        hasattr(retry_result, "final_output"),
                        getattr(retry_result, "final_output", "N/A"),
                        type(getattr(retry_result, "final_output", None)),
                    )

                    # Check retry result
                    if hasattr(retry_result, "final_output") and retry_result.final_output is not None:
                        # Retry succeeded!
                        if hasattr(retry_result.final_output, "model_dump"):
                            final_output = json.dumps(retry_result.final_output.model_dump())
                        else:
                            final_output = str(retry_result.final_output)

                        logger.info(
                            "%s retry SUCCEEDED! Output length: %s",
                            specialist_name,
                            len(final_output),
                        )

                        # Emit success event (warning severity - something unusual happened)
                        add_specialist_event({
                            "type": "SPECIALIST_RETRY_SUCCESS",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "details": {
                                "specialist": specialist_name,
                                "output_type": output_type_name,
                                "output_length": len(final_output),
                                "message": f"{specialist_name} successfully produced output on retry"
                            }
                        })
                    else:
                        # Retry also failed - emit ERROR audit event and raise exception
                        error_message = (
                            f"{specialist_name} failed to produce {output_type_name} output "
                            f"after retry. The specialist completed tool calls but could not "
                            f"synthesize the results into the required format."
                        )

                        logger.error(
                            "%s retry FAILED! Still no output after nudge prompt. Events consumed: %s, Duration: %.0fms",
                            specialist_name,
                            retry_event_count,
                            retry_duration_ms,
                        )

                        # Emit ERROR audit event so it shows in the audit panel
                        add_specialist_event({
                            "type": "SPECIALIST_ERROR",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "details": {
                                "specialist": specialist_name,
                                "output_type": output_type_name,
                                "error": error_message,
                                "retry_events": retry_event_count,
                                "retry_duration_ms": retry_duration_ms,
                                "severity": "error"
                            }
                        })

                        raise SpecialistOutputError(
                            specialist_name=specialist_name,
                            output_type_name=output_type_name,
                            message=error_message
                        )

                except SpecialistOutputError:
                    # Re-raise our custom error
                    raise
                except Exception as e:
                    # Retry mechanism itself failed
                    logger.error("%s retry mechanism error: %s", specialist_name, e)
                    raise SpecialistOutputError(
                        specialist_name=specialist_name,
                        output_type_name=output_type_name,
                        message=f"{specialist_name} retry failed with error: {str(e)}"
                    )

    # Emit summary event
    add_specialist_event({
        "type": "SPECIALIST_SUMMARY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": {
            "specialist": specialist_name,
            "toolCallCount": len(tool_calls),
            "totalDurationMs": total_duration_ms,
            "toolCalls": [
                {
                    "name": tc.tool_name,
                    "args": tc.tool_args,
                    "durationMs": tc.duration_ms
                }
                for tc in tool_calls
            ]
        }
    })

    logger.info(
        "%s complete: %s tool calls, %sms total, output_length=%s",
        specialist_name,
        len(tool_calls),
        total_duration_ms,
        len(final_output),
    )

    # Inject batching nudge if threshold was hit (exactly at threshold, not after)
    if tool_name:
        nudge = _generate_batching_nudge(tool_name, consecutive_count)
        if nudge:
            logger.info(
                "TRIGGERED for %s after %s consecutive calls. Injecting reminder to supervisor about batching %s.",
                tool_name,
                consecutive_count,
                get_batching_config().get(tool_name, {}).get("entity", "items"),
            )
            final_output += nudge

    return final_output
