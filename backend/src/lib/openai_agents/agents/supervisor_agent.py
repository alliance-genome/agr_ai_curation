"""
Supervisor Agent using OpenAI Agents SDK.

This agent coordinates routing to specialized domain agents based on
query intent, using streaming tool wrappers for visibility.

Each specialist agent runs in isolation with its own context window.
Only the specialist's final output returns to the supervisor, preventing
context window explosion from accumulated tool outputs.

STREAMING VISIBILITY:
Unlike as_tool(), our custom streaming wrappers use Runner.run_streamed()
to capture internal tool calls and emit events to the audit panel.

Advanced features used:
- ModelSettings: Per-agent temperature and reasoning configuration
- Reasoning: Extended thinking time for complex routing decisions (GPT-5 models)
- Guardrails: Optional input validation for safety (PII detection, topic relevance)
- Streaming tool wrappers: Specialists run with event capture for audit visibility

DYNAMIC AGENT DISCOVERY:
Specialist agents are discovered from unified `agents` table records where
`visibility='system'` and `supervisor_enabled=true`.
"""

import asyncio
import logging
import time
from typing import Optional, List, Literal, Dict, Any, Callable

from agents import Agent, ModelSettings, Runner, RunConfig, function_tool
from agents.models.openai_provider import OpenAIProvider
from openai.types.shared import Reasoning

from ..streaming_tools import run_specialist_with_events

# Prompt cache and context tracking imports
from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

# Note: Answer model not used here - supervisor streams plain text for better UX

logger = logging.getLogger(__name__)

# Type alias for reasoning effort levels
ReasoningEffort = Literal["minimal", "low", "medium", "high"]


def _fetch_document_sections_sync(document_id: str, user_id: str) -> List[Dict[str, Any]]:
    """
    Synchronously fetch document sections for injection into the PDF agent prompt.

    This wrapper handles the async get_document_sections function in a sync context.
    """
    from src.lib.weaviate_client.chunks import get_document_sections

    try:
        # Try to get the running loop
        try:
            asyncio.get_running_loop()
            # If there's a running loop, we can't use asyncio.run()
            # Create a new event loop in a thread or use run_coroutine_threadsafe
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, get_document_sections(document_id, user_id))
                return future.result(timeout=10)
        except RuntimeError:
            # No running loop, safe to use asyncio.run()
            return asyncio.run(get_document_sections(document_id, user_id))
    except Exception as e:
        logger.warning("Failed to fetch document sections: %s", e)
        return []


