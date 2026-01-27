"""
Registry Type Schemas for Agent System.

Defines the canonical types for agent registry entries. These types are used
by AGENT_REGISTRY and provide type safety and validation.

Note: AgentRegistryEntry is distinct from openai_agents/config.py:AgentConfig.
      AgentRegistryEntry = static registry metadata (what agents ARE)
      AgentConfig = resolved runtime configuration (how agents RUN)

Usage:
    from src.lib.agent_studio.registry_types import AgentRegistryEntry, BatchingConfig

    agent = AgentRegistryEntry(
        name="Gene Validation Agent",
        description="Validates gene identifiers",
        category="Validation",
        factory=create_gene_agent,
        tools=["agr_curation_query"],
    )
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ValidationResult:
    """Result of a validation check.

    Used by validation functions to report success/failure with details.

    Attributes:
        passed: Whether the validation passed
        errors: List of error messages (cause failure)
        warnings: List of warning messages (informational)
    """
    passed: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self):
        # Auto-compute passed based on errors if not explicitly set
        if self.errors:
            self.passed = False


@dataclass
class BatchingConfig:
    """Configuration for batch processing hints.

    When the supervisor detects repeated individual calls to a specialist,
    it can inject a reminder to batch requests.

    Attributes:
        entity: The type of entity being batched (e.g., "genes", "alleles")
        example: Example of proper batched call syntax
    """
    entity: str
    example: str


@dataclass
class FrontendMetadata:
    """Configuration for frontend display.

    Defines how the agent appears in the Flow Builder UI.

    Attributes:
        icon: Emoji or icon identifier for the agent
        color: Optional hex color for theming
        show_in_palette: Whether to show in the agent palette (False for system agents)
    """
    icon: str = "✨"  # Default sparkle icon
    color: Optional[str] = None
    show_in_palette: bool = True


@dataclass
class SupervisorMetadata:
    """Configuration for supervisor routing.

    Defines how the supervisor presents this agent as a tool.

    Attributes:
        enabled: Whether this agent appears as a supervisor tool (default True)
        tool_name: The function_tool name (e.g., "ask_gene_specialist")
        tool_description: Description shown to the LLM for routing decisions
    """
    enabled: bool = True
    tool_name: Optional[str] = None
    tool_description: Optional[str] = None


@dataclass
class AgentRegistryEntry:
    """Complete configuration for an agent in the registry.

    This is the canonical type for agent definitions. All agent metadata
    should conform to this schema.

    Attributes:
        name: Human-readable display name
        description: Short description of what the agent does
        category: High-level category (Extraction, Validation, Output, etc.)
        subcategory: Optional subcategory for grouping
        factory: Factory function to create the agent, or None for non-executable entries
        tools: List of tool IDs this agent uses
        requires_document: Whether the agent needs a document context
        required_params: Parameters required by the factory function
        batch_capabilities: Special capabilities for batching (e.g., "pdf_extraction")
        has_mod_rules: Whether this agent has MOD-specific prompt rules
        config_defaults: Default config values (reasoning, temperature, etc.)
        batching: Optional batching configuration for supervisor hints
        supervisor: Optional supervisor routing metadata
        frontend: Optional frontend display metadata
        documentation: Optional documentation dict (legacy format)
    """
    name: str
    description: str
    category: str
    subcategory: Optional[str] = None
    factory: Optional[Callable[..., Any]] = None
    tools: List[str] = field(default_factory=list)
    requires_document: bool = False
    required_params: List[str] = field(default_factory=list)
    batch_capabilities: List[str] = field(default_factory=list)
    has_mod_rules: bool = False
    config_defaults: Optional[Dict[str, Any]] = None
    batching: Optional[BatchingConfig] = None
    supervisor: Optional[SupervisorMetadata] = None
    frontend: Optional[FrontendMetadata] = None
    documentation: Optional[Dict[str, Any]] = None

    def is_executable(self) -> bool:
        """Check if this agent can be executed (has a factory)."""
        return self.factory is not None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for AGENT_REGISTRY compatibility."""
        result = {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "subcategory": self.subcategory,
            "factory": self.factory,
            "tools": self.tools,
            "requires_document": self.requires_document,
            "required_params": self.required_params,
            "batch_capabilities": self.batch_capabilities,
            "has_mod_rules": self.has_mod_rules,
        }
        if self.config_defaults:
            result["config_defaults"] = self.config_defaults
        if self.batching:
            result["batching"] = {
                "entity": self.batching.entity,
                "example": self.batching.example,
            }
        if self.supervisor:
            result["supervisor"] = {
                "enabled": self.supervisor.enabled,
                "tool_name": self.supervisor.tool_name,
                "tool_description": self.supervisor.tool_description,
            }
        if self.frontend:
            result["frontend"] = {
                "icon": self.frontend.icon,
                "color": self.frontend.color,
                "show_in_palette": self.frontend.show_in_palette,
            }
        if self.documentation:
            result["documentation"] = self.documentation
        return result


