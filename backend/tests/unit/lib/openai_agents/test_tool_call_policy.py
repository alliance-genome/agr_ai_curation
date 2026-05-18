"""Unit tests for shared specialist tool-call policy helpers."""

from __future__ import annotations

from src.lib.openai_agents.tool_call_policy import (
    DOCUMENT_REQUIRED_TOOL_NAMES,
    required_package_tool_names_from_metadata,
    required_tool_names_for_available_tools,
)


def test_document_tools_take_precedence_over_package_required_tools():
    required = required_tool_names_for_available_tools(
        {"search_document", "tool_alpha"},
        required_package_tool_names_resolver=lambda _tools: {"tool_alpha"},
    )

    assert required == DOCUMENT_REQUIRED_TOOL_NAMES


def test_package_required_tools_apply_without_document_tools():
    required = required_tool_names_for_available_tools(
        {"tool_alpha", "tool_beta"},
        required_package_tool_names_resolver=lambda tools: {
            tool for tool in tools if tool == "tool_alpha"
        },
    )

    assert required == frozenset({"tool_alpha"})


def test_empty_tool_set_has_no_required_policy():
    assert required_tool_names_for_available_tools([]) == frozenset()


def test_available_tool_names_must_be_strings():
    try:
        required_tool_names_for_available_tools(["search_document", None])  # type: ignore[list-item]
    except TypeError as exc:
        assert "must be strings" in str(exc)
    else:
        raise AssertionError("Expected non-string tool names to raise TypeError")


def test_required_package_tool_names_are_derived_from_metadata():
    required = required_package_tool_names_from_metadata(
        {"tool_alpha", "tool_beta"},
        {
            "tool_alpha": {"required_tool_call": {"enforce": True}},
            "tool_beta": {"required_tool_call": {"enforce": False}},
        },
    )

    assert required == {"tool_alpha"}
