"""Unit tests for shared specialist tool-call policy helpers."""

from __future__ import annotations

from src.lib.openai_agents.tool_call_policy import (
    DOCUMENT_REQUIRED_TOOL_NAMES,
    required_tool_names_for_available_tools,
)


def test_document_tools_take_precedence_over_package_required_tools():
    required = required_tool_names_for_available_tools(
        {"search_document", "agr_curation_query"},
        required_package_tool_names_resolver=lambda _tools: {"agr_curation_query"},
    )

    assert required == DOCUMENT_REQUIRED_TOOL_NAMES


def test_package_required_tools_apply_without_document_tools():
    required = required_tool_names_for_available_tools(
        {"agr_curation_query", "get_agent_contract"},
        required_package_tool_names_resolver=lambda tools: {
            tool for tool in tools if tool == "agr_curation_query"
        },
    )

    assert required == frozenset({"agr_curation_query"})


def test_empty_tool_set_has_no_required_policy():
    assert required_tool_names_for_available_tools([]) == frozenset()
