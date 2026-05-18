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
        "or field details are needed beyond the compact prompt."
    ),
)
def get_agent_contract(
    agent_id: str,
    topic: str,
    field_path: Optional[str] = None,
    detail_level: str = "summary",
) -> dict:
    """Return read-only structured contract details for an agent."""

    return _get_agent_contract(
        agent_id=agent_id,
        topic=topic,
        field_path=field_path,
        detail_level=detail_level,
    )


__all__ = ["get_agent_contract"]
