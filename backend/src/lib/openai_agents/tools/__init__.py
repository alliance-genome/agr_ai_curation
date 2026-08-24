"""
Tools for OpenAI Agents SDK.

These tools wrap existing functionality to be used with the OpenAI Agents SDK.
"""

from .sql_query import create_sql_query_tool
from .rest_api import create_rest_api_tool

__all__ = [
    "create_sql_query_tool",
    "create_rest_api_tool",
]
