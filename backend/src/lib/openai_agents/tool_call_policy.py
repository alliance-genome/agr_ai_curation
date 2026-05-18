"""Shared runtime tool-call policy helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable

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

    available = {
        str(tool_name or "").strip()
        for tool_name in available_tool_names
        if str(tool_name or "").strip()
    }
    if not available:
        return frozenset()

    if available & DOCUMENT_REQUIRED_TOOL_NAMES:
        return DOCUMENT_REQUIRED_TOOL_NAMES

    if required_package_tool_names_resolver is None:
        return frozenset()

    return frozenset(required_package_tool_names_resolver(available))


__all__ = [
    "DOCUMENT_REQUIRED_TOOL_NAMES",
    "required_tool_names_for_available_tools",
]
