"""
Registry Builder - Builds AGENT_REGISTRY from resolved YAML configurations.

This module provides the bridge between layered agent definitions
and AGENT_REGISTRY metadata used by catalog_service.py.

Runtime packages are the primary source of truth, with `config/agents/`
acting as the override layer.
This module builds the registry dynamically at startup.
"""

import logging
from typing import Any, Dict, Optional

from src.lib.config.agent_loader import (
    AgentDefinition,
    ModelConfig,
    canonical_system_agent_key,
    load_agent_definitions,
    get_agent_definition,
)
from src.lib.agent_studio.system_agent_docs import get_system_agent_documentation

logger = logging.getLogger(__name__)


def _build_config_defaults(model_config: Optional[ModelConfig]) -> Dict[str, Any]:
    """
    Build config_defaults dict from YAML model_config.

    Always preserves the per-agent model from YAML so each agent can
    declare its own authoritative runtime model default. Other settings
    only include non-default values to avoid unnecessary overrides.

    Priority in get_agent_config():
    1. Environment variable (highest)
    2. config_defaults from YAML (this)
    3. Global fallback defaults (lowest)

    Args:
        model_config: ModelConfig from agent.yaml

    Returns:
        Dictionary with model, temperature, reasoning defaults
    """
    if model_config is None:
        raise ValueError(
            "Cannot build config_defaults: agent is missing model_config "
            "(model and reasoning are required, no code fallback)."
        )

    # model is the required per-agent default declared in the package agent.yaml,
    # so it is always preserved. reasoning and temperature are optional and only
    # included when the agent actually sets them (e.g. the supervisor omits
    # reasoning; Gemini agents set temperature). There is no ModelConfig() code
    # default to compare against.
    defaults: Dict[str, Any] = {"model": model_config.model}

    if model_config.reasoning is not None:
        defaults["reasoning"] = model_config.reasoning
    if model_config.temperature is not None:
        defaults["temperature"] = model_config.temperature

    return defaults


def _agent_definition_to_registry_entry(
    agent_def: AgentDefinition,
) -> Dict[str, Any]:
    """
    Convert an AgentDefinition to an AGENT_REGISTRY entry.

    Args:
        agent_def: AgentDefinition from YAML

    Returns:
        Dictionary in AGENT_REGISTRY format
    """
    # Package-owned documentation keeps domain-specific UI copy with the
    # agent bundle (loaded from each agent's docs.yaml into
    # agent_def.documentation). Configured synthetic flow nodes that ship no
    # docs.yaml (e.g. curation_prep) keep their curator-facing prose in
    # system_agent_docs.yaml.
    doc = agent_def.documentation or get_system_agent_documentation(
        agent_def.agent_id
    )
    if not doc and agent_def.description.strip():
        doc = {"summary": agent_def.description.strip()}

    system_agent_key = canonical_system_agent_key(agent_def)
    supervisor_tool_name = f"ask_{system_agent_key.replace('-', '_')}_specialist"

    # Build batching config if agent is batchable
    batching = None
    if agent_def.supervisor_routing.batchable:
        entity = agent_def.supervisor_routing.batching_entity
        # Generate example: ask_gene_specialist("Look up these genes: ...")
        batching = {
            "entity": entity,
            "example": f'{supervisor_tool_name}("Look up these {entity}: ...")',
        }

    return {
        "name": agent_def.name,
        "description": agent_def.description,
        "category": agent_def.category,
        "subcategory": agent_def.subcategory,
        "package_id": agent_def.package_id,
        "has_group_rules": agent_def.group_rules_enabled,
        "tools": agent_def.tools,
        "factory": None,
        "requires_document": agent_def.requires_document,
        "required_params": agent_def.required_params,
        "batch_capabilities": agent_def.batch_capabilities,
        "config_defaults": _build_config_defaults(agent_def.model_config),
        "supervisor": {
            "enabled": agent_def.supervisor_routing.enabled,
            "tool_name": supervisor_tool_name,
        },
        "batching": batching,
        "frontend": {
            "icon": agent_def.frontend.icon,
            "show_in_palette": agent_def.frontend.show_in_palette,
        },
        "curation": {
            "adapter_key": agent_def.curation.adapter_key,
            "domain_pack_id": agent_def.curation.domain_pack_id,
            "launchable": agent_def.curation.launchable,
        },
        "documentation": doc if doc else None,
    }


def build_agent_registry() -> Dict[str, Dict[str, Any]]:
    """
    Build AGENT_REGISTRY from YAML configurations.

    Loads all resolved agent definitions from runtime packages plus
    `config/agents/` overrides and converts them to AGENT_REGISTRY metadata
    entries.

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
        "package_id": None,
        "has_group_rules": False,
        "tools": [],
        "factory": None,  # Not an executable agent
        "requires_document": False,
        "required_params": [],
        "batch_capabilities": [],
        "frontend": {
            "icon": "📋",
            "show_in_palette": False,
        },
        "documentation": get_system_agent_documentation("task_input"),
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
        entry = _agent_definition_to_registry_entry(agent_def)
        registry[agent_id] = entry

        # Keep folder-name aliases only when the folder is the configured
        # public key. Bundles with an explicit system_agent_key expose that
        # canonical route only.
        if (
            agent_def.folder_name != agent_id
            and agent_def.folder_name not in registry
            and agent_def.folder_name != "pdf"
            and (
                not agent_def.system_agent_key
                or agent_def.system_agent_key == agent_def.folder_name
            )
        ):
            registry[agent_def.folder_name] = entry

        logger.debug(
            "Added to registry: %s (folder=%s)",
            agent_id,
            agent_def.folder_name,
        )

    logger.info("Built AGENT_REGISTRY with %s entries", len(registry))

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
            "has_group_rules": False,
            "tools": [],
            "factory": None,
            "requires_document": False,
            "required_params": [],
            "batch_capabilities": [],
            "frontend": {
                "icon": "📋",
                "show_in_palette": False,
            },
            "documentation": get_system_agent_documentation("task_input"),
        }

    # Get agent definition from YAML
    agent_def = get_agent_definition(agent_id)
    if agent_def is None:
        return None

    return _agent_definition_to_registry_entry(agent_def)
