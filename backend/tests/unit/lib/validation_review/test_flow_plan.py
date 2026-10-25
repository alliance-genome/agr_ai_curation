"""Saved flow choices are an allowlist, including explicit validator opt-outs."""
from dataclasses import replace

import pytest

from src.lib.validation_review.flow_plan import configured_registry
from src.lib.validation_history import store
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from tests.unit.lib.domain_packs.test_validator_dispatch import _multivalued_dispatch_pack


def test_flow_registry_contains_only_declared_binding_and_exact_node(tmp_path):
    registry = DomainPackValidationRegistry.from_domain_pack(_multivalued_dispatch_pack(tmp_path))
    first = registry.bindings[0]
    extra = replace(first, binding_id="not.in.flow")
    registry = replace(registry, bindings=(*registry.bindings, extra))
    node = {"id": "chosen", "data": {"agent_id": "chosen_validator", "custom_instructions": "Check the supplied term"}}
    configured = configured_registry(registry, [{"binding_id": first.binding_id, "state": "replaced", "validator_node_id": "chosen"}], {"chosen": node})
    assert [binding.binding_id for binding in configured.bindings] == [first.binding_id]
    assert configured.bindings[0].raw["flow_validator"] == {"node_id": "chosen", "data": node["data"]}
    assert configured.bindings[0].validator_agent == first.validator_agent
    assert not configured.bindings[0].batch_enabled
    assert configured_registry(registry, [], {}).bindings == ()


def test_unknown_missing_or_unpinned_flow_agents_are_rejected(tmp_path):
    registry = DomainPackValidationRegistry.from_domain_pack(_multivalued_dispatch_pack(tmp_path))
    first = registry.bindings[0]
    group = {"binding_id": first.binding_id, "state": "replaced", "validator_node_id": "chosen"}
    with pytest.raises(store.HistoryConflict, match="unavailable"):
        configured_registry(registry, [group], {})
    with pytest.raises(store.HistoryConflict, match="saved execution revision"):
        configured_registry(registry, [group], {"chosen": {"id": "chosen", "data": {"agent_id": "ca_custom"}}})
    with pytest.raises(store.HistoryConflict, match="unavailable validator binding"):
        configured_registry(registry, [{**group, "binding_id": "unconfigured"}], {})
    required = replace(registry, bindings=(replace(first, required=True, allow_opt_out=False),))
    with pytest.raises(store.HistoryConflict, match="does not permit"):
        configured_registry(required, [{"binding_id": first.binding_id, "state": "skipped"}], {})


@pytest.mark.parametrize('state', ['replaced', 'supplemental'])
def test_custom_choice_replaces_automatic_in_single_shared_dispatcher(tmp_path, state):
    registry = DomainPackValidationRegistry.from_domain_pack(_multivalued_dispatch_pack(tmp_path))
    binding = registry.bindings[0]
    chosen = {'id': 'chosen', 'data': {'agent_id': 'saved_validator', 'custom_instructions': 'Check identity'}}
    groups = [{'state': 'automatic', 'binding_id': binding.binding_id},
              {'state': state, 'binding_id': binding.binding_id, 'validator_node_id': 'chosen'}]
    resolved = configured_registry(registry, groups, {'chosen': chosen})
    assert len(resolved.bindings) == 1
    assert resolved.bindings[0].raw['flow_validator']['data'] == chosen['data']
    assert resolved.bindings[0].input_fields == binding.input_fields
    assert resolved.bindings[0].expected_result_fields == binding.expected_result_fields


def test_flow_validator_uses_only_saved_node_and_current_authenticated_owner(monkeypatch):
    from types import SimpleNamespace
    from agents import Agent
    from src.lib.validation_review.flow_plan import build_validator
    calls = []
    def build(key, **kwargs):
        calls.append((key, kwargs))
        return Agent(name='Saved custom validator', model='test', instructions='Validate supplied inputs')
    monkeypatch.setattr('src.lib.agent_studio.catalog_service.get_agent_by_id', build)
    monkeypatch.setattr('src.lib.agent_studio.custom_profile_validators.runtime_validator_user_id', lambda owner: 7)
    monkeypatch.setattr('src.lib.packages.tool_roles.require_validator_tool_boundary', lambda *a, **kw: None)
    pin = {'node_id': 'validator-node', 'data': {'agent_id': 'ca_saved', 'agent_revision_id': 'revision',
        'execution_receipt': {'revision': 'saved'}, 'custom_instructions': 'Resolve this organism'}}
    build_validator(pin, SimpleNamespace(user_id='owner', document_id='paper', authenticated_groups=['TEAM_A']))
    assert calls == [('ca_saved', {'user_id': 'owner', 'db_user_id': 7, 'document_id': 'paper',
        'authenticated_groups': ['TEAM_A'], 'cost_node_id': 'validator-node',
        'execution_revision_id': 'revision', 'execution_receipt': {'revision': 'saved'},
        'additional_runtime_context': ['Resolve this organism']})]


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


def test_custom_flow_validator_rejects_stale_frozen_benchmark_revision(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from src.lib.validation_review.flow_plan import build_validator
    from src.lib.openai_agents.benchmark_routing import benchmark_route_plan
    from src.lib.benchmarks.models import BenchmarkSuiteRoute
    from src.lib.benchmarks.source_revisions import benchmark_source_revisions
    from tests.unit.lib.benchmarks.test_source_revisions import source_receipt
    key = 'ca_pinned_validator'
    frozen = source_receipt().model_copy(update={'agent_key': key})
    stale = frozen.model_copy(update={'revision': frozen.revision + 1})
    constructor = Mock()
    monkeypatch.setattr('src.lib.agent_studio.catalog_service.get_benchmark_agent_by_id', constructor)
    monkeypatch.setattr('src.lib.agent_studio.custom_profile_validators.runtime_validator_user_id', lambda _: 7)
    routes = {'agent:' + key: BenchmarkSuiteRoute(provider='openai', model='fixture-model', reasoning_effort='low')}
    with benchmark_route_plan(routes), benchmark_source_revisions({'agent:' + key: frozen}):
        with pytest.raises(store.HistoryConflict, match='differs from the frozen benchmark source'):
            build_validator({'node_id': 'saved', 'data': {'agent_id': key,
                'agent_revision_id': str(stale.agent_revision_id), 'execution_receipt': stale.model_dump(mode='json')}},
                SimpleNamespace(user_id='owner', document_id='paper', authenticated_groups=()))
    constructor.assert_not_called()
