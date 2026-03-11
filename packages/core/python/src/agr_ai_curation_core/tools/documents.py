"""Context-driven document tool factories for the AGR core package."""

from __future__ import annotations

from typing import Any

from .weaviate_search import (
    create_read_section_tool as _create_read_section_tool,
    create_read_subsection_tool as _create_read_subsection_tool,
    create_search_tool as _create_search_tool,
)


def _require_context_value(context: dict[str, Any], key: str) -> str:
    value = context.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required context value '{key}'")
    return value


def create_search_document_tool(context: dict[str, Any]):
    """Create the package-exported search_document tool."""
    return _create_search_tool(
        document_id=_require_context_value(context, "document_id"),
        user_id=_require_context_value(context, "user_id"),
    )


def create_read_section_tool(context: dict[str, Any]):
    """Create the package-exported read_section tool."""
    return _create_read_section_tool(
        document_id=_require_context_value(context, "document_id"),
        user_id=_require_context_value(context, "user_id"),
    )


def create_read_subsection_tool(context: dict[str, Any]):
    """Create the package-exported read_subsection tool."""
    return _create_read_subsection_tool(
        document_id=_require_context_value(context, "document_id"),
        user_id=_require_context_value(context, "user_id"),
    )


__all__ = [
    "create_read_section_tool",
    "create_read_subsection_tool",
    "create_search_document_tool",
]
