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
    Get list of tool names for supervisor from AGENT_REGISTRY.

    Returns tool names for agents that have supervisor.enabled=True (default).
    Excludes:
    - Entries without factories (like task_input)
    - Entries with supervisor.enabled=False (like formatters)
    """
    from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

    tools = []
    for agent_id, entry in AGENT_REGISTRY.items():
        # Skip non-agent entries
        if entry.get("factory") is None:
            continue

        supervisor = entry.get("supervisor", {})

        # Skip disabled agents (default is enabled)
        if not supervisor.get("enabled", True):
            continue

        tool_name = supervisor.get("tool_name")
        if tool_name:
            tools.append(tool_name)

    return tools


def generate_routing_table() -> str:
    """
    Build supervisor routing table from AGENT_REGISTRY.

    Returns markdown table with tool names and descriptions.
    """
    from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

    rows = ["| Tool | When to Use |", "|------|-------------|"]

    for agent_id, entry in AGENT_REGISTRY.items():
        if entry.get("factory") is None:
            continue

        supervisor = entry.get("supervisor", {})
        if not supervisor.get("enabled", True):
            continue

        tool_name = supervisor.get("tool_name")
        description = supervisor.get("tool_description")

        if tool_name and description:
            rows.append(f"| {tool_name} | {description} |")

    return "\n".join(rows)


def create_supervisor_agent(
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    document_name: Optional[str] = None,
    hierarchy: Optional[Dict[str, Any]] = None,
    abstract: Optional[str] = None,
    enable_guardrails: bool = False,  # Enable input guardrails (PII detection, topic check)
    active_mods: Optional[List[str]] = None,  # MOD-specific rules to inject (e.g., ["MGI", "FB"])
) -> Agent:
    """
    Create a Supervisor agent with specialist tools (as_tool pattern).

    Each specialist runs in isolation with its own context window.
    Only the specialist's final output returns to the supervisor, preventing
    context window explosion from accumulated tool outputs.

    All agent settings (model, temperature, reasoning) are configured via environment
    variables. See config.py for available settings.

    Available Specialist Tools:
    - ask_pdf_specialist (requires document_id and user_id)
    - ask_gene_expression_specialist (requires document_id and user_id)
    - ask_gene_specialist
    - ask_allele_specialist
    - ask_disease_specialist
    - ask_chemical_specialist
    - ask_gene_ontology_specialist
    - ask_go_annotations_specialist
    - ask_orthologs_specialist
    - ask_ontology_mapping_specialist

    Args:
        document_id: The UUID of the PDF document (for PDF/Expression specialists)
        user_id: The user's user ID for tenant isolation (for PDF/Expression specialists)
        document_name: Optional name of the document for context
        hierarchy: Optional pre-fetched document hierarchy (avoids duplicate fetch)
        abstract: Optional pre-fetched paper abstract (injected into specialist prompts)
        enable_guardrails: Enable input guardrails for safety (default: False)
        active_mods: Optional list of MOD IDs to inject rules for (e.g., ["MGI", "FB"]).
                     Passed to gene and allele agents for MOD-specific behavior.

    Returns:
        An Agent instance configured as a supervisor with specialist tools
    """
    # Import agent factory functions and config
    from .disease_agent import create_disease_agent
    from .gene_agent import create_gene_agent
    from .chemical_agent import create_chemical_agent
    from .allele_agent import create_allele_agent
    from .orthologs_agent import create_orthologs_agent
    from .gene_ontology_agent import create_gene_ontology_agent
    from .go_annotations_agent import create_go_annotations_agent
    from .ontology_mapping_agent import create_ontology_mapping_agent
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
        f"[OpenAI Agents] Creating Supervisor agent with streaming tool wrappers, "
        f"model={config.model}, temp={config.temperature}, reasoning={config.reasoning}"
    )

    # Create specialist tools using streaming tool wrappers
    # Each specialist runs in isolation with event capture for audit visibility
    specialist_tools = []

    # PDF specialist tool (only if document is loaded)
    if document_id and user_id:
        from ..pdf_agent import create_pdf_agent

        # Extract flat section names from pre-fetched hierarchy (avoids duplicate Weaviate query)
        # The hierarchy is already fetched in runner.py and passed here
        sections = []
        if hierarchy and hierarchy.get("sections"):
            sections = [s.get("name") for s in hierarchy.get("sections", []) if s.get("name")]
            logger.info(f"[OpenAI Agents] Extracted {len(sections)} sections from pre-fetched hierarchy")

        if hierarchy and hierarchy.get("sections"):
            logger.info(f"[OpenAI Agents] Using pre-fetched hierarchy with {len(hierarchy.get('sections', []))} sections for PDF agent prompt")
        elif sections:
            logger.info(f"[OpenAI Agents] Fetched {len(sections)} flat sections for PDF agent prompt (no hierarchy)")
        else:
            logger.warning("[OpenAI Agents] No document structure found for PDF agent (will use search-only mode)")

        pdf_agent = create_pdf_agent(
            document_id=document_id,
            user_id=user_id,
            document_name=document_name,
            sections=sections,
            hierarchy=hierarchy,
            abstract=abstract
        )
        specialist_tools.append(_create_streaming_tool(
            agent=pdf_agent,
            tool_name="ask_pdf_specialist",
            tool_description="Ask the PDF Specialist about the loaded document. Use for questions about paper content, methods, results, figures. The specialist will search and read the document autonomously.",
            specialist_name="PDF Specialist"
        ))

        # Gene Expression specialist tool (also requires document)
        from .gene_expression_agent import create_gene_expression_agent
        expression_agent = create_gene_expression_agent(
            document_id=document_id,
            user_id=user_id,
            document_name=document_name,
            sections=sections,
            hierarchy=hierarchy,
            abstract=abstract,
            active_mods=active_mods,  # Pass MOD-specific rules (e.g., WB anatomy preferences)
        )
        specialist_tools.append(_create_streaming_tool(
            agent=expression_agent,
            tool_name="ask_gene_expression_specialist",
            tool_description="Extract gene expression data from the paper. Returns formatted text with organism, genes, annotations (anatomy, life stage, reagent, evidence). Use export_to_file to save results as CSV, TSV, or JSON.",
            specialist_name="Gene Expression Specialist"
        ))

    # Export to File tool (always available)
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

    # Gene Curation specialist tool (with MOD-specific injection)
    gene_agent = create_gene_agent(active_mods=active_mods)
    specialist_tools.append(_create_streaming_tool(
        agent=gene_agent,
        tool_name="ask_gene_specialist",
        tool_description="Ask the Gene Curation Specialist about genes. Use for gene symbols, IDs, names, genomic locations. Supports worm (WB), fly (FB), mouse (MGI), human (HGNC), zebrafish (ZFIN), rat (RGD), yeast (SGD).",
        specialist_name="Gene Specialist"
    ))

    # Allele Curation specialist tool (with MOD-specific injection)
    allele_agent = create_allele_agent(active_mods=active_mods)
    specialist_tools.append(_create_streaming_tool(
        agent=allele_agent,
        tool_name="ask_allele_specialist",
        tool_description="Ask the Allele Curation Specialist about alleles/variants. Use for allele symbols, names, species, obsolete/extinction status.",
        specialist_name="Allele Specialist"
    ))

    # Disease Ontology specialist tool
    disease_agent = create_disease_agent()
    specialist_tools.append(_create_streaming_tool(
        agent=disease_agent,
        tool_name="ask_disease_specialist",
        tool_description="Ask the Disease Ontology Specialist about diseases. Use for DOID terms, disease hierarchy, definitions, synonyms.",
        specialist_name="Disease Specialist"
    ))

    # Chemical Ontology specialist tool
    chemical_agent = create_chemical_agent()
    specialist_tools.append(_create_streaming_tool(
        agent=chemical_agent,
        tool_name="ask_chemical_specialist",
        tool_description="Ask the Chemical Ontology Specialist about chemicals/compounds. Use for ChEBI terms, chemical classifications, structures, formulas.",
        specialist_name="Chemical Specialist"
    ))

    # Gene Ontology specialist tool (QuickGO)
    go_agent = create_gene_ontology_agent()
    specialist_tools.append(_create_streaming_tool(
        agent=go_agent,
        tool_name="ask_gene_ontology_specialist",
        tool_description="Ask the Gene Ontology Specialist about GO terms. Use for GO term search, hierarchy (children/ancestors), definitions. NOT for gene annotations.",
        specialist_name="Gene Ontology Specialist"
    ))

    # GO Annotations specialist tool
    go_annotations_agent = create_go_annotations_agent()
    specialist_tools.append(_create_streaming_tool(
        agent=go_annotations_agent,
        tool_name="ask_go_annotations_specialist",
        tool_description="Ask the GO Annotations Specialist what GO annotations a gene has. Use for gene-to-GO-term associations, evidence codes, annotation sources.",
        specialist_name="GO Annotations Specialist"
    ))

    # Alliance Orthologs specialist tool
    orthologs_agent = create_orthologs_agent()
    specialist_tools.append(_create_streaming_tool(
        agent=orthologs_agent,
        tool_name="ask_orthologs_specialist",
        tool_description="Ask the Alliance Orthologs Specialist about ortholog relationships. Use for finding orthologs between species, confidence scores, prediction methods.",
        specialist_name="Orthologs Specialist"
    ))

    # Ontology Mapping specialist tool
    mapping_agent = create_ontology_mapping_agent()
    specialist_tools.append(_create_streaming_tool(
        agent=mapping_agent,
        tool_name="ask_ontology_mapping_specialist",
        tool_description="Ask the Ontology Mapping Specialist to map labels to ontology IDs. Use for anatomy terms → WBbt/FBbt, life stages → WBls/FBdv, cellular components → GO.",
        specialist_name="Ontology Mapping Specialist"
    ))

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

    # Inject MOD-specific rules for supervisor dispatch behavior
    if active_mods:
        try:
            from config.mod_rules.mod_config import inject_mod_rules

            instructions = inject_mod_rules(
                base_prompt=instructions,
                mod_ids=active_mods,
                component_type="agents",
                component_name="supervisor",
                prompts_out=prompts_used,  # Collect MOD prompts for tracking
            )
            logger.info(f"Supervisor configured with MOD-specific dispatch rules: {active_mods}")
        except ImportError as e:
            logger.warning(f"Could not import mod_config for supervisor, skipping injection: {e}")
        except Exception as e:
            # Don't fail if supervisor rules don't exist - they're optional
            logger.debug(f"No supervisor MOD rules found or error: {e}")

    logger.info(
        f"[OpenAI Agents] Creating Supervisor agent, model={config.model}, "
        f"prompt_v={base_prompt.version}, mods={active_mods}"
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
