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
Specialist agents are discovered from YAML config files via get_supervisor_tools().
Factory functions are registered in AGENT_FACTORIES mapping agent_id to callable.
"""

import asyncio
import logging
from typing import Optional, List, Literal, Dict, Any, Callable

from agents import Agent, ModelSettings, Runner, RunConfig, function_tool
from agents.models.openai_provider import OpenAIProvider
from openai.types.shared import Reasoning

from ..streaming_tools import run_specialist_with_events

# Prompt cache and context tracking imports
from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

# Config-driven architecture imports
from src.lib.config import get_supervisor_tools

# Note: Answer model not used here - supervisor streams plain text for better UX

logger = logging.getLogger(__name__)

# Type alias for reasoning effort levels
ReasoningEffort = Literal["minimal", "low", "medium", "high"]


# =============================================================================
# AGENT FACTORY REGISTRY
# =============================================================================
# Maps agent_id to factory function for dynamic agent creation.
# Factory functions are imported lazily to avoid circular imports.
# Each factory is called with appropriate kwargs based on agent requirements.
# =============================================================================

def _get_agent_factory(agent_id: str) -> Optional[Callable]:
    """
    Get the factory function for an agent by its agent_id.

    Uses lazy imports to avoid circular import issues at module load time.

    Args:
        agent_id: The agent identifier (e.g., "gene_validation", "pdf_extraction")

    Returns:
        Factory function or None if not found
    """
    # Lazy import mapping to avoid circular imports
    # Keys MUST match agent_id values in alliance_agents/*/agent.yaml
    factory_mapping = {
        # Validation agents
        "gene_validation": ("src.lib.openai_agents.agents.gene_agent", "create_gene_agent"),
        "allele_validation": ("src.lib.openai_agents.agents.allele_agent", "create_allele_agent"),
        "disease_validation": ("src.lib.openai_agents.agents.disease_agent", "create_disease_agent"),
        "chemical_validation": ("src.lib.openai_agents.agents.chemical_agent", "create_chemical_agent"),
        # Lookup agents (query external APIs/databases)
        "gene_ontology_lookup": ("src.lib.openai_agents.agents.gene_ontology_agent", "create_gene_ontology_agent"),
        "go_annotations_lookup": ("src.lib.openai_agents.agents.go_annotations_agent", "create_go_annotations_agent"),
        "orthologs_lookup": ("src.lib.openai_agents.agents.orthologs_agent", "create_orthologs_agent"),
        "ontology_mapping_lookup": ("src.lib.openai_agents.agents.ontology_mapping_agent", "create_ontology_mapping_agent"),
        # Extraction agents (require document)
        "pdf_extraction": ("src.lib.openai_agents.pdf_agent", "create_pdf_agent"),
        "gene_expression_extraction": ("src.lib.openai_agents.agents.gene_expression_agent", "create_gene_expression_agent"),
    }

    if agent_id not in factory_mapping:
        return None

    module_path, factory_name = factory_mapping[agent_id]

    try:
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, factory_name)
    except (ImportError, AttributeError) as e:
        logger.warning(f"[OpenAI Agents] Failed to import factory for {agent_id}: {e}")
        return None


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
        logger.warning(f"[OpenAI Agents] Failed to fetch document sections: {e}")
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
        logger.warning(f"[OpenAI Agents] Failed to fetch document hierarchy: {e}")
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
    from ..config import supports_reasoning, supports_temperature, is_gemini_provider, get_model_for_agent

    # Build reasoning config for models that support it
    reasoning = None
    if reasoning_effort and supports_reasoning(model):
        reasoning = Reasoning(effort=reasoning_effort)

    # GPT-5 models don't support temperature parameter, others do
    effective_temperature = temperature if supports_temperature(model) else None

    # Gemini doesn't support parallel tool calls
    parallel_tool_calls = False if is_gemini_provider() else True

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
    Get list of tool names for supervisor from YAML config files.

    Uses the config-driven get_supervisor_tools() to discover enabled agents.

    Returns tool names for agents that have supervisor_routing.enabled=True.
    Tool names are generated as ask_{folder}_specialist.
    """
    tools = get_supervisor_tools()
    return [t["tool_name"] for t in tools]