def validate_registry(registry: Dict[str, Dict[str, Any]]) -> ValidationResult:
    """
    Validate a registry dictionary for consistency.

    Checks:
    - Required fields are present
    - Factory functions are callable
    - Document requirements are consistent

    Args:
        registry: Dictionary mapping agent IDs to config dicts

    Returns:
        ValidationResult with any errors found
    """
    errors: List[str] = []
    warnings: List[str] = []

    required_fields = ["name", "description", "category"]

    for agent_id, config in registry.items():
        # Check required fields
        for field_name in required_fields:
            if field_name not in config:
                errors.append(f"{agent_id}: missing required field '{field_name}'")

        # Check factory is callable if present
        factory = config.get("factory")
        if factory is not None and not callable(factory):
            errors.append(f"{agent_id}: factory must be callable, got {type(factory)}")

        # Check document consistency
        requires_doc = config.get("requires_document", False)
        required_params = config.get("required_params", [])
        if requires_doc and "document_id" not in required_params:
            warnings.append(
                f"{agent_id}: requires_document=True but 'document_id' not in required_params"
            )

    return ValidationResult(
        passed=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )


def entry_from_dict(agent_id: str, data: Dict[str, Any]) -> AgentRegistryEntry:
    """
    Create an AgentRegistryEntry from a dictionary.

    Args:
        agent_id: The agent identifier (for error messages)
        data: Dictionary with agent configuration

    Returns:
        AgentRegistryEntry instance

    Raises:
        ValueError: If required fields are missing
    """
    # Extract batching config if present
    batching = None
    if "batching" in data:
        batching_data = data["batching"]
        batching = BatchingConfig(
            entity=batching_data.get("entity", "items"),
            example=batching_data.get("example", ""),
        )

    # Extract frontend metadata if present
    frontend = None
    if "frontend" in data:
        frontend_data = data["frontend"]
        frontend = FrontendMetadata(
            icon=frontend_data.get("icon", "✨"),
            color=frontend_data.get("color"),
            show_in_palette=frontend_data.get("show_in_palette", True),
        )

    # Extract supervisor metadata if present
    supervisor = None
    if "supervisor" in data:
        supervisor_data = data["supervisor"]
        supervisor = SupervisorMetadata(
            enabled=supervisor_data.get("enabled", True),
            tool_name=supervisor_data.get("tool_name"),
            tool_description=supervisor_data.get("tool_description"),
        )

    return AgentRegistryEntry(
        name=data.get("name", agent_id),
        description=data.get("description", ""),
        category=data.get("category", "Uncategorized"),
        subcategory=data.get("subcategory"),
        factory=data.get("factory"),
        tools=data.get("tools", []),
        requires_document=data.get("requires_document", False),
        required_params=data.get("required_params", []),
        batch_capabilities=data.get("batch_capabilities", []),
        has_mod_rules=data.get("has_mod_rules", False),
        config_defaults=data.get("config_defaults"),
        batching=batching,
        supervisor=supervisor,
        frontend=frontend,
        documentation=data.get("documentation"),
    )


# Backwards-compatibility aliases (deprecated, use AgentRegistryEntry instead)
AgentConfig = AgentRegistryEntry
config_from_dict = entry_from_dict
