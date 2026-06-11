"""Runtime tool wrapper for read-only agent contract lookup."""

from __future__ import annotations

from typing import Optional

from agents import function_tool

from src.lib.agent_contracts import get_agent_contract as _get_agent_contract


@function_tool(
    name_override="get_agent_contract",
    description_override=(
        "Read deterministic runtime contract metadata for an agent. "
        "Use when schema, tool, domain-envelope, validator-binding, ontology, "
        "or field details are needed beyond the compact prompt. When an output "
        "schema has many fields, pass field_limit (and the field_cursor a "
        "previous call returned) to page through the schema field list."
    ),
)
def get_agent_contract(
    agent_id: str,
    topic: str,
    field_path: Optional[str] = None,
    detail_level: str = "summary",
    field_limit: Optional[int] = None,
    field_cursor: Optional[str] = None,
) -> dict:
    """Return read-only structured contract details for an agent."""

    return _get_agent_contract(
        agent_id=agent_id,
        topic=topic,
        field_path=field_path,
        detail_level=detail_level,
        field_limit=field_limit,
        field_cursor=field_cursor,
    )


__all__ = ["get_agent_contract"]
