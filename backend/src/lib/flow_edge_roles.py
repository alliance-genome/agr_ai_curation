"""Shared edge-role and formatter-binding contract for persisted flows."""

from collections.abc import Mapping
from typing import Any, Literal


CONTROL_FLOW_EDGE_ROLE = "control_flow"
OUTPUT_ATTACHMENT_EDGE_ROLE = "output_attachment"
VALIDATION_ATTACHMENT_EDGE_ROLE = "validation_attachment"
SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS = frozenset(
    {
        "chat_output",
        "chat_output_formatter",
        "csv_formatter",
        "tsv_formatter",
        "json_formatter",
    }
)
FlowEdgeRole = Literal[
    "control_flow",
    "output_attachment",
    "validation_attachment",
]


def agent_can_source_output_attachment(
    entry: Mapping[str, Any] | None,
) -> bool:
    """Return whether an active visible agent emits formatter-ready structure.

    Agent lookup is responsible for activity and curator visibility. This
    predicate deliberately permits only extraction agents, plus Validation
    agents that declare a concrete structured output schema. A schema alone
    never upgrades a Custom/general agent into a formatter source.
    """

    if not isinstance(entry, Mapping):
        return False
    if entry.get("is_active") is False or entry.get("visible") is False:
        return False
    if "produces_flow_artifacts" in entry:
        return entry.get("produces_flow_artifacts") is True

    category = str(entry.get("category") or "").strip().lower()
    subcategory = str(entry.get("subcategory") or "").strip().lower()
    if "extract" in category or "extract" in subcategory:
        return True

    is_validation = "validation" in category
    output_schema_key = str(
        entry.get("output_schema_key") or entry.get("output_schema") or ""
    ).strip()
    return is_validation and bool(output_schema_key)


__all__ = [
    "CONTROL_FLOW_EDGE_ROLE",
    "agent_can_source_output_attachment",
    "FlowEdgeRole",
    "OUTPUT_ATTACHMENT_EDGE_ROLE",
    "SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS",
    "VALIDATION_ATTACHMENT_EDGE_ROLE",
]
