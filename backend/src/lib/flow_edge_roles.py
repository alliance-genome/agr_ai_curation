"""Shared edge-role contract for persisted flow definitions."""

from typing import Literal


CONTROL_FLOW_EDGE_ROLE = "control_flow"
VALIDATION_ATTACHMENT_EDGE_ROLE = "validation_attachment"
FlowEdgeRole = Literal["control_flow", "validation_attachment"]


__all__ = [
    "CONTROL_FLOW_EDGE_ROLE",
    "FlowEdgeRole",
    "VALIDATION_ATTACHMENT_EDGE_ROLE",
]
