"""Workshop validator discovery preserves pinned identity and caller access."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.lib.agent_studio.custom_profile_validators import custom_validator_capabilities
from src.lib.domain_packs.validation_registry import ValidatorAgentRef
from tests.unit.lib.agent_studio.test_profile_mappings import fixture


@pytest.fixture
def catalog(monkeypatch):
    from src.lib.agent_studio import custom_agent_service, execution_revision_service
    from src.lib.config import agent_loader, schema_discovery
    from src.models.sql import database
    _, base = fixture()
    base = replace(base, binding=replace(base.binding, validator_agent=ValidatorAgentRef('example', 'lookup')))
    agent = SimpleNamespace(id=uuid4(), execution_revision_id=uuid4(), agent_key='ca_example', name='My lookup')
    revision = SimpleNamespace(id=agent.execution_revision_id, agent_id=agent.id, fingerprint='sha256:' + 'a' * 64)
    saved = SimpleNamespace(template_source='lookup', output_contract=SimpleNamespace(output_mode='domain', output_schema_key='validator_result'))
    definition = SimpleNamespace(output_schema='validator_result')
    db = MagicMock()
    db.get.side_effect = lambda model, key: revision if key == revision.id else agent
    monkeypatch.setattr(database, 'SessionLocal', lambda: MagicMock(__enter__=lambda _: db, __exit__=lambda *_: None))
    visible = MagicMock(return_value=[agent])
    authorize = MagicMock(return_value=(revision, saved))
    monkeypatch.setattr(custom_agent_service, 'list_custom_agents_visible_to_user', visible)
    monkeypatch.setattr(execution_revision_service, 'get_execution_revision', authorize)
    monkeypatch.setattr(agent_loader, 'get_agent_definition_for_package', lambda *_: definition)
    monkeypatch.setattr(agent_loader, 'canonical_system_agent_key', lambda _: 'lookup')
    from src.schemas.domain_validator import DomainValidatorResultBase
    monkeypatch.setattr(schema_discovery, 'resolve_output_schema', lambda _: DomainValidatorResultBase)
    return base, agent, revision, saved, visible, authorize


def test_custom_pin_survives_head_advance_and_does_not_change_with_rename(catalog):
    base, agent, revision, saved, visible, authorize = catalog
    first = custom_validator_capabilities([base], user_id=7, active_group_ids=['FB'])[0]
    assert first.ref.binding_id.endswith(str(revision.id))
    assert first.binding.raw['custom_validator']['fingerprint'] == revision.fingerprint
    authorize.assert_called_once()
    assert authorize.call_args.args[3] == 7
    assert authorize.call_args.kwargs['active_group_ids'] == ['FB']
    agent.name = 'Renamed lookup'
    visible.return_value = []  # archived or no longer the current head; explicitly saved pin remains discoverable
    pinned = custom_validator_capabilities([base], user_id=7, active_group_ids=['FB'], references=[first.ref])[0]
    assert pinned.fingerprint() == first.fingerprint()
    assert pinned.binding.display_name == 'Renamed lookup'
    assert not pinned.binding.batch_enabled


def test_revoked_access_and_wrong_schema_or_template_are_not_offered(catalog):
    base, agent, revision, saved, visible, authorize = catalog
    saved.template_source = 'unrelated'
    assert custom_validator_capabilities([base], user_id=7, active_group_ids=[]) == []
    saved.template_source = 'lookup'
    saved.output_contract.output_schema_key = 'different_result'
    assert custom_validator_capabilities([base], user_id=7, active_group_ids=[]) == []
    saved.output_contract.output_schema_key = 'validator_result'
    from src.lib.agent_studio.execution_revision_service import ExecutionRevisionNotFoundError
    authorize.side_effect = ExecutionRevisionNotFoundError('not visible')
    assert custom_validator_capabilities([base], user_id=8, active_group_ids=[]) == []


def test_no_caller_never_discovers_custom_agents(catalog):
    base, _, _, _, visible, _ = catalog
    assert custom_validator_capabilities([base], user_id=None, active_group_ids=[]) == []
    visible.assert_not_called()


def test_dispatch_reauthorizes_exact_revision_and_rejects_changed_identity(monkeypatch):
    from src.lib.agent_studio import custom_profile_validators as service, catalog_service
    pin = {'agent_key': 'ca_saved', 'revision_id': str(uuid4()), 'fingerprint': 'sha256:expected'}
    context = SimpleNamespace(user_id='request-sub', document_id='paper', authenticated_groups=('FB',))
    resolve = MagicMock(return_value=7)
    monkeypatch.setattr(service, 'runtime_validator_user_id', resolve)
    agent = SimpleNamespace(execution_receipt={'fingerprint': pin['fingerprint']})
    build = MagicMock(return_value=agent)
    monkeypatch.setattr(catalog_service, 'get_agent_by_id', build)
    assert service.build_custom_validator_agent(pin, context) is agent
    resolve.assert_called_with('request-sub')
    build.assert_called_once_with('ca_saved', execution_revision_id=pin['revision_id'], db_user_id=7,
                                  user_id='request-sub', document_id='paper', authenticated_groups=['FB'])
    agent.execution_receipt['fingerprint'] = 'sha256:changed'
    with pytest.raises(ValueError, match='fingerprint'):
        service.build_custom_validator_agent(pin, context)
    build.side_effect = ValueError('access revoked')
    with pytest.raises(ValueError, match='access revoked'):
        service.build_custom_validator_agent(pin, context)
    resolve.return_value = None
    with pytest.raises(ValueError, match='Authenticated user'):
        service.build_custom_validator_agent(pin, context)
