"""Authorized capture of a saved flow's exact executable definition."""

from uuid import UUID

from sqlalchemy.orm import Session

from src.lib.flows.access import get_visible_flow
from src.lib.flows.execution_revisions import resolve_flow_execution_revisions
from src.lib.flows.validation_attachments import apply_flow_validation_attachment_defaults
from src.schemas.flows import FlowDefinition

from .execution_context import BenchmarkCuratorContext
from .frozen_flow import FrozenBenchmarkFlow
from .saved_flows import flow_summary


def capture_recipe_flow(session, curator, recipe: dict, definition: FlowDefinition) -> FrozenBenchmarkFlow:
    """Capture a hydrated installed recipe using normal revision resolution."""
    from .suites import _digest

    resolved = resolve_flow_execution_revisions(
        session, definition, user_id=curator.db_user_id, active_group_ids=list(curator.active_groups),
    )
    if any(finding.severity == "error" for finding in resolved.findings):
        raise ValueError("Selected recipe contains an unavailable executable revision")
    executable = apply_flow_validation_attachment_defaults(
        resolved.definition, entries_by_node=resolved.entries_by_node,
    )
    from src.lib.agent_studio.catalog_service import get_active_visible_agent_metadata
    from .flow_contracts import discover_output_contract

    contracts = {}
    for node in executable.nodes:
        if node.type == "task_input":
            continue
        metadata = resolved.entries_by_node.get(node.id)
        if metadata is None:
            metadata = get_active_visible_agent_metadata(
                node.data.agent_id, db_user_id=curator.db_user_id,
                authenticated_groups=list(curator.active_groups),
            )
        if metadata is None:
            raise ValueError("Selected recipe agent is unavailable")
        contract = discover_output_contract(session, curator, agent_id=node.data.agent_id, metadata=metadata)
        contracts[node.id] = {
            "node_id": node.id, "title": node.data.agent_display_name, "output_key": node.data.output_key,
            "contract": contract.model_dump(mode="json"),
        }
    return FrozenBenchmarkFlow(
        source_kind="recipe", source_id=recipe["name"], source_revision=_digest(recipe),
        title=recipe["name"], description=recipe["description"],
        definition=executable.model_dump(mode="json"),
        output_contracts=contracts,
    )


def capture_saved_flow(
    session: Session, curator: BenchmarkCuratorContext, flow_id: UUID, *, expected_revision: str,
) -> FrozenBenchmarkFlow:
    """Freeze only after source visibility and every custom node pin resolve.

    This function does not persist, run models, or certify package dependencies.
    The full admission boundary must capture/check those alongside this object.
    """
    row = get_visible_flow(session, flow_id, curator.db_user_id)
    summary = flow_summary(row)
    if summary.revision != expected_revision:
        raise ValueError("Saved flow changed; refresh the selected revision")
    resolved = resolve_flow_execution_revisions(
        session, FlowDefinition.model_validate(row.flow_definition), user_id=curator.db_user_id,
        active_group_ids=list(curator.active_groups),
    )
    if any(finding.severity == "error" for finding in resolved.findings):
        raise ValueError("Selected flow contains an unavailable executable revision")
    definition = apply_flow_validation_attachment_defaults(
        resolved.definition, entries_by_node=resolved.entries_by_node,
    )
    return FrozenBenchmarkFlow(
        source_kind="saved_flow", source_id=str(row.id), source_revision=summary.revision,
        title=summary.title, description=summary.description,
        definition=definition.model_dump(mode="json"),
    )
