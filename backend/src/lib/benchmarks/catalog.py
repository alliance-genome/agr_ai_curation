"""Project-agnostic construction of stable benchmark route-slot catalogs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .models import (
    BenchmarkExecutionTarget,
    BenchmarkModelCatalogEntry,
    BenchmarkRoute,
    BenchmarkRouteCatalog,
    BenchmarkRouteSlot,
    BenchmarkTargetCatalogEntry,
)


def build_route_catalog(
    *,
    models: Iterable[BenchmarkModelCatalogEntry],
    supervisor_default: BenchmarkRoute,
    agent_defaults: Mapping[str, BenchmarkRoute],
    model_validator_defaults: Mapping[str, BenchmarkRoute],
    agent_targets: Iterable[str],
    flow_agents: Mapping[str, Iterable[str]],
    flow_model_validators: Mapping[str, Iterable[str]],
    agent_aliases: Mapping[str, str] | None = None,
) -> BenchmarkRouteCatalog:
    """Build slots from deployment catalogs without provider-specific assumptions.

    Deterministic validators have no model route and therefore are intentionally
    absent from ``model_validator_defaults`` and the resulting catalog.
    """

    aliases = agent_aliases or {}
    unknown_alias_targets = set(aliases.values()) - set(agent_defaults)
    if unknown_alias_targets:
        raise ValueError(
            "agent aliases reference entries without defaults: "
            + ", ".join(sorted(unknown_alias_targets))
        )

    slots = [
        BenchmarkRouteSlot(
            slot="supervisor", kind="supervisor", default_route=supervisor_default
        )
    ]
    slots.extend(
        BenchmarkRouteSlot(slot=f"agent:{agent_id}", kind="agent", default_route=route)
        for agent_id, route in sorted(agent_defaults.items())
    )
    slots.extend(
        BenchmarkRouteSlot(
            slot=f"validator:{validator_id}",
            kind="validator",
            default_route=route,
        )
        for validator_id, route in sorted(model_validator_defaults.items())
    )

    targets = [
        BenchmarkTargetCatalogEntry(
            target=BenchmarkExecutionTarget(kind="agent", id=agent_id),
            route_slots=(f"agent:{agent_id}",),
        )
        for agent_id in sorted(set(agent_targets))
    ]
    for flow_id in sorted(flow_agents):
        agent_ids = tuple(
            sorted(
                {aliases.get(agent_id, agent_id) for agent_id in flow_agents[flow_id]}
            )
        )
        validator_ids = tuple(sorted(set(flow_model_validators.get(flow_id, ()))))
        missing_agents = set(agent_ids) - set(agent_defaults)
        missing_validators = set(validator_ids) - set(model_validator_defaults)
        if missing_agents or missing_validators:
            missing = sorted(missing_agents | missing_validators)
            raise ValueError(
                f"flow '{flow_id}' references model-bearing catalog entries "
                f"without defaults: {', '.join(missing)}"
            )
        targets.append(
            BenchmarkTargetCatalogEntry(
                target=BenchmarkExecutionTarget(kind="flow", id=flow_id),
                route_slots=(
                    "supervisor",
                    *(f"agent:{agent_id}" for agent_id in agent_ids),
                    *(f"validator:{validator_id}" for validator_id in validator_ids),
                ),
            )
        )
    return BenchmarkRouteCatalog(
        models=tuple(sorted(models, key=lambda item: (item.provider, item.model))),
        route_slots=tuple(slots),
        targets=tuple(targets),
    )
