"""Shared edge-role contract for persisted flow definitions."""

from typing import Literal


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


__all__ = [
    "CONTROL_FLOW_EDGE_ROLE",
    "FlowEdgeRole",
    "OUTPUT_ATTACHMENT_EDGE_ROLE",
    "SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS",
    "VALIDATION_ATTACHMENT_EDGE_ROLE",
]
