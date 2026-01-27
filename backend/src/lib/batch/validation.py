"""Flow validation for batch compatibility."""
from typing import Set

from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
from src.schemas.batch import BatchValidationResponse


def get_entry_nodes(flow_definition: dict) -> Set[str]:
    """Get nodes with no incoming edges (entry points)."""
    nodes = {n["id"] for n in flow_definition.get("nodes", [])}
    targets = {e["target"] for e in flow_definition.get("edges", [])}
    return nodes - targets


def get_exit_nodes(flow_definition: dict) -> Set[str]:
    """Get nodes with no outgoing edges (exit points)."""
    nodes = {n["id"] for n in flow_definition.get("nodes", [])}
    sources = {e["source"] for e in flow_definition.get("edges", [])}
    return nodes - sources


def get_node_agent_id(flow_definition: dict, node_id: str) -> str | None:
    """Get agent_id for a node."""
    for node in flow_definition.get("nodes", []):
        if node["id"] == node_id:
            return node.get("data", {}).get("agent_id")
    return None


def has_batch_capability(agent_id: str, capability: str) -> bool:
    """Check if an agent has a specific batch capability."""
    agent = AGENT_REGISTRY.get(agent_id)
    if not agent:
        return False
    return capability in agent.get("batch_capabilities", [])


def validate_flow_for_batch(flow_definition: dict) -> BatchValidationResponse:
    """Validate a flow is compatible with batch processing.

    Rules:
    1. Must contain at least one node with pdf_extraction capability
    2. All exit nodes must have file_output capability (not chat_output)
    """
    errors = []

    # Check flow contains at least one PDF extraction agent
    has_pdf_agent = False
    for node in flow_definition.get("nodes", []):
        agent_id = node.get("data", {}).get("agent_id")
        if agent_id and has_batch_capability(agent_id, "pdf_extraction"):
            has_pdf_agent = True
            break

    if not has_pdf_agent:
        errors.append("Flow must contain a PDF extraction agent (PDF Specialist or Gene Expression Extractor)")

    # Check exit nodes have file output (not chat)
    exit_nodes = get_exit_nodes(flow_definition)
    for node_id in exit_nodes:
        agent_id = get_node_agent_id(flow_definition, node_id)
        if agent_id:
            if has_batch_capability(agent_id, "chat_output"):
                errors.append("Flow ends with Chat Output - batch requires file output (CSV, TSV, or JSON)")
            elif not has_batch_capability(agent_id, "file_output"):
                errors.append("Flow must end with a file output agent (CSV, TSV, or JSON Formatter)")

    return BatchValidationResponse(valid=len(errors) == 0, errors=errors)
