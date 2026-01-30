"""
Registry Builder - Builds AGENT_REGISTRY from YAML configurations.

This module provides the bridge between config-driven agent definitions
and the AGENT_REGISTRY used by catalog_service.py.

YAML files (config/agents/*/agent.yaml) are the source of truth.
This module builds the registry dynamically at startup.
"""

import logging
from typing import Any, Callable, Dict, Optional

from src.lib.config.agent_loader import (
    AgentDefinition,
    load_agent_definitions,
    get_agent_definition,
    get_agent_by_folder,
)
from src.lib.config.agent_factory import get_agent_factory

logger = logging.getLogger(__name__)

# Static documentation for agents (help text for frontend)
#
# NOTE: This is intentionally separate from agent.yaml files because:
# 1. It's verbose UI content (examples, capabilities, limitations) that would
#    bloat the YAML files and make them harder to maintain
# 2. Not all agents need extensive documentation - many just need the brief
#    description from YAML
# 3. Documentation is presentation-layer concern, not agent configuration
# 4. Allows documentation to be updated without touching agent configs
#
# If YAML-based documentation is desired in the future, consider a separate
# docs.yaml file per agent or a dedicated documentation directory.
AGENT_DOCUMENTATION: Dict[str, Dict[str, Any]] = {
    "task_input": {
        "summary": "The starting point for curation workflows - defines what task the AI should perform.",
        "capabilities": [
            {
                "name": "Define extraction tasks",
                "description": "Tell the AI what data to extract from the paper",
                "example_query": "Extract all gene names and their expression patterns from this paper",
                "example_result": "The flow begins processing your request through the configured agents",
            },
            {
                "name": "Set curation context",
                "description": "Provide background information about what you're looking for",
                "example_query": "This paper describes insulin signaling in C. elegans. Find all relevant genes.",
                "example_result": "Context is passed to downstream agents to improve accuracy",
            },
        ],
        "data_sources": [],
        "limitations": [
            "Instructions should be clear and specific for best results",
            "Complex multi-part requests may need to be broken into separate flows",
        ],
    },
    "supervisor": {
        "summary": "Routes curator queries to the appropriate specialist agent - works behind the scenes.",
        "capabilities": [
            {
                "name": "Query routing",
                "description": "Automatically determines which specialist agent should handle your question",
                "example_query": "Find the gene daf-2",
                "example_result": "Routes to Gene Validation Agent",
            },
            {
                "name": "Multi-step coordination",
                "description": "Coordinates complex queries that need multiple agents",
                "example_query": "Find all genes mentioned in the paper and validate them",
                "example_result": "Routes to PDF Specialist first, then Gene Validation Agent",
            },
        ],
        "data_sources": [],
        "limitations": [
            "Routing decisions are based on query keywords and context",
            "Very ambiguous queries may be routed to a general agent first",
            "Not available in Flow Builder - works automatically in chat",
        ],
    },
    "pdf": {
        "summary": "Extracts text, tables, and structured data from scientific papers using hybrid search.",
        "capabilities": [
            {
                "name": "Full-text search",
                "description": "Find specific content within the PDF using semantic and keyword search",
                "example_query": "Find all mentions of insulin signaling pathway",
                "example_result": "Returns relevant passages with page numbers",
            },
            {
                "name": "Table extraction",
                "description": "Extract data tables and convert to structured format",
                "example_query": "Extract the gene expression table from results section",
                "example_result": "Returns structured data with headers and values",
            },
            {
                "name": "Section navigation",
                "description": "Read specific sections of the paper",
                "example_query": "Read the Methods section",
                "example_result": "Returns full text of the specified section",
            },
        ],
        "data_sources": [
            {
                "name": "PDF Documents",
                "description": "Scientific papers indexed in Weaviate",
                "species_supported": None,
                "data_types": ["text", "tables", "figures", "references"],
            },
        ],
        "limitations": [
            "Document must be loaded in Weaviate before use",
            "Very large tables may be truncated",
            "Figure analysis is limited to captions only",
        ],
    },
    "gene_validation": {
        "summary": "Validates gene identifiers against the Alliance Curation Database.",
        "capabilities": [
            {
                "name": "Gene lookup",
                "description": "Find genes by symbol, name, ID, or cross-reference",
                "example_query": "Look up the gene daf-16",
                "example_result": "Returns gene ID, symbol, name, species, and synonyms",
            },
            {
                "name": "Batch validation",
                "description": "Validate multiple genes at once",
                "example_query": "Look up these genes: daf-16, lin-3, unc-54, act-1",
                "example_result": "Returns validation results for each gene",
            },
        ],
        "data_sources": [
            {
                "name": "Alliance Curation Database",
                "description": "Comprehensive gene data from all MODs",
                "species_supported": [
                    "C. elegans", "D. melanogaster", "D. rerio",
                    "H. sapiens", "M. musculus", "R. norvegicus", "S. cerevisiae"
                ],
                "data_types": ["genes", "symbols", "synonyms", "cross-references"],
            },
        ],
        "limitations": [
            "Only validates against Alliance MOD data",
            "Some newly published genes may not be in the database yet",
        ],
    },
    # Additional documentation can be added here as needed
    # For agents without custom documentation, defaults will be used
}


