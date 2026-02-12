"""
Tool Registry for managing diagnostic tools in Prompt Explorer.

Provides centralized management of tools with:
- Tool registration and discovery
- Anthropic format conversion
- Handler dispatch
- Tool categorization for organization at scale
"""

import logging
from typing import Dict, List, Callable, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """Definition of a diagnostic tool."""
    name: str
    description: str
    input_schema: dict
    handler: Callable
    category: str = "general"  # For organization: "database", "api", "system", etc.
    requires_auth: bool = False  # Future: role-based access control
    tags: List[str] = field(default_factory=list)  # For filtering/search


class DiagnosticToolRegistry:
    """
    Registry for managing diagnostic tools available to Opus.

    Designed to scale to 100+ tools with:
    - Categorization for organization
    - Tags for filtering
    - Efficient lookup by name
    - Bulk retrieval in Anthropic format
    """

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._categories: Dict[str, List[str]] = {}  # category -> [tool_names]
        self._initialized = False
        logger.info("Initialized DiagnosticToolRegistry")

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable,
        category: str = "general",
        requires_auth: bool = False,
        tags: Optional[List[str]] = None
    ) -> None:
        """
        Register a new diagnostic tool.

        Args:
            name: Unique tool identifier
            description: Tool description for LLM (shown in tool schema)
            input_schema: JSON Schema for tool inputs
            handler: Callable that executes the tool
            category: Category for organization (database, api, system, etc.)
            requires_auth: Whether tool requires authentication
            tags: Optional tags for filtering/search
        """
        if name in self._tools:
            logger.warning("Tool '%s' already registered, overwriting", name)
            # Remove from old category
            old_tool = self._tools[name]
            if old_tool.category in self._categories:
                self._categories[old_tool.category] = [
                    n for n in self._categories[old_tool.category] if n != name
                ]

        tool_def = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            category=category,
            requires_auth=requires_auth,
            tags=tags or []
        )

        self._tools[name] = tool_def

        # Track by category
        if category not in self._categories:
            self._categories[category] = []
        self._categories[category].append(name)

        logger.debug('Registered diagnostic tool: %s (category: %s)', name, category)

    def unregister(self, name: str) -> bool:
        """
        Remove a tool from the registry.

        Returns:
            True if tool was removed, False if not found
        """
        if name not in self._tools:
            return False

        tool = self._tools.pop(name)
        if tool.category in self._categories:
            self._categories[tool.category] = [
                n for n in self._categories[tool.category] if n != name
            ]

        logger.info('Unregistered diagnostic tool: %s', name)
        return True

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """Get a tool definition by name."""
        return self._tools.get(name)

    def get_all_tools(self) -> List[ToolDefinition]:
        """Get all registered tools."""
        return list(self._tools.values())

    def get_anthropic_tools(self) -> List[dict]:
        """
        Get all tools in Anthropic format.

        Returns:
            List of dicts with keys: name, description, input_schema
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema
            }
            for tool in self._tools.values()
        ]

    def get_tools_by_category(self, category: str) -> List[ToolDefinition]:
        """Get all tools in a specific category."""
        tool_names = self._categories.get(category, [])
        return [self._tools[name] for name in tool_names if name in self._tools]

    def get_categories(self) -> List[str]:
        """Get all categories with at least one tool."""
        return [cat for cat, tools in self._categories.items() if tools]

    def get_tools_by_tag(self, tag: str) -> List[ToolDefinition]:
        """Get all tools with a specific tag."""
        return [tool for tool in self._tools.values() if tag in tool.tags]

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_tool_count(self) -> int:
        """Get the total number of registered tools."""
        return len(self._tools)

    def get_tools_summary(self) -> Dict[str, Any]:
        """
        Get a summary of all registered tools.

        Useful for debugging and documentation generation.
        """
        return {
            "total_tools": len(self._tools),
            "categories": {
                cat: len(tools) for cat, tools in self._categories.items() if tools
            },
            "tools": [
                {
                    "name": tool.name,
                    "category": tool.category,
                    "tags": tool.tags,
                    "requires_auth": tool.requires_auth
                }
                for tool in self._tools.values()
            ]
        }


# Singleton instance
_registry_instance: Optional[DiagnosticToolRegistry] = None


def get_diagnostic_tools_registry() -> DiagnosticToolRegistry:
    """
    Get the singleton diagnostic tools registry.

    On first access, initializes the registry and registers all tools.
    """
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = DiagnosticToolRegistry()
        # Auto-register all tools on first access
        from . import tool_definitions
        tool_definitions.register_all_tools(_registry_instance)
        _registry_instance._initialized = True
        logger.info('Diagnostic tools registry initialized with %s tools', _registry_instance.get_tool_count())
    return _registry_instance


def reset_registry() -> None:
    """
    Reset the singleton registry.

    Primarily used for testing.
    """
    global _registry_instance
    _registry_instance = None
