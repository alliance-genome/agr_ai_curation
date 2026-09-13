"""Authorized, side-effect-free saved-flow discovery for benchmark preparation.

Discovery receipts describe a mutable saved flow at read time. They are not
executable snapshots; the execution-freeze boundary must reauthorize selection.
"""

from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from src.lib.agent_studio.catalog_service import get_active_visible_agent_metadata
from src.lib.flows.access import get_visible_flow, visible_flow_filter
from src.lib.flows.execution_revisions import resolve_flow_execution_revisions
from src.models.sql.curation_flow import CurationFlow
from src.schemas.flows import FlowDefinition

from .execution_context import BenchmarkCuratorContext
from .flow_contracts import BenchmarkFlowOutputContract, discover_output_contract
from .flow_stages import BenchmarkFlowStage, authorize_scheduled_validator, flow_stages
from .flow_model_defaults import stage_model_defaults
from .models import FrozenStrictModel
from .suites import _digest


class BenchmarkSavedFlowSummary(FrozenStrictModel):
    source_kind: Literal["saved_flow"] = "saved_flow"
    flow_id: UUID
    title: str
    description: str | None
    revision: str


class BenchmarkSavedFlowNodeContract(FrozenStrictModel):
    node_id: str
    title: str
    output_key: str
    contract: BenchmarkFlowOutputContract


class BenchmarkSavedFlowContracts(FrozenStrictModel):
    flow: BenchmarkSavedFlowSummary
    nodes: tuple[BenchmarkSavedFlowNodeContract, ...]
    status: Literal["verified", "not_verified"]
    reason: str | None = None
    stages: tuple[BenchmarkFlowStage, ...] = ()
    route_default_conflicts: tuple[str, ...] = ()


class BenchmarkSavedFlowPage(FrozenStrictModel):
    items: tuple[BenchmarkSavedFlowSummary, ...]
    total_items: int
    next_offset: int | None = None


def flow_summary(flow: CurationFlow) -> BenchmarkSavedFlowSummary:
    return BenchmarkSavedFlowSummary(
        flow_id=flow.id, title=flow.name, description=flow.description,
        revision=_digest({
            "flow_id": str(flow.id), "name": flow.name,
            "description": flow.description, "definition": flow.flow_definition,
        }),
    )


def visible_saved_flows(session: Session, curator: BenchmarkCuratorContext):
    """Return a query so API pagination bounds database reads before hydration."""
    return session.query(CurationFlow).filter(
        CurationFlow.is_active.is_(True), visible_flow_filter(curator.db_user_id),
    ).order_by(CurationFlow.name, CurationFlow.id)


def saved_flow_contracts(
    session: Session, curator: BenchmarkCuratorContext, flow_id: UUID,
    *, expected_revision: str | None = None,
) -> BenchmarkSavedFlowContracts:
    """Reauthorize detail/selection before reading any node or output schema.

    A shared flow is not permission to inspect its owner's private agents or
    profiles. Failure to resolve any node returns no partial contract payload.
    Callers translate access failures into their standard sanitized API errors.
    """
    flow = get_visible_flow(session, flow_id, curator.db_user_id)
    summary = flow_summary(flow)
    if expected_revision is not None and expected_revision != summary.revision:
        raise ValueError("Saved flow changed; refresh flow discovery before selecting it")
    try:
        definition = FlowDefinition.model_validate(flow.flow_definition)
        resolved = resolve_flow_execution_revisions(
            session, definition, user_id=curator.db_user_id,
            active_group_ids=list(curator.active_groups),
        )
        if any(finding.severity == "error" for finding in resolved.findings):
            raise ValueError("Unavailable flow node revision")
        nodes = []
        entries = {}
        for node in resolved.definition.nodes:
            if node.type == "task_input":
                continue
            metadata = (
                resolved.entries_by_node[node.id]
                if node.id in resolved.entries_by_node else
                get_active_visible_agent_metadata(
                    node.data.agent_id, db_user_id=curator.db_user_id,
                    authenticated_groups=list(curator.active_groups),
                )
            )
            if metadata is None:
                raise ValueError("Unavailable flow agent")
            entries[node.id] = metadata
            contract = discover_output_contract(
                session, curator, agent_id=node.data.agent_id, metadata=metadata,
            )
            nodes.append(BenchmarkSavedFlowNodeContract(
                node_id=node.id, title=node.data.agent_display_name,
                output_key=node.data.output_key, contract=contract,
            ))
        stages = flow_stages(
            resolved.definition, entries,
            authorize_validator=lambda node_id, binding: authorize_scheduled_validator(
                session, curator, entries[node_id], binding,
            ),
        )
        stages, route_conflicts = stage_model_defaults(session, curator, stages)
    except ValueError:
        return BenchmarkSavedFlowContracts(
            flow=summary, nodes=(), status="not_verified",
            reason="A flow node or its saved output structure is unavailable. Open the flow in AI Curation and verify its agents and revisions.",
        )
    verified = bool(nodes) and all(node.contract.status == "verified" for node in nodes)
    return BenchmarkSavedFlowContracts(
        flow=summary, nodes=tuple(nodes), stages=stages, status="verified" if verified else "not_verified",
        route_default_conflicts=route_conflicts,
        reason=None if verified else "One or more steps have no declared output structure; select a verified source for mapping.",
    )
