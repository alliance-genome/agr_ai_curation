"""
Diagnostic Tools for Agent Studio.

This module provides diagnostic tools for Opus to troubleshoot
trace issues and validate agent behavior.

Usage:
    from src.lib.agent_studio.diagnostic_tools import (
        get_diagnostic_tools_registry,
        DiagnosticToolRegistry,
    )

    # Get singleton registry (auto-initializes with all tools)
    registry = get_diagnostic_tools_registry()

    # Get tools in Anthropic format for Claude API
    anthropic_tools = registry.get_anthropic_tools()

    # Execute a package-registered diagnostic tool by name
    tool = registry.get_tool("package_lookup_tool")
    result = tool.handler(method="lookup", query="example")
"""

from .registry import (
    DiagnosticToolRegistry,
    ToolDefinition,
    get_diagnostic_tools_registry,
    reset_registry,
)

__all__ = [
    "DiagnosticToolRegistry",
    "ToolDefinition",
    "get_diagnostic_tools_registry",
    "reset_registry",
]
