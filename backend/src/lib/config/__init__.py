"""
Config-driven architecture loaders.

This package provides dynamic discovery and loading of:
- Agent definitions from config/agents/*/agent.yaml
- Prompts from config/agents/*/prompt.yaml
- Schemas from config/agents/*/schema.py
- Group rules from config/agents/*/group_rules/*.yaml

YAML files are the source of truth. Database is a runtime cache.
"""

from .agent_loader import (
    AgentDefinition,
    SupervisorRouting,
    ModelConfig,
    FrontendConfig,
    load_agent_definitions,
    get_agent_definition,
    get_agent_by_folder,
    get_agent_by_tool_name,
    get_supervisor_tools,
    list_agents,
    is_initialized as is_agents_initialized,
    reset_cache as reset_agent_cache,
)

from .schema_discovery import (
    discover_agent_schemas,
    get_agent_schema,
    get_schema_for_agent,
    get_schema_json,
    list_agent_schemas,
    is_initialized as is_schemas_initialized,
    reset_cache as reset_schema_cache,
)

__all__ = [
    # Agent loader
    "AgentDefinition",
    "SupervisorRouting",
    "ModelConfig",
    "FrontendConfig",
    "load_agent_definitions",
    "get_agent_definition",
    "get_agent_by_folder",
    "get_agent_by_tool_name",
    "get_supervisor_tools",
    "list_agents",
    "is_agents_initialized",
    "reset_agent_cache",
    # Schema discovery
    "discover_agent_schemas",
    "get_agent_schema",
    "get_schema_for_agent",
    "get_schema_json",
    "list_agent_schemas",
    "is_schemas_initialized",
    "reset_schema_cache",
]
