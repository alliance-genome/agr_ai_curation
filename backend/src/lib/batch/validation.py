"""Flow validation for batch compatibility."""

from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
from src.lib.executable_flow_graph import (
    ExecutableFlowTopologyError,
    project_executable_flow_graph,
)
from src.schemas.batch import BatchValidationResponse


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
    2. All exit nodes must have file_output or curation_handoff capability (not chat_output)
    """
    try:
        projection = project_executable_flow_graph(flow_definition)
    except ExecutableFlowTopologyError as exc:
        return BatchValidationResponse(valid=False, errors=[str(exc)])

    errors = []
    control_node_ids = set(projection.control_node_ids)

    # Check flow contains at least one PDF extraction agent
    has_pdf_agent = False
    for node in flow_definition.get("nodes", []):
        if node.get("id") not in control_node_ids:
            continue
        agent_id = node.get("data", {}).get("agent_id")
        if agent_id and has_batch_capability(agent_id, "pdf_extraction"):
            has_pdf_agent = True
            break

    if not has_pdf_agent:
        errors.append("Flow must contain a PDF extraction agent (PDF Specialist or Gene Expression Extractor)")

    # Check exit nodes have file output (not chat)
    for node_id in projection.terminal_node_ids:
        agent_id = get_node_agent_id(flow_definition, node_id)
        if agent_id:
            if has_batch_capability(agent_id, "chat_output"):
                errors.append("Flow ends with Chat Output - batch requires file output or curation handoff")
            elif not (
                has_batch_capability(agent_id, "file_output")
                or has_batch_capability(agent_id, "curation_handoff")
            ):
                errors.append(
                    "Flow must end with a file output agent (CSV, TSV, or JSON Formatter) "
                    "or the Curation Handoff agent"
                )

    return BatchValidationResponse(valid=len(errors) == 0, errors=errors)
