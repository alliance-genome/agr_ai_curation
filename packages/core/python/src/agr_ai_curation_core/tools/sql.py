"""Context-driven SQL tool factory for the AGR core package."""

from __future__ import annotations

from typing import Any

from .sql_query import create_sql_query_tool


def create_curation_db_sql_tool(context: dict[str, Any]):
    """Create the package-exported curation_db_sql tool."""
    database_url = context.get("database_url")
    if not isinstance(database_url, str) or not database_url:
        raise ValueError("Missing required context value 'database_url'")
    return create_sql_query_tool(database_url, tool_name="curation_db_sql")


__all__ = ["create_curation_db_sql_tool"]
