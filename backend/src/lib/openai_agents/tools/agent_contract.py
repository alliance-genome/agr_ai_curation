"""Runtime tool wrapper for read-only agent contract lookup."""

from __future__ import annotations

from typing import Optional

from agents import function_tool

from src.lib.agent_contracts import get_agent_contract as _get_agent_contract


@function_tool(
    name_override="get_agent_contract",
    description_override=(
        "Read deterministic runtime contract metadata for an agent (topics: "
        "tools, output_schema, domain_envelope, validator_bindings, "
        "ontology_constraints, field). Results are scoped and paged: start with "
        "the default summary, and to look at one field pass field_path (add "
        "domain_pack_id or object_type when the field exists in several packs) "
        "before asking for detail_level=detail. An unknown field or selector "
        "returns an error, never the whole registry. When page.truncated is "
        "true, pass page.next_cursor unchanged as cursor to read the next page. "
        "An item with detail_complete=false lists omitted_values; repeat the "
        "same arguments with item_ref=<item ref> and detail_pointer to read one "
        "exactly."
    ),
)
def get_agent_contract(
    agent_id: str,
    topic: str,
    field_path: Optional[str] = None,
    detail_level: str = "summary",
    limit: Optional[int] = None,
    cursor: Optional[str] = None,
    domain_pack_id: Optional[str] = None,
    object_type: Optional[str] = None,
    item_ref: Optional[str] = None,
    detail_pointer: Optional[str] = None,
) -> dict:
    """Return one scoped, bounded page of read-only contract details for an agent."""

    return _get_agent_contract(
        agent_id=agent_id,
        topic=topic,
        field_path=field_path,
        detail_level=detail_level,
        limit=limit,
        cursor=cursor,
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        item_ref=item_ref,
        detail_pointer=detail_pointer,
    )


__all__ = ["get_agent_contract"]
