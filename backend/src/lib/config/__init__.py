"""
Config-driven architecture loaders.

This package provides dynamic discovery and loading of:
- Agent definitions from runtime packages plus `config/agents/*/agent.yaml` overrides
- Prompts from runtime packages plus `config/agents/*/prompt.yaml` overrides
- Schemas from runtime packages plus `config/agents/*/schema.py` overrides
- Group rules from runtime packages plus `config/agents/*/group_rules/*.yaml` overrides
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
    get_group_for_provider_group,
    get_groups_for_provider_groups,
    get_provider_to_group_mapping,
    get_group_claim_key,
    get_identity_provider_type,
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

from .models_loader import (
    ModelDefinition,
    load_models,
    get_model,
    get_default_model,
    list_models as list_model_definitions,
    is_initialized as is_models_initialized,
    reset_cache as reset_models_cache,
)

from .providers_loader import (
    ProviderDefinition,
    load_providers,
    get_provider,
    get_default_runner_provider,
    list_providers as list_provider_definitions,
    is_initialized as is_providers_initialized,
    reset_cache as reset_providers_cache,
)

from .tool_policy_defaults_loader import (
    ToolPolicyDefault,
    load_tool_policy_defaults,
)

from .provider_validation import (
    get_provider_validation_strict_mode,
    build_provider_runtime_report,
    validate_provider_runtime_contracts,
    validate_and_cache_provider_runtime_contracts,
    get_startup_provider_validation_report,
    reset_startup_provider_validation_report,
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
    "get_group_for_provider_group",
    "get_groups_for_provider_groups",
    "get_provider_to_group_mapping",
    "get_group_claim_key",
    "get_identity_provider_type",
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
    # Models loader
    "ModelDefinition",
    "load_models",
    "get_model",
    "get_default_model",
    "list_model_definitions",
    "is_models_initialized",
    "reset_models_cache",
    # Providers loader
    "ProviderDefinition",
    "load_providers",
    "get_provider",
    "get_default_runner_provider",
    "list_provider_definitions",
    "is_providers_initialized",
    "reset_providers_cache",
    # Tool policy defaults loader
    "ToolPolicyDefault",
    "load_tool_policy_defaults",
    # Provider validation
    "get_provider_validation_strict_mode",
    "build_provider_runtime_report",
    "validate_provider_runtime_contracts",
    "validate_and_cache_provider_runtime_contracts",
    "get_startup_provider_validation_report",
    "reset_startup_provider_validation_report",
]