def generate_routing_table() -> str:
    """
    Build supervisor routing table from YAML config files.

    Returns markdown table with tool names and descriptions.
    Tool metadata comes from agent.yaml supervisor_routing sections.
    """
    tools = get_supervisor_tools()

    rows = ["| Tool | When to Use |", "|------|-------------|"]

    for tool in tools:
        tool_name = tool["tool_name"]
        description = tool["description"]
        if tool_name and description:
            rows.append(f"| {tool_name} | {description} |")

    return "\n".join(rows)


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
    Dynamically create specialist tools based on discovered agent configs.

    Uses get_supervisor_tools() to discover enabled agents and creates
    streaming tool wrappers for each one.

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
    from src.lib.config import get_agent_by_folder

    tools_metadata = get_supervisor_tools()
    specialist_tools = []

    for tool_meta in tools_metadata:
        tool_name = tool_meta["tool_name"]
        agent_id = tool_meta["agent_id"]
        folder_name = tool_meta["folder_name"]
        description = tool_meta["description"]
        requires_document = tool_meta.get("requires_document", False)
        group_rules_enabled = tool_meta.get("group_rules_enabled", False)

        # Skip document-dependent agents if no document is loaded
        if requires_document and (not document_id or not user_id):
            logger.debug(f"[OpenAI Agents] Skipping {tool_name} - requires document but none loaded")
            continue

        # Get factory function
        factory = _get_agent_factory(agent_id)
        if factory is None:
            logger.warning(f"[OpenAI Agents] No factory found for agent: {agent_id}")
            continue

        # Build factory kwargs based on agent requirements
        factory_kwargs = {}

        # Document-dependent agents
        if requires_document:
            factory_kwargs.update({
                "document_id": document_id,
                "user_id": user_id,
                "document_name": document_name,
                "sections": sections,
                "hierarchy": hierarchy,
                "abstract": abstract,
            })

        # Group-aware agents (MODs, institutions, teams, etc.)
        if group_rules_enabled and active_groups:
            factory_kwargs["active_groups"] = active_groups

        try:
            # Create the agent instance
            agent = factory(**factory_kwargs)

            # Get agent definition for display name (optional lookup)
            agent_def = get_agent_by_folder(folder_name)
            if agent_def:
                specialist_name = agent_def.name.replace(" Agent", "").replace(" Validation", "")
            else:
                # Fallback: derive from folder name
                specialist_name = folder_name.replace("_", " ").title()

            streaming_tool = _create_streaming_tool(
                agent=agent,
                tool_name=tool_name,
                tool_description=description,
                specialist_name=specialist_name,
            )
            specialist_tools.append(streaming_tool)

            logger.info(f"[OpenAI Agents] Created dynamic tool: {tool_name}")

        except Exception as e:
            logger.error(f"[OpenAI Agents] Failed to create tool {tool_name}: {e}")
            continue

    # Warn if no specialist tools were created
    if not specialist_tools:
        logger.warning("[OpenAI Agents] No specialist tools created - supervisor may have limited functionality")

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
    Specialist tools are discovered from YAML config files (alliance_agents/*/agent.yaml)
    via get_supervisor_tools(). Only agents with supervisor_routing.enabled=True are included.
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
    from ..config import get_agent_config, log_agent_config, get_model_for_agent

    # Get supervisor config from registry + environment
    config = get_agent_config("supervisor")
    log_agent_config("Supervisor", config)

    # Get the model (returns LitellmModel for Gemini, string for OpenAI)
    model = get_model_for_agent(config.model)

    # Build model settings for supervisor
    supervisor_settings = _build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=config.reasoning,
    )

    # Configure guardrails if enabled
    input_guardrails = []
    if enable_guardrails and GUARDRAILS_AVAILABLE:
        if safety_guardrail:
            input_guardrails.append(safety_guardrail)
        else:
            logger.warning("[OpenAI Agents] Guardrails requested but not available")
    elif enable_guardrails:
        logger.warning("[OpenAI Agents] Guardrails requested but module not imported")

    logger.info(
        f"[OpenAI Agents] Creating Supervisor agent with dynamic tool discovery, "
        f"model={config.model}, temp={config.temperature}, reasoning={config.reasoning}"
    )

    # Extract section names from hierarchy for document-dependent agents
    sections = []
    if hierarchy and hierarchy.get("sections"):
        sections = [s.get("name") for s in hierarchy.get("sections", []) if s.get("name")]
        logger.info(f"[OpenAI Agents] Extracted {len(sections)} sections from pre-fetched hierarchy")

    # =========================================================================
    # DYNAMIC SPECIALIST TOOL CREATION
    # =========================================================================
    # Discover enabled agents from YAML configs and create streaming tool wrappers.
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

    logger.info(f"[OpenAI Agents] Dynamic discovery created {len(specialist_tools)} specialist tools")

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
            logger.error(f"[export_to_file] Validation error: {e}")
            return json_module.dumps({"error": str(e)})
        except Exception as e:
            logger.error(f"[export_to_file] Error generating file: {e}")
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
        instructions += "\n\nNOTE: No PDF document is currently loaded. The ask_pdf_specialist and ask_gene_expression_specialist tools are not available."

    # Inject group-specific rules for supervisor dispatch behavior
    if active_groups:
        try:
            from config.mod_rules.mod_config import inject_group_rules

            instructions = inject_group_rules(
                base_prompt=instructions,
                group_ids=active_groups,
                component_type="agents",
                component_name="supervisor",
                prompts_out=prompts_used,  # Collect group prompts for tracking
            )
            logger.info(f"Supervisor configured with group-specific dispatch rules: {active_groups}")
        except ImportError as e:
            logger.warning(f"Could not import mod_config for supervisor, skipping injection: {e}")
        except Exception as e:
            # Don't fail if supervisor rules don't exist - they're optional
            logger.debug(f"No supervisor group rules found or error: {e}")

    logger.info(
        f"[OpenAI Agents] Creating Supervisor agent, model={config.model}, "
        f"prompt_v={base_prompt.version}, groups={active_groups}"
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
    from ..langfuse_client import log_agent_config
    tool_names = [getattr(t, 'name', str(t)) for t in specialist_tools]
    log_agent_config(
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

    logger.info(f"[OpenAI Agents] Supervisor configured with {len(specialist_tools)} specialist tools")

    return supervisor
