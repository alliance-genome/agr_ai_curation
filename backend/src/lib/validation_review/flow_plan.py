"""Compile only saved flow validator choices into the shared review dispatcher.

The supervisor never chooses an agent. Replacement/supplemental selections use
one saved node for the original binding, preserving its target/output contract.
"""
from copy import deepcopy
from dataclasses import replace

from src.lib.domain_packs.flow_validator_selection import (
    effective_flow_validation_groups, set_flow_validator_selections, reset_flow_validator_selections,
)
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.lib.validation_history import store


def configured_registry(registry, groups, nodes):
    selected = effective_flow_validation_groups(groups)
    definitions = {binding.binding_id: binding for binding in registry.bindings}
    replacements = {}
    for group in selected:
        binding_id = group.get("binding_id") or group.get("validator_binding_id")
        if not binding_id or binding_id not in definitions:
            raise store.HistoryConflict("Flow selection names an unavailable validator binding")
        binding = definitions[binding_id]
        state = group.get("state")
        if state == "skipped":
            if binding.required and not binding.allow_opt_out:
                raise store.HistoryConflict("Required validator does not permit flow opt-out")
            continue
        if state == "automatic":
            continue
        if state not in {"replaced", "supplemental"}:
            raise store.HistoryConflict("Unknown saved flow validation selection")
        node = nodes.get(str(group.get("validator_node_id")))
        data = node.get("data", {}) if node else {}
        agent_id = data.get("agent_id")
        if not agent_id:
            raise store.HistoryConflict("Saved flow validator node is unavailable")
        if str(agent_id).startswith("ca_") and (not data.get("agent_revision_id") or not data.get("execution_receipt")):
            raise store.HistoryConflict("Custom flow validator requires its saved execution revision")
        replacements[binding_id] = replace(binding, batch_enabled=False,
            raw={**binding.raw, "flow_validator": {"node_id": node["id"], "data": deepcopy(data)}})
    authorized = {group.get("binding_id") or group.get("validator_binding_id") for group in selected}
    return replace(registry, bindings=tuple(replacements.get(binding.binding_id, binding)
        for binding in registry.bindings if binding.binding_id in authorized))


def prepare(envelope, domain_pack, *, groups, nodes, runtime_context):
    from src.lib.domain_packs.profile_validation import resolve_envelope_profile_validation, profile_mapping_binding_id
    from src.lib.domain_packs.validator_dispatch import prepare_validator_dispatch

    profile = resolve_envelope_profile_validation(envelope, domain_pack,
        user_id=runtime_context.user_id, active_group_ids=runtime_context.authenticated_groups or ())
    registry = profile.registry if profile is not None else DomainPackValidationRegistry.from_domain_pack(domain_pack)
    if profile is not None:
        expected = {profile_mapping_binding_id(profile.profile, mapping) for mapping in profile.profile.contract.validator_mappings}
        supplied = [group.get("binding_id") or group.get("validator_binding_id") for group in groups]
        if len(supplied) != len(expected) or set(supplied) != expected or any(group.get("state") != "automatic" for group in groups):
            raise store.HistoryConflict("Flow must retain the exact saved profile's automatic mappings")
    registry = configured_registry(registry, groups, nodes)
    # Custom choices are now explicit bound execution routes, not suppressed work.
    # Only opt-outs remain in the dispatcher selection context.
    token = set_flow_validator_selections([group for group in groups if group.get("state") == "skipped"])
    try:
        plan = prepare_validator_dispatch(envelope, domain_pack, registry=registry,
            runtime_context=runtime_context, profile_context=profile)
        excluded = {group.get("binding_id") or group.get("validator_binding_id")
                    for group in groups if group.get("state") == "skipped"}
        # The initial episode records exclusions, while the saved executable
        # registry cannot accidentally restore them during follow-up matching.
        return replace(plan,
            registry=replace(plan.registry, bindings=tuple(binding for binding in plan.registry.bindings
                if binding.binding_id not in excluded)),
            matches=tuple(match for match in plan.matches if match.binding.binding_id not in excluded))
    finally:
        reset_flow_validator_selections(token)


def build_validator(pin, runtime_context):
    from src.lib.agent_studio.catalog_service import get_agent_by_id
    from src.lib.openai_agents.benchmark_routing import benchmark_route_kwargs

    if runtime_context is None or not runtime_context.user_id:
        raise store.HistoryAccessError("Flow validator requires authenticated runtime ownership")
    from src.lib.agent_studio.custom_profile_validators import runtime_validator_user_id
    db_user_id = runtime_validator_user_id(runtime_context.user_id)
    if db_user_id is None:
        raise store.HistoryAccessError("Flow validator owner is unavailable")
    data = pin["data"]
    agent_id = data["agent_id"]
    kwargs = {"user_id": runtime_context.user_id, "db_user_id": db_user_id, "document_id": runtime_context.document_id,
              "authenticated_groups": list(runtime_context.authenticated_groups or ()), "cost_node_id": pin["node_id"]}
    if data.get("custom_instructions"):
        kwargs["additional_runtime_context"] = [data["custom_instructions"]]
    if data.get("agent_revision_id"):
        kwargs.update(execution_revision_id=data["agent_revision_id"], execution_receipt=data["execution_receipt"])
    route = benchmark_route_kwargs("agent:" + agent_id)
    if route and str(agent_id).startswith("ca_"):
        from src.lib.agent_studio.catalog_service import get_benchmark_agent_by_id
        from src.lib.benchmarks.source_revisions import require_benchmark_source
        from src.schemas.agent_execution_revision import AgentExecutionReceipt
        saved = AgentExecutionReceipt.model_validate(kwargs.pop("execution_receipt"))
        revision = kwargs.pop("execution_revision_id")
        frozen = require_benchmark_source("agent:" + agent_id, agent_id)
        if frozen != saved or str(frozen.agent_revision_id) != str(revision):
            raise store.HistoryConflict("Flow validator differs from the frozen benchmark source")
        agent = get_benchmark_agent_by_id(agent_id, benchmark_slot="agent:" + agent_id, **kwargs)
    else:
        agent = get_agent_by_id(agent_id, **kwargs, **route)
    from src.lib.packages.tool_roles import require_validator_tool_boundary
    from .extractor_phase import source_tools
    require_validator_tool_boundary([tool.name for tool in source_tools(agent)],
        validator_role=True, agent_label=agent.name)
    return agent
