"""Flow execution engine for curation flows.

Provides functions to execute user-defined agent workflows with
streaming tool wrappers for full audit visibility.

Key concepts:
- Streaming tools: Uses _create_streaming_tool() to capture internal agent tool calls
- Flow supervisor: A custom supervisor configured for the specific flow
- Streaming execution: Delegates to run_agent_streamed() for rich audit events

Architecture:
    execute_flow() creates a flow supervisor with streaming-wrapped tools, then
    delegates to run_agent_streamed() to get the same rich audit events as
    regular chat (SUPERVISOR_START, AGENT_GENERATING, CREW_START, etc.)
    plus Langfuse tracing, prompt logging, and document metadata.

    Unlike the old as_tool() approach, streaming tools use run_specialist_with_events()
    to capture internal tool calls (read_section, search_document, etc.) and emit
    events for the audit panel and PDF highlighting.
"""
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator, List, Optional, Set

from agents import Agent

from src.models.sql.curation_flow import CurationFlow
from src.lib.agent_studio.catalog_service import get_agent_by_id, AGENT_REGISTRY
from src.lib.openai_agents.config import (
    get_agent_config,
    get_model_for_agent,
    build_model_settings,
)
from src.lib.openai_agents.agents.supervisor_agent import _create_streaming_tool
from src.lib.document_context import DocumentContext

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time in ISO format for audit events."""
    return datetime.now(timezone.utc).isoformat()


def is_agent_in_flow(flow: CurationFlow, agent_id: str) -> bool:
    """Check if an agent is part of a flow's step sequence.

    Used to restrict which tools are enabled during flow execution.
    Only agents explicitly in the flow can have their tools called.

    Args:
        flow: The CurationFlow object containing flow_definition
        agent_id: The agent ID to check (e.g., "gene", "disease")

    Returns:
        True if the agent is in the flow, False otherwise
    """
    flow_def = flow.flow_definition
    nodes = flow_def.get("nodes", [])
    for node in nodes:
        node_data = node.get("data", {})
        if node_data.get("agent_id") == agent_id:
            return True
    return False


def get_flow_agent_ids(flow: CurationFlow) -> Set[str]:
    """Get the set of agent IDs used in a flow.

    Excludes task_input nodes since they are not executable agents.

    Args:
        flow: The CurationFlow object containing flow_definition

    Returns:
        Set of agent IDs (e.g., {"gene", "disease", "allele"})
    """
    agent_ids = set()
    for node in flow.flow_definition.get("nodes", []):
        agent_id = node.get("data", {}).get("agent_id")
        node_type = node.get("type", "agent")
        # Skip task_input nodes - they're not agents
        if agent_id and node_type != "task_input" and agent_id != "task_input":
            agent_ids.add(agent_id)
    return agent_ids


def get_task_instructions(flow: CurationFlow) -> Optional[str]:
    """Extract task_instructions from the task_input node in a flow.

    The task_input node contains the curator's initial task/request that
    provides context for the entire flow.

    Args:
        flow: The CurationFlow object containing flow_definition

    Returns:
        task_instructions string if found, None otherwise
    """
    for node in flow.flow_definition.get("nodes", []):
        node_type = node.get("type", "agent")
        agent_id = node.get("data", {}).get("agent_id")
        if node_type == "task_input" or agent_id == "task_input":
            return node.get("data", {}).get("task_instructions")
    return None


def flow_requires_document(flow: CurationFlow) -> bool:
    """Check if any agent in the flow requires a document.

    Used to determine whether to include document guidance in supervisor
    instructions. Only adds document awareness when the flow actually has
    document-requiring agents (like PDF Specialist).

    Args:
        flow: The CurationFlow object containing flow_definition

    Returns:
        True if any agent in the flow requires a document, False otherwise
    """
    for agent_id in get_flow_agent_ids(flow):
        entry = AGENT_REGISTRY.get(agent_id)
        if entry and entry.get("requires_document", False):
            return True
    return False


def get_all_agent_tools(
    flow: CurationFlow,
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    document_name: Optional[str] = None,
    active_groups: Optional[List[str]] = None,
    doc_context: Optional[DocumentContext] = None,
) -> List:
    """Get streaming-wrapped tools for agents in the flow.

    Uses _create_streaming_tool() to wrap each agent, which captures internal
    tool calls via run_specialist_with_events() and emits events for the audit
    panel and PDF highlighting. This is the same pattern used by normal chat.

    Only agents that are explicitly in the flow get their tools created.
    This gives the supervisor access only to the intended agents.

    Document context (hierarchy, abstract, sections) can be passed in to avoid
    redundant fetches, or will be fetched automatically using DocumentContext
    which leverages the same cache as normal chat.

    Args:
        flow: The curation flow defining which agents are active
        document_id: For document-aware agents
        user_id: For tenant isolation (Cognito subject ID)
        document_name: Optional filename for prompt context
        active_groups: Active group IDs for database agents
        doc_context: Pre-fetched DocumentContext (optimization to avoid re-fetch)

    Returns:
        List of streaming-wrapped tool functions for agents in the flow
    """
    flow_agent_ids = get_flow_agent_ids(flow)
    all_tools = []

    # Use pre-fetched document context if provided, otherwise fetch
    # This optimization matches how chat pre-fetches and passes through
    if doc_context is None and document_id and user_id:
        doc_context = DocumentContext.fetch(document_id, user_id, document_name)
        logger.info(
            f"[Flow Executor] Fetched document context: {doc_context.section_count()} sections, "
            f"abstract={'yes' if doc_context.abstract else 'no'}"
        )
    elif doc_context:
        logger.debug(
            f"[Flow Executor] Using pre-fetched document context: {doc_context.section_count()} sections"
        )

    # Build context for agent creation
    # Start with document context if available, then add flow-specific params
    context = {}
    if doc_context:
        context.update(doc_context.to_agent_kwargs())
    else:
        # Fallback for non-document flows
        context["document_id"] = document_id
        context["user_id"] = user_id
    context["active_groups"] = active_groups or []

    # Only create tools for agents IN the flow
    for agent_id in flow_agent_ids:
        # Skip if not in registry
        if agent_id not in AGENT_REGISTRY:
            logger.warning(f"[Flow Executor] Agent '{agent_id}' in flow but not in registry, skipping")
            continue

        entry = AGENT_REGISTRY[agent_id]

        # Check if this agent requires document and we don't have one
        if entry.get("requires_document", False) and not document_id:
            logger.warning(
                f"[Flow Executor] Agent '{agent_id}' requires document but none provided, skipping"
            )
            continue

        try:
            agent = get_agent_by_id(agent_id, **context)
        except Exception as e:
            logger.warning(f"[Flow Executor] Failed to create agent '{agent_id}': {e}")
            continue

        # Use streaming tool wrapper (same pattern as normal chat)
        # This captures internal tool calls and emits events for audit visibility
        tool_name = f"ask_{agent_id}_specialist"
        tool_description = entry.get("description", f"Ask the {entry['name']}")
        specialist_name = entry.get("name", agent_id)

        streaming_tool = _create_streaming_tool(
            agent=agent,
            tool_name=tool_name,
            tool_description=tool_description,
            specialist_name=specialist_name,
        )

        logger.info(f"[Flow Executor] Created streaming tool: {tool_name} ({specialist_name})")
        all_tools.append(streaming_tool)

    logger.info(f"[Flow Executor] Created {len(all_tools)} streaming tools for flow")
    return all_tools


def build_supervisor_instructions(
    flow: CurationFlow,
    has_document: bool = False,
    document_name: Optional[str] = None,
) -> str:
    """Build supervisor system instructions that list all flow steps.

    The supervisor sees all steps upfront so it knows the intended sequence.
    Skips task_input nodes since they provide context, not execution steps.

    When a document is loaded for the flow, includes guidance so the supervisor
    knows to use PDF tools without asking the user for a document. This fixes
    flows that lack task_input nodes (where the prompt doesn't mention documents).

    Args:
        flow: The CurationFlow containing the flow definition
        has_document: Whether a document is loaded for this flow execution
        document_name: Optional filename for context in the guidance

    Returns:
        System instructions string for the flow supervisor
    """
    nodes = flow.flow_definition.get("nodes", [])
    # entry_node_id = flow.flow_definition.get("entry_node_id")  # Reserved for future edge traversal

    # Build ordered step list (for V1, just use node order)
    # Skip task_input nodes - they're context, not steps
    step_descriptions = []
    step_num = 0
    for node in nodes:
        node_type = node.get("type", "agent")
        data = node.get("data", {})
        agent_id = data.get("agent_id")

        # Skip task_input nodes
        if node_type == "task_input" or agent_id == "task_input":
            continue

        step_num += 1
        agent_name = data.get("agent_display_name", agent_id or "Unknown")
        step_goal = data.get("step_goal", "")

        step_desc = f"Step {step_num}: {agent_name}"
        if step_goal:
            step_desc += f" - {step_goal}"
        step_descriptions.append(step_desc)

    # Build document guidance if a document is loaded
    # This ensures the supervisor knows a document is available even if the
    # flow lacks a task_input node that mentions the document
    doc_guidance = ""
    if has_document:
        name_hint = f" ('{document_name}')" if document_name else ""
        doc_guidance = f"""
Document Available{name_hint}: A document is loaded for this flow execution.
Use the PDF Specialist tools to read and search the document's content.
Do NOT ask the user to provide a document - one is already available.
"""

    instructions = f"""You are executing the "{flow.name}" curation flow.
{doc_guidance}
Execute these steps in order:
{chr(10).join(step_descriptions)}

Guidelines:
- You MAY call agents multiple times if you need more data before moving to the next step
- Pass relevant context from previous steps to subsequent steps
- The final step typically produces output (file or response)

COMPLETION: Once the final step produces output (e.g., CSV file saved, response generated),
your task is COMPLETE. Respond with a brief summary of what was produced and stop.
Do NOT start a new cycle through the steps after output is produced.
"""
    return instructions


def build_flow_prompt(
    flow: CurationFlow,
    document_id: Optional[str] = None,
    user_query: Optional[str] = None,
) -> str:
    """Build the initial prompt for flow execution.

    Combines flow context with user query information.
    Includes task_instructions from task_input node if present.

    NOTE: We don't include document_id in the prompt because the PDF agent's
    tools are already created with the document context. Adding it here would
    be redundant and could confuse the agent. This matches how normal chat works.

    Args:
        flow: The CurationFlow to execute
        document_id: Optional document ID (not used in prompt - tools already have it)
        user_query: Optional user-provided context or query

    Returns:
        Initial prompt string for the flow supervisor
    """
    prompt_parts = []

    # Extract task_instructions from task_input node (if present)
    task_instructions = get_task_instructions(flow)
    if task_instructions:
        prompt_parts.append(f"Task Instructions:\n{task_instructions}")

    # NOTE: Don't add document_id to prompt - PDF agent's tools already have document context
    # Adding it here would be redundant and differs from normal chat behavior

    # Add user query if provided (this may override or complement task_instructions)
    if user_query:
        prompt_parts.append(f"User Query: {user_query}")
    elif not task_instructions:
        # Only add default if no task_instructions AND no user_query
        prompt_parts.append(f"Execute the '{flow.name}' curation workflow.")

    # Add step-specific goals as context (skip task_input nodes)
    nodes = flow.flow_definition.get("nodes", [])
    step_goals = []
    step_num = 0
    for node in nodes:
        node_type = node.get("type", "agent")
        data = node.get("data", {})
        agent_id = data.get("agent_id")

        # Skip task_input nodes
        if node_type == "task_input" or agent_id == "task_input":
            continue

        step_num += 1
        goal = data.get("step_goal")
        if goal:
            step_goals.append(f"- Step {step_num}: {goal}")

    if step_goals:
        prompt_parts.append("\nStep Goals:")
        prompt_parts.extend(step_goals)

    return "\n".join(prompt_parts)


def create_flow_supervisor(
    flow: CurationFlow,
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    document_name: Optional[str] = None,
    active_groups: Optional[List[str]] = None,
    doc_context: Optional[DocumentContext] = None,
) -> Agent:
    """Create a supervisor agent configured for flow execution.

    The supervisor has access to all agent tools, but only those
    in the flow have is_enabled=True.

    Args:
        flow: The CurationFlow defining the workflow
        document_id: Optional document for PDF-aware agents
        user_id: Cognito subject ID for Weaviate tenant isolation
        document_name: Optional filename for prompt context
        active_groups: Active group IDs for database queries
        doc_context: Pre-fetched DocumentContext (optimization to avoid re-fetch)

    Returns:
        Configured Agent instance for flow supervision
    """
    # Get supervisor config (model, temperature, reasoning)
    config = get_agent_config("supervisor")

    # Build model configuration
    model = get_model_for_agent(config.model)
    model_settings = build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=config.reasoning,
    )

    # Get all tools with flow-based is_enabled
    # Pass through pre-fetched doc_context to avoid redundant Weaviate queries
    tools = get_all_agent_tools(
        flow=flow,
        document_id=document_id,
        user_id=user_id,
        document_name=document_name,
        active_groups=active_groups,
        doc_context=doc_context,
    )

    # Determine if document guidance should be included in system instructions
    # Only include when: 1) a document is provided AND 2) the flow has document-requiring agents
    # This prevents confusing the supervisor by mentioning documents when no PDF tools exist
    has_document = bool(document_id) and flow_requires_document(flow)

    # Build supervisor instructions with document awareness if applicable
    instructions = build_supervisor_instructions(
        flow,
        has_document=has_document,
        document_name=document_name,
    )

    # Create flow supervisor agent
    supervisor = Agent(
        name=f"Flow Supervisor: {flow.name}",
        instructions=instructions,
        tools=tools,
        model=model,
        model_settings=model_settings,
    )

    logger.info(
        f"[Flow Executor] Created flow supervisor for '{flow.name}': "
        f"model={config.model}, streaming_tools={len(tools)}"
    )

    return supervisor


async def execute_flow(
    flow: CurationFlow,
    user_id: str,
    session_id: str,
    document_id: Optional[str] = None,
    document_name: Optional[str] = None,
    user_query: Optional[str] = None,
    active_groups: Optional[List[str]] = None,
) -> AsyncGenerator[dict, None]:
    """Execute a curation flow using the shared streaming infrastructure.

    Delegates to run_agent_streamed() with a custom flow supervisor to get
    the same rich audit events as regular chat (SUPERVISOR_START, AGENT_GENERATING,
    CREW_START, SUPERVISOR_COMPLETE, etc.) plus Langfuse tracing, prompt logging,
    and document metadata caching.

    Args:
        flow: The CurationFlow to execute
        user_id: Cognito subject ID for Weaviate tenant isolation
        session_id: Session ID for tracing (Langfuse)
        document_id: Optional document for PDF-aware agents
        document_name: Optional name of the document for Langfuse metadata
        user_query: Optional user-provided query/context
        active_groups: Active group IDs for database queries

    Yields:
        dict: Streaming events - FLOW_STARTED, then all regular chat events
              (RUN_STARTED, SUPERVISOR_START, TOOL_START, etc.), then FLOW_FINISHED
    """
    logger.info(
        f"[Flow Executor] Starting flow: '{flow.name}', "
        f"user_id={user_id}, session_id={session_id}"
    )

    # Pre-fetch document context BEFORE creating supervisor (optimization)
    # This matches how chat pre-fetches and passes through to avoid redundant Weaviate queries
    # The DocumentContext cache ensures we only hit Weaviate once even if called multiple times
    doc_context = None
    if document_id and user_id:
        doc_context = DocumentContext.fetch(document_id, user_id, document_name)
        logger.info(
            f"[Flow Executor] Pre-fetched document context: {doc_context.section_count()} sections, "
            f"abstract={'yes' if doc_context.abstract else 'no'}"
        )

    # Create flow supervisor with restricted tools
    # Pass pre-fetched doc_context to avoid redundant fetches in get_all_agent_tools
    supervisor = create_flow_supervisor(
        flow=flow,
        document_id=document_id,
        user_id=user_id,
        document_name=document_name,
        active_groups=active_groups,
        doc_context=doc_context,
    )

    # Build flow prompt
    prompt = build_flow_prompt(flow, document_id, user_query)

    # Calculate step count for metadata (exclude task_input nodes)
    all_nodes = flow.flow_definition.get("nodes", [])
    total_steps = sum(
        1 for n in all_nodes
        if n.get("type") != "task_input" and n.get("data", {}).get("agent_id") != "task_input"
    )

    # Emit flow-specific FLOW_STARTED (before delegating)
    # This adds flow metadata that run_agent_streamed doesn't know about
    yield {
        "type": "FLOW_STARTED",
        "timestamp": _now_iso(),
        "data": {
            "execution_mode": "flow",
            "flow_id": str(flow.id),
            "flow_name": flow.name,
            "total_steps": total_steps,
        }
    }

    # Delegate to run_agent_streamed with flow supervisor
    # This gives us: Langfuse tracing, prompt logging, document metadata,
    # rich events (SUPERVISOR_START, AGENT_GENERATING, CREW_START, etc.)
    # Pass pre-fetched doc_context to avoid redundant Weaviate queries
    from src.lib.openai_agents.runner import run_agent_streamed

    async for event in run_agent_streamed(
        user_message=prompt,
        user_id=str(user_id),
        session_id=session_id,
        document_id=document_id,
        document_name=document_name,
        conversation_history=None,  # Flows don't use conversation history
        active_groups=active_groups,
        agent=supervisor,  # Pass the flow supervisor
        doc_context=doc_context,  # Pass pre-fetched context (optimization)
    ):
        yield event

        # Terminate flow after output is produced
        # FILE_READY indicates a file output agent (CSV, TSV, JSON) completed
        # CHAT_OUTPUT_READY indicates chat output agent completed
        # This prevents the supervisor from looping back to call agents again
        event_type = event.get("type")
        if event_type == "FILE_READY":
            logger.info(
                f"[Flow Executor] Output file produced - terminating flow '{flow.name}'"
            )
            break
        elif event_type == "CHAT_OUTPUT_READY":
            logger.info(
                f"[Flow Executor] Chat output produced - terminating flow '{flow.name}'"
            )
            break

    # Emit flow-specific completion event
    yield {
        "type": "FLOW_FINISHED",
        "timestamp": _now_iso(),
        "data": {
            "flow_id": str(flow.id),
            "flow_name": flow.name,
        }
    }

    logger.info(f"[Flow Executor] Flow completed: '{flow.name}'")