def _agent_definition_to_registry_entry(
    agent_def: AgentDefinition,
    factory: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Convert an AgentDefinition to an AGENT_REGISTRY entry.

    Args:
        agent_def: AgentDefinition from YAML
        factory: Factory function (from convention-based discovery)

    Returns:
        Dictionary in AGENT_REGISTRY format
    """
    # Get documentation if available
    doc = AGENT_DOCUMENTATION.get(agent_def.agent_id, {})

    # Build batching config if agent is batchable
    batching = None
    if agent_def.supervisor_routing.batchable:
        entity = agent_def.supervisor_routing.batching_entity
        tool_name = agent_def.tool_name
        # Generate example: ask_gene_specialist("Look up these genes: ...")
        batching = {
            "entity": entity,
            "example": f'{tool_name}("Look up these {entity}: ...")',
        }

    return {
        "name": agent_def.name,
        "description": agent_def.description,
        "category": agent_def.category,
        "subcategory": agent_def.subcategory,
        "has_mod_rules": agent_def.group_rules_enabled,
        "tools": agent_def.tools,
        "factory": factory,
        "requires_document": agent_def.requires_document,
        "required_params": agent_def.required_params,
        "batch_capabilities": agent_def.batch_capabilities,
        "config_defaults": {
            "reasoning": agent_def.model_config.reasoning,
        } if agent_def.model_config.reasoning != "medium" else {},
        "supervisor": {
            "enabled": agent_def.supervisor_routing.enabled,
            "tool_name": agent_def.tool_name,
        },
        "batching": batching,
        "frontend": {
            "icon": agent_def.frontend.icon,
            "show_in_palette": agent_def.frontend.show_in_palette,
        },
        "documentation": doc if doc else None,
    }


def build_agent_registry() -> Dict[str, Dict[str, Any]]:
    """
    Build AGENT_REGISTRY from YAML configurations.

    Loads all agent definitions from config/agents/*/agent.yaml and
    converts them to AGENT_REGISTRY format. Uses convention-based
    factory discovery for agent creation.

    Returns:
        Dictionary mapping agent_id to registry entry

    Note:
        This function builds the registry fresh each time. For caching,
        use the AGENT_REGISTRY constant in catalog_service.py which calls
        this once at module load time.
    """
    registry: Dict[str, Dict[str, Any]] = {}

    # Add task_input as a special non-agent entry
    registry["task_input"] = {
        "name": "Initial Instructions",
        "description": "Define the curator's task that starts the flow",
        "category": "Input",
        "subcategory": "Input",
        "has_mod_rules": False,
        "tools": [],
        "factory": None,  # Not an executable agent
        "requires_document": False,
        "required_params": [],
        "batch_capabilities": [],
        "frontend": {
            "icon": "📋",
            "show_in_palette": False,
        },
        "documentation": AGENT_DOCUMENTATION.get("task_input"),
    }

    # Load all agent definitions from YAML
    try:
        agent_defs = load_agent_definitions()
    except FileNotFoundError:
        logger.warning(
            "Agent definitions not found. AGENT_REGISTRY will be minimal."
        )
        return registry

    # Convert each agent definition to registry format
    for agent_id, agent_def in agent_defs.items():
        # Get factory via convention-based discovery
        factory = get_agent_factory(agent_def.folder_name)

        if factory is None:
            logger.warning(
                f"No factory found for agent {agent_id} "
                f"(folder: {agent_def.folder_name}). "
                "Agent will be display-only."
            )

        entry = _agent_definition_to_registry_entry(agent_def, factory)
        registry[agent_id] = entry

        # Also add folder_name as an alias for backwards compatibility
        # This allows both AGENT_REGISTRY.get("pdf") and get("pdf_extraction")
        if agent_def.folder_name != agent_id and agent_def.folder_name not in registry:
            registry[agent_def.folder_name] = entry

        logger.debug(
            f"Added to registry: {agent_id} "
            f"(folder={agent_def.folder_name}, factory={'present' if factory else 'missing'})"
        )

    logger.info(
        f"Built AGENT_REGISTRY with {len(registry)} entries "
        f"({sum(1 for e in registry.values() if e.get('factory'))} with factories)"
    )

    return registry


def get_registry_entry(agent_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a single registry entry, building from YAML if needed.

    This is a convenience function that doesn't require loading
    the full registry.

    Args:
        agent_id: The agent identifier

    Returns:
        Registry entry dict or None if not found
    """
    # Special case for task_input
    if agent_id == "task_input":
        return {
            "name": "Initial Instructions",
            "description": "Define the curator's task that starts the flow",
            "category": "Input",
            "subcategory": "Input",
            "has_mod_rules": False,
            "tools": [],
            "factory": None,
            "requires_document": False,
            "required_params": [],
            "batch_capabilities": [],
            "frontend": {
                "icon": "📋",
                "show_in_palette": False,
            },
            "documentation": AGENT_DOCUMENTATION.get("task_input"),
        }

    # Get agent definition from YAML
    agent_def = get_agent_definition(agent_id)
    if agent_def is None:
        return None

    # Get factory
    factory = get_agent_factory(agent_def.folder_name)

    return _agent_definition_to_registry_entry(agent_def, factory)
