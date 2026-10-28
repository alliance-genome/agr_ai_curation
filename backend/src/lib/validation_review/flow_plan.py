"""Compile only saved flow validator choices into the shared review dispatcher.

The supervisor uses only configured automatic validators and allowed opt-outs.
"""
from dataclasses import replace

from src.lib.domain_packs.flow_validator_selection import (
    effective_flow_validation_groups, set_flow_validator_selections, reset_flow_validator_selections,
)
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.lib.validation_history import store


def configured_registry(registry, groups, nodes):
    selected = effective_flow_validation_groups(groups)
    definitions = {binding.binding_id: binding for binding in registry.bindings}
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
        raise store.HistoryConflict("Unknown saved flow validation selection")
    authorized = {group.get("binding_id") or group.get("validator_binding_id") for group in selected}
    return replace(registry, bindings=tuple(binding
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
    # Only explicit opt-outs suppress configured automatic validation.
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