def fetch_document_hierarchy_sync(document_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Synchronously fetch hierarchical document structure for injection into PDF agent prompt.

    Returns the LLM-resolved hierarchy with top-level sections and subsections.
    This wrapper handles the async get_document_sections_hierarchical in a sync context.

    This is a public function, exported for use by runner.py.
    """
    from src.lib.weaviate_client.chunks import get_document_sections_hierarchical

    try:
        # Try to get the running loop
        try:
            asyncio.get_running_loop()
            # If there's a running loop, we can't use asyncio.run()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, get_document_sections_hierarchical(document_id, user_id))
                return future.result(timeout=10)
        except RuntimeError:
            # No running loop, safe to use asyncio.run()
            return asyncio.run(get_document_sections_hierarchical(document_id, user_id))
    except Exception as e:
        logger.warning("Failed to fetch document hierarchy: %s", e)
        return None


# Import guardrails (optional - won't break if module has issues)
try:
    from ..guardrails import safety_guardrail, biology_topic_guardrail
    GUARDRAILS_AVAILABLE = True
except ImportError:
    GUARDRAILS_AVAILABLE = False
    safety_guardrail = None
    biology_topic_guardrail = None


def _create_streaming_tool(
    agent: Agent,
    tool_name: str,
    tool_description: str,
    specialist_name: str,
    run_config: Optional[RunConfig] = None,
) -> Callable:
    """
    Create a streaming tool wrapper for a specialist agent.

    Unlike as_tool(), this wrapper uses run_specialist_with_events() to capture
    internal tool calls and emit events to the audit panel.

    Args:
        agent: The specialist agent to wrap
        tool_name: The tool name (e.g., "ask_pdf_specialist")
        tool_description: Description for the LLM
        specialist_name: Human-readable name for audit events
        run_config: Optional run configuration

    Returns:
        A function_tool decorated async function
    """
    @function_tool(name_override=tool_name, description_override=tool_description)
    async def streaming_tool_wrapper(query: str) -> str:
        """Ask the specialist a question and get a response."""
        return await run_specialist_with_events(
            agent=agent,
            input_text=query,
            specialist_name=specialist_name,
            run_config=run_config,
            tool_name=tool_name,  # Pass tool_name for batching nudge tracking
        )

    return streaming_tool_wrapper


def _build_model_settings(
    model: str,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[ReasoningEffort] = None,
    provider_override: Optional[str] = None,
) -> Optional[ModelSettings]:
    """
    Build ModelSettings with optional reasoning for models that support it.

    Reasoning is supported on:
    - GPT-5 family models (gpt-5, gpt-5-mini)
    - Gemini 3 models (gemini-3.0-pro) - uses "low"/"high" thinking levels
    - Gemini 2.5 models (gemini-2.5-pro, gemini-2.5-flash) - uses thinking budgets

    IMPORTANT: GPT-5 models don't support the temperature parameter -
    they use reasoning instead. Gemini models support both.

    For Gemini, the OpenAI SDK's reasoning_effort parameter maps to:
    - minimal/low -> "low" thinking level (Gemini 3) or 1,024 budget (Gemini 2.5)
    - medium -> "high" thinking level (Gemini 3) or 8,192 budget (Gemini 2.5)
    - high -> "high" thinking level (Gemini 3) or 24,576 budget (Gemini 2.5)

    Args:
        model: The model name (e.g., "gpt-5", "gpt-4o", "gemini-3.0-pro")
        temperature: Optional temperature override (0.0-1.0)
        reasoning_effort: Optional reasoning effort for models that support it

    Returns:
        ModelSettings instance or None if no settings needed
    """
    from ..config import supports_reasoning, supports_temperature, resolve_model_provider

    # Build reasoning config for models that support it
    reasoning = None
    if reasoning_effort and supports_reasoning(model):
        reasoning = Reasoning(effort=reasoning_effort)

    # GPT-5 models don't support temperature parameter, others do
    effective_temperature = temperature if supports_temperature(model) else None

    # Provider-level capability gates (configured in providers.yaml)
    model_provider = resolve_model_provider(model, provider_override)
    from src.lib.config.providers_loader import get_provider

    provider_def = get_provider(model_provider)
    if provider_def is None:
        raise ValueError(f"Unknown provider_id: {model_provider}")
    parallel_tool_calls = bool(provider_def.supports_parallel_tool_calls)

    # Only create ModelSettings if we have something to set
    if reasoning is not None or effective_temperature is not None or not parallel_tool_calls:
        return ModelSettings(
            temperature=effective_temperature,
            reasoning=reasoning,
            parallel_tool_calls=parallel_tool_calls
        )

    return None


def get_supervisor_agent_tools() -> List[str]:
    """
    Get list of tool names for supervisor-enabled system agents.
    """
    tools = _get_supervisor_specialist_specs()
    return [t["tool_name"] for t in tools]


def generate_routing_table() -> str:
    """
    Build supervisor routing table from unified agent records.

    Returns markdown table with tool names and descriptions.
    """
    tools = _get_supervisor_specialist_specs()

    rows = ["| Tool | When to Use |", "|------|-------------|"]

    for tool in tools:
        tool_name = tool["tool_name"]
        description = tool["description"]
        if tool_name and description:
            rows.append(f"| {tool_name} | {description} |")

    return "\n".join(rows)


def _get_supervisor_specialist_specs() -> List[Dict[str, Any]]:
    """Load supervisor-enabled system agents from unified DB records."""
    from src.models.sql.agent import Agent as AgentRecord
    from src.models.sql.database import SessionLocal
    from src.lib.agent_studio.catalog_service import get_agent_metadata

    db = SessionLocal()
    try:
        rows = db.query(AgentRecord).filter(
            AgentRecord.visibility == "system",
            AgentRecord.is_active == True,  # noqa: E712
            AgentRecord.supervisor_enabled == True,  # noqa: E712
        ).order_by(AgentRecord.agent_key.asc()).all()
    finally:
        db.close()

    specs: List[Dict[str, Any]] = []
    for row in rows:
        try:
            metadata = get_agent_metadata(row.agent_key)
            requires_document = bool(metadata.get("requires_document", False))
        except Exception:
            logger.exception(
                "Failed to resolve metadata for supervisor specialist '%s'",
                row.agent_key,
            )
            continue

        specs.append(
            {
                "agent_key": row.agent_key,
                "name": row.name,
                "description": row.supervisor_description or row.description or f"Ask {row.name}",
                "tool_name": f"ask_{row.agent_key.replace('-', '_')}_specialist",
                "requires_document": requires_document,
                "group_rules_enabled": bool(row.group_rules_enabled),
                "batchable": bool(row.supervisor_batchable),
                "batching_entity": row.supervisor_batching_entity,
            }
        )

    return specs


def _create_dynamic_specialist_tools(
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    document_name: Optional[str] = None,
    sections: Optional[List[str]] = None,
    hierarchy: Optional[Dict[str, Any]] = None,
    abstract: Optional[str] = None,
    active_groups: Optional[List[str]] = None,
) -> List[Callable]:
    """
    Dynamically create specialist tools based on unified agent records.

    Args:
        document_id: UUID of loaded document (for document-dependent agents)
        user_id: User ID for tenant isolation (for document-dependent agents)
        document_name: Name of the document for context
        sections: Flat list of section names from document
        hierarchy: Hierarchical document structure
        abstract: Paper abstract for context injection
        active_groups: Group IDs for rule injection (e.g., ["MGI", "FB"])

    Returns:
        List of function_tool decorated callables
    """
    from src.lib.agent_studio.catalog_service import get_agent_by_id

    tools_metadata = _get_supervisor_specialist_specs()
    specialist_tools = []

    for tool_meta in tools_metadata:
        tool_name = tool_meta["tool_name"]
        agent_key = tool_meta["agent_key"]
        description = tool_meta["description"]
        requires_document = tool_meta.get("requires_document", False)
        group_rules_enabled = tool_meta.get("group_rules_enabled", False)

        # Skip document-dependent agents if no document is loaded
        if requires_document and (not document_id or not user_id):
            logger.debug("Skipping %s - requires document but none loaded", tool_name)
            continue

        # Build runtime kwargs for unified agent builder
        agent_kwargs: Dict[str, Any] = {}
        if requires_document:
            agent_kwargs.update({
                "document_id": document_id,
                "user_id": user_id,
                "document_name": document_name,
                "sections": sections,
                "hierarchy": hierarchy,
                "abstract": abstract,
            })

        # Group-aware agents (MODs, institutions, teams, etc.)
        if group_rules_enabled and active_groups:
            agent_kwargs["active_groups"] = active_groups

        try:
            # Create the agent instance from unified spec.
            agent = get_agent_by_id(agent_key, **agent_kwargs)

            specialist_name = (
                str(tool_meta.get("name") or agent.name or agent_key)
                .replace(" Agent", "")
                .replace(" Validation", "")
            )

            streaming_tool = _create_streaming_tool(
                agent=agent,
                tool_name=tool_name,
                tool_description=description,
                specialist_name=specialist_name,
            )
            specialist_tools.append(streaming_tool)

            logger.info("Created dynamic tool: %s", tool_name)

        except Exception as e:
            logger.error("Failed to create tool %s for %s: %s", tool_name, agent_key, e)
            continue

    # Warn if no specialist tools were created
    if not specialist_tools:
        logger.warning("No specialist tools created - supervisor may have limited functionality")

    return specialist_tools


def create_supervisor_agent(
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    document_name: Optional[str] = None,
    hierarchy: Optional[Dict[str, Any]] = None,
    abstract: Optional[str] = None,
    enable_guardrails: bool = False,  # Enable input guardrails (PII detection, topic check)
    active_groups: Optional[List[str]] = None,  # Group-specific rules to inject (e.g., ["MGI", "FB"])
) -> Agent:
    """
    Create a Supervisor agent with dynamically discovered specialist tools.

    DYNAMIC AGENT DISCOVERY:
    Specialist tools are discovered from unified `agents` table records where
    `visibility='system'` and `supervisor_enabled=true`.
    Document-dependent agents are filtered out if no document is loaded.

    Each specialist runs in isolation with its own context window.
    Only the specialist's final output returns to the supervisor, preventing
    context window explosion from accumulated tool outputs.

    All agent settings (model, temperature, reasoning) are configured via environment
    variables. See config.py for available settings.

    Built-in Tools (always available):
    - export_to_file: Export data to CSV, TSV, or JSON files

    Args:
        document_id: The UUID of the PDF document (for document-dependent specialists)
        user_id: The user's user ID for tenant isolation (for document-dependent specialists)
        document_name: Optional name of the document for context
        hierarchy: Optional pre-fetched document hierarchy (avoids duplicate fetch)
        abstract: Optional pre-fetched paper abstract (injected into specialist prompts)
        enable_guardrails: Enable input guardrails for safety (default: False)
        active_groups: Optional list of group IDs to inject rules for (e.g., ["MGI", "FB"]).
                       Passed to agents with group_rules_enabled=True for group-specific behavior.

    Returns:
        An Agent instance configured as a supervisor with specialist tools
    """
    from ..config import (
        get_agent_config,
        log_agent_config,
        get_model_for_agent,
        resolve_model_provider,
    )
    route_start = time.monotonic()

    # Get supervisor config from registry + environment
    config = get_agent_config("supervisor")
    log_agent_config("Supervisor", config)

    model_provider = resolve_model_provider(config.model)

    # Get the model (returns LitellmModel for Gemini/Groq, string for OpenAI)
    model = get_model_for_agent(config.model, provider_override=model_provider)

    # Build model settings for supervisor
    supervisor_settings = _build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=config.reasoning,
        provider_override=model_provider,
    )

    # Configure guardrails if enabled
    input_guardrails = []
    if enable_guardrails and GUARDRAILS_AVAILABLE:
        if safety_guardrail:
            input_guardrails.append(safety_guardrail)
        else:
            logger.warning("Guardrails requested but not available")
    elif enable_guardrails:
        logger.warning("Guardrails requested but module not imported")

    logger.info(
        "Creating Supervisor agent with dynamic tool discovery, model=%s temp=%s reasoning=%s",
        config.model,
        config.temperature,
        config.reasoning,
        extra={"operation": "supervisor_routing_setup"},
    )

    # Extract section names from hierarchy for document-dependent agents
    sections = []
    if hierarchy and hierarchy.get("sections"):
        sections = [s.get("name") for s in hierarchy.get("sections", []) if s.get("name")]
        logger.info("Extracted %s sections from pre-fetched hierarchy", len(sections))

    # =========================================================================
    # DYNAMIC SPECIALIST TOOL CREATION
    # =========================================================================
    # Discover enabled agents from unified records and create streaming tool wrappers.
    # Document-dependent agents are automatically filtered if no document is loaded.
    # MOD-specific rules are injected for agents with group_rules_enabled=True.
    # =========================================================================
    specialist_tools = _create_dynamic_specialist_tools(
        document_id=document_id,
        user_id=user_id,
        document_name=document_name,
        sections=sections,
        hierarchy=hierarchy,
        abstract=abstract,
        active_groups=active_groups,
    )

    routing_duration_ms = (time.monotonic() - route_start) * 1000
    logger.info(
        "Dynamic discovery created %s specialist tools",
        len(specialist_tools),
        extra={
            "operation": "supervisor_routing_setup",
            "specialist_tool_count": len(specialist_tools),
            "duration_ms": round(routing_duration_ms, 1),
        },
    )

    # Export to File tool (always available - supervisor built-in, not a specialist agent)
    # Allows supervisor to export data as downloadable CSV, TSV, or JSON files
    @function_tool(
        name_override="export_to_file",
        description_override="""Export data to a downloadable file. Use when user asks to:
- Export, download, or save data as CSV, TSV, or JSON
- Get a spreadsheet or file version of results
- "Give me this as CSV", "TSV format please", "Download as JSON"

Supported formats: csv, tsv, json

The tool returns file information including a download URL that will render as a download button in the chat."""
    )
    async def export_to_file_tool(
        format_type: str,
        data: str,
        filename_hint: str = "export"
    ) -> str:
        """
        Export data to a downloadable file.

        Args:
            format_type: "csv", "tsv", or "json"
            data: The data to export as JSON string.
                  For CSV/TSV: JSON array of objects (e.g., '[{"gene": "BRCA1", "id": "123"}]')
                  For JSON: Any valid JSON structure
            filename_hint: Suggested filename without extension (e.g., "gene_results")

        Returns:
            JSON string with file information including download_url
        """
        import json as json_module
        from ..tools.file_output_tools import (
            _save_csv_impl,
            _save_tsv_impl,
            _save_json_impl,
        )

        format_type_lower = format_type.lower().strip()

        try:
            if format_type_lower == "csv":
                result = await _save_csv_impl(data, filename_hint)
            elif format_type_lower == "tsv":
                result = await _save_tsv_impl(data, filename_hint)
            elif format_type_lower == "json":
                result = await _save_json_impl(data, filename_hint)
            else:
                return json_module.dumps({
                    "error": f"Unsupported format: {format_type}. Supported formats: csv, tsv, json"
                })

            # Return the file info as JSON string
            return json_module.dumps(result)

        except ValueError as e:
            logger.error("export_to_file validation error: %s", e)
            return json_module.dumps({"error": str(e)})
        except Exception as e:
            logger.error("export_to_file error generating file: %s", e)
            return json_module.dumps({"error": f"Failed to generate file: {str(e)}"})

    specialist_tools.append(export_to_file_tool)

    # Get base prompt from cache (zero DB queries at runtime)
    base_prompt = get_prompt("supervisor")
    prompts_used = [base_prompt]

    # Build instructions from cached prompt
    instructions = base_prompt.content

    if document_id:
        # Document is loaded - tell supervisor to use it for extraction requests
        instructions += "\n\n**DOCUMENT CONTEXT**: A PDF document is loaded. When users ask to \"create annotation\", \"extract\", or request curation tasks, use the loaded document as the source."
    else:
        # No document - inform supervisor that PDF tools are unavailable
        instructions += "\n\nNOTE: No PDF document is currently loaded. The ask_pdf_specialist, ask_gene_expression_specialist, ask_phenotype_specialist, ask_allele_extractor_specialist, ask_disease_extractor_specialist, and ask_chemical_extractor_specialist tools are not available."

    # Inject group-specific rules for supervisor dispatch behavior
    if active_groups:
        try:
            from config.group_rules import inject_group_rules

            instructions = inject_group_rules(
                base_prompt=instructions,
                group_ids=active_groups,
                component_type="agents",
                component_name="supervisor",
                prompts_out=prompts_used,  # Collect group prompts for tracking
            )
            logger.info("Supervisor configured with group-specific dispatch rules: %s", active_groups)
        except ImportError as e:
            logger.warning("Could not import mod_config for supervisor, skipping injection: %s", e)
        except Exception as e:
            # Don't fail if supervisor rules don't exist - they're optional
            logger.debug("No supervisor group rules found or error: %s", e)

    logger.info(
        "Creating Supervisor agent, model=%s prompt_v=%s groups=%s",
        config.model,
        base_prompt.version,
        active_groups,
    )

    # Create the supervisor with specialist tools
    # Note: We don't use output_type=Answer here to preserve streaming text
    # (structured output generates JSON tokens which don't stream nicely)
    # Note: 'model' variable was set earlier via get_model_for_agent()
    # For Gemini: returns LitellmModel (handles thought_signature)
    # For OpenAI: returns model name string
    supervisor = Agent(
        name="Query Supervisor",
        instructions=instructions,
        model=model,  # LitellmModel for Gemini, string for OpenAI
        model_settings=supervisor_settings,
        input_guardrails=input_guardrails,
        tools=specialist_tools,
    )

    # Register prompts for execution logging (committed when agent actually runs)
    set_pending_prompts(supervisor.name, prompts_used)

    # Log supervisor configuration to Langfuse for trace visibility
    from ..langfuse_client import log_agent_config as log_agent_config_to_langfuse
    tool_names = [getattr(t, 'name', str(t)) for t in specialist_tools]
    log_agent_config_to_langfuse(
        agent_name="Query Supervisor",
        instructions=instructions,
        model=config.model,
        tools=tool_names,
        model_settings={
            "temperature": config.temperature,
            "reasoning": config.reasoning,
            "prompt_version": base_prompt.version,
        },
        metadata={
            "document_id": document_id,
            "user_id": user_id,
            "specialist_count": len(specialist_tools)
        }
    )

    logger.info("Supervisor configured with %s specialist tools", len(specialist_tools))

    return supervisor
