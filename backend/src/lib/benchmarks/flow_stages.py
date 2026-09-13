"""Describe model-route coupling from the ordinary flow execution graph.

Roles come from graph attachments and authorized agent metadata, not route
prefixes, display names or model-generated guesses. No providers run here.
"""

from collections.abc import Callable, Mapping
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
from src.lib.executable_flow_graph import project_executable_flow_graph
from src.lib.flows.formatter_capability import resolved_formatter_format
from src.lib.flows.validation_attachments import (
    apply_flow_validation_attachment_defaults, validation_schedule_from_node_data,
)
from src.schemas.flows import FlowDefinition
from src.schemas.agent_execution_revision import AgentExecutionReceipt

from .models import BenchmarkSuiteRoute, FrozenStrictModel
from .execution_context import BenchmarkCuratorContext
from .suites import _digest

FlowStageRole = Literal["extraction", "validation", "output", "other", "supervisor"]
_CATEGORY_ROLES: dict[str, FlowStageRole] = {
    "Extraction": "extraction", "Validation": "validation", "Output": "output",
}


class BenchmarkFlowStage(FrozenStrictModel):
    stage_id: str
    node_id: str | None
    source_node_id: str | None = None
    title: str
    role: FlowStageRole
    route_slot: str | None
    binding_id: str | None = None
    agent_id: str | None = None
    execution_receipt: AgentExecutionReceipt | None = None
    default_route: BenchmarkSuiteRoute | None = None


def authorize_scheduled_validator(
    session: Session, curator: BenchmarkCuratorContext,
    source_metadata: Mapping[str, Any], binding: Mapping[str, Any],
) -> AgentExecutionReceipt | str | None:
    """Authorize a package or pinned custom validator without constructing it."""
    from src.lib.agent_studio.catalog_service import get_active_visible_agent_metadata
    from src.lib.agent_studio.execution_revision_service import (
        authorize_execution_receipt, get_execution_revision,
    )
    from src.lib.config.agent_loader import canonical_system_agent_key, get_agent_definition_for_package
    from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
    from src.lib.domain_packs.profile_validation import resolve_profile_validation

    raw_receipt = source_metadata.get("execution_receipt")
    if raw_receipt:
        receipt = authorize_execution_receipt(
            session, raw_receipt, curator.db_user_id, active_group_ids=list(curator.active_groups),
        )
        if receipt.output_contract.generic_profile_ref is not None:
            generic_pack = resolve_curation_domain_pack_by_id("generic")
            if generic_pack is None:
                raise ValueError("Generic validation package is unavailable")
            context = resolve_profile_validation(
                receipt, generic_pack, db=session,
                user_id=curator.db_user_id, active_group_ids=curator.active_groups,
            )
            if context is None or context.unavailable:
                raise ValueError("Saved profile validators are unavailable")
            selected = next((item for item in context.registry.bindings
                             if item.binding_id == binding["validator_binding_id"]), None)
            if selected is None:
                raise ValueError("Validator binding is not in the authorized profile")
            pin = selected.raw.get("custom_validator")
            if pin:
                row, saved = get_execution_revision(
                    session, UUID(pin["agent_id"]), UUID(pin["revision_id"]), curator.db_user_id,
                    active_group_ids=list(curator.active_groups),
                )
                verified = authorize_execution_receipt(session, AgentExecutionReceipt(
                    agent_id=row.agent_id, agent_key=pin["agent_key"],
                    agent_revision_id=row.id, revision=row.revision,
                    fingerprint=pin["fingerprint"], output_contract=saved.output_contract,
                ).model_dump(mode="json"), curator.db_user_id, active_group_ids=list(curator.active_groups))
                return verified
    if not binding.get("validator_agent_id"):
        return None
    package_id = binding.get("validator_package_id")
    if not package_id:
        raise ValueError("Validator has no package identity")
    definition = get_agent_definition_for_package(package_id, binding["validator_agent_id"])
    if definition is None:
        raise ValueError("Validator package is unavailable")
    key = canonical_system_agent_key(definition)
    get_active_visible_agent_metadata(
        key, db_user_id=curator.db_user_id, authenticated_groups=list(curator.active_groups),
    )
    return key


def flow_stages(
    definition: FlowDefinition,
    entries_by_node: Mapping[str, Mapping[str, Any]],
    *, authorize_validator: Callable[[str, Mapping[str, Any]], AgentExecutionReceipt | str | None],
) -> tuple[BenchmarkFlowStage, ...]:
    """Hydrate the same attachment defaults used by normal flow authoring.

    The callback must authorize scheduled model validators and return their
    canonical agent key; deterministic bindings return None. Graph-attached
    validators use the same agent:<key> route as ordinary executable nodes.
    Domain-pack/profile scheduled validators use validator:<binding-id>.
    """
    hydrated = apply_flow_validation_attachment_defaults(
        definition, entries_by_node=entries_by_node,
    )
    graph = project_executable_flow_graph(hydrated)
    sidecars = {sidecar.validator_node_id: sidecar for sidecar in graph.validation_sidecars}
    nodes = {node.id: node for node in hydrated.nodes}
    stages = [BenchmarkFlowStage(
        stage_id="supervisor", node_id=None, title="Flow supervisor",
        role="supervisor", route_slot="supervisor",
    )]
    ordered_node_ids = tuple(dict.fromkeys((
        *graph.ordered_executable_node_ids,
        *(sidecar.validator_node_id for sidecar in graph.validation_sidecars),
    )))
    for node_id in ordered_node_ids:
        node = nodes[node_id]
        if node.type == "task_input":
            continue
        entry = entries_by_node[node_id]
        agent_id = node.data.agent_id
        sidecar = sidecars.get(node_id)
        role: FlowStageRole = "validation" if sidecar else _CATEGORY_ROLES.get(str(entry.get("category") or ""), "other")
        # These branches mirror executor runtime tools, not biological names.
        non_model = agent_id in {CURATION_PREP_AGENT_ID, "curation_handoff"} or (
            resolved_formatter_format(agent_id, entry) is not None
        )
        receipt = entry.get("execution_receipt")
        stages.append(BenchmarkFlowStage(
            stage_id=_digest({"node": node_id}), node_id=node_id,
            source_node_id=sidecar.source_node_id if sidecar else None,
            binding_id=sidecar.binding_id if sidecar else None,
            title=node.data.agent_display_name, role=role, agent_id=agent_id,
            route_slot=None if non_model else f"agent:{agent_id}",
            execution_receipt=AgentExecutionReceipt.model_validate(receipt) if receipt else None,
        ))
        if sidecar is not None:
            # The normal executor invokes sidecars as validator requests, not
            # as control steps followed by _execute_validation_groups_for_step.
            # Their palette defaults therefore are not another scheduled layer.
            continue
        schedule = validation_schedule_from_node_data(node.data.model_dump())
        for binding in schedule["scheduled_validators"]:
            binding_id = binding["validator_binding_id"]
            source = authorize_validator(node_id, binding)
            validator_receipt = source if isinstance(source, AgentExecutionReceipt) else None
            validator_id = source.agent_key if isinstance(source, AgentExecutionReceipt) else source
            stages.append(BenchmarkFlowStage(
                stage_id=_digest({"node": node_id, "binding": binding_id}),
                node_id=node_id, source_node_id=node_id,
                binding_id=binding_id, agent_id=validator_id,
                title=binding.get("validator_id") or binding_id,
                role="validation",
                route_slot=f"validator:{binding_id}" if validator_id is not None else None,
                execution_receipt=validator_receipt,
            ))
    return tuple(stages)
