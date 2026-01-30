"""
Config-driven architecture loaders.

This package provides dynamic discovery and loading of:
- Agent definitions from config/agents/*/agent.yaml
- Prompts from config/agents/*/prompt.yaml
- Schemas from config/agents/*/schema.py
- Group rules from config/agents/*/group_rules/*.yaml
- Group definitions from config/groups.yaml

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

from .groups_loader import (
    GroupDefinition,
    load_groups,
    get_group,
    get_group_for_cognito_group,
    get_groups_for_cognito_groups,
    list_groups,
    get_valid_group_ids,
    get_cognito_to_group_mapping,
    is_initialized as is_groups_initialized,
    reset_cache as reset_groups_cache,
)

from .connections_loader import (
    ConnectionDefinition,
    HealthCheck,
    load_connections,
    get_connection,
    list_connections,
    get_required_connections,
    get_optional_connections,
    get_connection_status,
    update_health_status,
    is_initialized as is_connections_initialized,
    reset_cache as reset_connections_cache,
)

from .agent_factory import (
    get_agent_factory,
    get_factory_by_agent_id,
    create_agent,
    create_agent_by_id,
    list_available_factories,
    clear_factory_cache,
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
    # Groups loader
    "GroupDefinition",
    "load_groups",
    "get_group",
    "get_group_for_cognito_group",
    "get_groups_for_cognito_groups",
    "list_groups",
    "get_valid_group_ids",
    "get_cognito_to_group_mapping",
    "is_groups_initialized",
    "reset_groups_cache",
    # Connections loader
    "ConnectionDefinition",
    "HealthCheck",
    "load_connections",
    "get_connection",
    "list_connections",
    "get_required_connections",
    "get_optional_connections",
    "get_connection_status",
    "update_health_status",
    "is_connections_initialized",
    "reset_connections_cache",
    # Agent factory
    "get_agent_factory",
    "get_factory_by_agent_id",
    "create_agent",
    "create_agent_by_id",
    "list_available_factories",
    "clear_factory_cache",
]
