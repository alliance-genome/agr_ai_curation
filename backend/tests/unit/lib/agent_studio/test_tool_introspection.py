"""Tests for tool introspection utility."""
import pytest
from agents import function_tool
from pydantic import BaseModel

from src.lib.agent_studio.tool_introspection import (
    introspect_tool,
    ToolMetadata,
)


class SampleInput(BaseModel):
    """Sample input for testing."""
    query: str
    limit: int = 10


@function_tool
def sample_tool(query: str, limit: int = 10) -> str:
    """Search for something.

    Args:
        query: The search query
        limit: Maximum results
    """
    return f"Found {limit} results for {query}"


def test_introspect_tool_returns_metadata():
    """Should return ToolMetadata instance."""
    metadata = introspect_tool(sample_tool)
    assert isinstance(metadata, ToolMetadata)


def test_introspect_tool_extracts_name():
    """Should extract function name."""
    metadata = introspect_tool(sample_tool)
    assert metadata.name == "sample_tool"


def test_introspect_tool_extracts_description():
    """Should extract docstring as description."""
    metadata = introspect_tool(sample_tool)
    assert "Search for something" in metadata.description


def test_introspect_tool_extracts_parameters():
    """Should extract parameter info."""
    metadata = introspect_tool(sample_tool)
    assert "query" in metadata.parameters
    assert metadata.parameters["query"]["type"] == "string"
