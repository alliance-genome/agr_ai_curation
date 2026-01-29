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
    list_agents,
)

from .schema_discovery import (
    discover_agent_schemas,
    get_agent_schema,
    list_agent_schemas,
)

__all__ = [
    # Agent loader
    "AgentDefinition",
    "SupervisorRouting",
    "ModelConfig",
    "FrontendConfig",
    "load_agent_definitions",
    "get_agent_definition",
    "list_agents",
    # Schema discovery
    "discover_agent_schemas",
    "get_agent_schema",
    "list_agent_schemas",
]
