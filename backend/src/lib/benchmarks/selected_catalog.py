"""Prepare explicitly selected saved flows through server-owned discovery."""

from uuid import UUID

from src.lib.agent_studio.agent_service import list_agents_visible_to_user

from .flow_capture import capture_saved_flow
from .frozen_flow import FrozenBenchmarkFlow, thaw_json
from .models import BenchmarkRouteCatalog, BenchmarkRouteSlot, BenchmarkTargetCatalogEntry
from .saved_flows import saved_flow_contracts
from .system_snapshot import capture_system_agent
from .supervisor_snapshot import capture_flow_supervisor
from .runtime_catalog import build_curator_route_catalog
from .dependencies import capture_dependencies
from .stage_definitions import definitions_from_flow_stages


def _with_dependencies(session, curator, catalog):
    targets = tuple(target.model_copy(update={
        "dependencies": capture_dependencies(session, curator, target),
    }) for target in catalog.targets)
    return catalog.model_copy(update={"targets": targets})


def prepare_selected_catalog(session, curator, catalog: BenchmarkRouteCatalog, suite) -> BenchmarkRouteCatalog:
    """Never accept executable definition or source prompts from the client."""
    from src.lib.openai_agents.config import get_benchmark_max_cases

    if len(suite.cases) > get_benchmark_max_cases():
        raise ValueError("Selected suite exceeds the configured case limit")
    selected = {}
    for case in suite.cases:
        if case.target.source_kind == "saved_flow":
            previous = selected.setdefault(case.target.id, case.target)
            if previous != case.target:
                raise ValueError("One saved flow cannot select conflicting revisions in a suite")
    existing_keys = {(case.target.kind, case.target.id) for case in suite.cases if case.target.source_kind is None}
    installed = build_curator_route_catalog(session, curator, freeze_targets=existing_keys) if existing_keys else None
    if not selected:
        if installed is None:
            raise ValueError("Suite has no selected targets")
        return _with_dependencies(session, curator, installed)
    agents = {agent.agent_key: agent for agent in list_agents_visible_to_user(
        session, curator.db_user_id, active_group_ids=curator.active_groups,
    )}
    targets = list(installed.targets) if installed is not None else []
    used_slots = {slot for target in targets for slot in target.route_slots}
    slots = {slot.slot: slot for slot in installed.route_slots if slot.slot in used_slots} if installed else {}
    explicit_slots = set.intersection(*(set(config.routes) for config in suite.configurations))
    for target in selected.values():
        if any(item.target.kind == target.kind and item.target.id == target.id for item in targets):
            raise ValueError("Saved flow identity conflicts with an existing catalog target")
        frozen = capture_saved_flow(session, curator, UUID(target.id), expected_revision=target.source_revision)
        contracts = saved_flow_contracts(session, curator, UUID(target.id), expected_revision=target.source_revision)
        if not contracts.stages or set(contracts.route_default_conflicts) - explicit_slots:
            raise ValueError("Selected flow has unavailable stages or conflicting shared model defaults")
        sources, systems, target_slots = {}, {}, set()
        nodes = {node["id"]: node for node in frozen.definition["nodes"]}
        for stage in contracts.stages:
            slot = stage.route_slot
            if slot is None:
                row = agents.get(stage.agent_id)
                if row is not None and row.visibility == "system":
                    systems[stage.agent_id] = capture_system_agent(row, active_groups=curator.active_groups)
                continue
            if stage.default_route is None:
                raise ValueError("Selected flow stage has no authorized model default")
            target_slots.add(slot)
            candidate = BenchmarkRouteSlot.model_validate({
                "slot": slot, "kind": "supervisor" if slot == "supervisor" else slot.split(":", 1)[0],
                "default_route": stage.default_route,
            })
            # Shared routes across selected targets must have one source/default.
            if slot in slots and slots[slot].default_route != candidate.default_route:
                if slot not in explicit_slots:
                    raise ValueError("Selected flow default conflicts with the catalog shared route")
            else:
                slots[slot] = candidate
            if stage.execution_receipt is not None:
                receipt = stage.execution_receipt
                if slot in sources and sources[slot] != receipt:
                    raise ValueError("Coupled flow nodes have different frozen source revisions")
                node = nodes.get(stage.node_id)
                if node is not None and node["data"]["agent_id"] == receipt.agent_key:
                    if thaw_json(node["data"].get("execution_receipt")) != receipt.model_dump(mode="json"):
                        raise ValueError("Flow source changed while capturing its contracts")
                sources[slot] = receipt
            elif stage.agent_id is not None:
                row = agents.get(stage.agent_id)
                if row is None:
                    raise ValueError("Flow system agent is unavailable")
                systems[stage.agent_id] = capture_system_agent(row, active_groups=curator.active_groups)
        frozen_data = frozen.model_dump(mode="json")
        frozen_data["output_contracts"] = {
            node.node_id: node.model_dump(mode="json") for node in contracts.nodes
        }
        frozen_data["stage_definitions"] = definitions_from_flow_stages(contracts.stages)
        targets.append(BenchmarkTargetCatalogEntry(
            target=target, route_slots=tuple(sorted(target_slots)),
            source_execution_receipts=sources, system_agent_snapshots=systems,
            flow_snapshot=FrozenBenchmarkFlow.model_validate(frozen_data),
            supervisor_snapshot=capture_flow_supervisor(frozen, curator),
        ))
    result = BenchmarkRouteCatalog(models=catalog.models, route_slots=tuple(slots.values()), targets=tuple(targets))
    return _with_dependencies(session, curator, result)
