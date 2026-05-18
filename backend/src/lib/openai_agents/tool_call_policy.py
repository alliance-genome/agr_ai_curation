"""Shared runtime tool-call policy helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

DOCUMENT_REQUIRED_TOOL_NAMES = frozenset(
    {"search_document", "read_section", "read_subsection"}
)


def required_tool_names_for_available_tools(
    available_tool_names: Iterable[str],
    *,
    required_package_tool_names_resolver: Callable[[set[str]], set[str] | frozenset[str]]
    | None = None,
) -> frozenset[str]:
    """Return the enforced required-tool set for an agent's available tools."""

    available: set[str] = set()
    for tool_name in available_tool_names:
        if not isinstance(tool_name, str):
            raise TypeError("available tool names must be strings")
        normalized = tool_name.strip()
        if normalized:
            available.add(normalized)
    if not available:
        return frozenset()

    if available & DOCUMENT_REQUIRED_TOOL_NAMES:
        return DOCUMENT_REQUIRED_TOOL_NAMES

    if required_package_tool_names_resolver is None:
        return frozenset()

    return frozenset(required_package_tool_names_resolver(available))


def required_package_tool_names_from_metadata(
    available_tool_names: set[str],
    metadata_by_tool_name: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """Return package tools whose metadata enforces a required tool call."""

    required: set[str] = set()
    for tool_name in available_tool_names:
        required_call = metadata_by_tool_name.get(tool_name, {}).get("required_tool_call")
        if isinstance(required_call, Mapping) and bool(required_call.get("enforce")):
            required.add(tool_name)
    return required


__all__ = [
    "DOCUMENT_REQUIRED_TOOL_NAMES",
    "required_package_tool_names_from_metadata",
    "required_tool_names_for_available_tools",
]
