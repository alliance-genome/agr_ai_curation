"""Saved flow choices are an allowlist, including explicit validator opt-outs."""
from dataclasses import replace

import pytest

from src.lib.validation_review.flow_plan import configured_registry
from src.lib.validation_history import store
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from tests.unit.lib.domain_packs.test_validator_dispatch import _multivalued_dispatch_pack


def test_profile_mappings_cannot_be_omitted_replaced_or_extended(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.lib.validation_review.flow_plan import prepare
    registry = DomainPackValidationRegistry.from_domain_pack(_multivalued_dispatch_pack(tmp_path))
    context = SimpleNamespace(registry=registry, profile=SimpleNamespace(
        contract=SimpleNamespace(validator_mappings=['saved-mapping'])))
    monkeypatch.setattr('src.lib.domain_packs.profile_validation.resolve_envelope_profile_validation', lambda *a, **kw: context)
    monkeypatch.setattr('src.lib.domain_packs.profile_validation.profile_mapping_binding_id', lambda profile, mapping: 'saved-mapping')
    runtime = SimpleNamespace(user_id='owner', authenticated_groups=['TEAM_A'])
    for groups in ([], [{'binding_id': 'other', 'state': 'automatic'}],
                   [{'binding_id': 'saved-mapping', 'state': 'replaced'}],
                   [{'binding_id': 'saved-mapping', 'state': 'automatic'}] * 2):
        with pytest.raises(store.HistoryConflict, match='exact saved profile'):
            prepare(object(), object(), groups=groups, nodes={}, runtime_context=runtime)


def test_flow_registry_contains_only_declared_automatic_bindings(tmp_path):
    registry = DomainPackValidationRegistry.from_domain_pack(_multivalued_dispatch_pack(tmp_path))
    first = registry.bindings[0]
    registry = replace(registry, bindings=(*registry.bindings, replace(first, binding_id="not.in.flow")))
    configured = configured_registry(registry, [{"binding_id": first.binding_id, "state": "automatic"}], {})
    assert configured.bindings == (first,)
    assert configured_registry(registry, [], {}).bindings == ()


@pytest.mark.parametrize("state", ["replaced", "supplemental"])
@pytest.mark.parametrize("agent", ["ca_custom", "packaged_validator"])
def test_all_flow_overrides_rejected_even_with_saved_execution_revision(tmp_path, state, agent):
    registry = DomainPackValidationRegistry.from_domain_pack(_multivalued_dispatch_pack(tmp_path))
    group = {"binding_id": registry.bindings[0].binding_id, "state": state, "validator_node_id": "chosen"}
    node = {"id": "chosen", "data": {"agent_id": agent, "agent_revision_id": "revision", "execution_receipt": {"saved": True}}}
    with pytest.raises(ValueError, match="not supported"):
        configured_registry(registry, [group], {"chosen": node})


def test_unknown_binding_and_required_optout_rejected(tmp_path):
    registry = DomainPackValidationRegistry.from_domain_pack(_multivalued_dispatch_pack(tmp_path))
    with pytest.raises(store.HistoryConflict, match="unavailable validator binding"):
        configured_registry(registry, [{"binding_id": "unknown", "state": "automatic"}], {})
    first = replace(registry.bindings[0], required=True, allow_opt_out=False)
    with pytest.raises(store.HistoryConflict, match="does not permit"):
        configured_registry(replace(registry, bindings=(first,)), [{"binding_id": first.binding_id, "state": "skipped"}], {})
