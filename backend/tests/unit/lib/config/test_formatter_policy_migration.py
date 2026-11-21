"""Only missing runtime helper policy rows are seeded; explicit decisions survive."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import json

from ..packages import find_repo_root


def test_formatter_policy_backfill_preserves_existing_decisions(monkeypatch):
    path = find_repo_root(Path(__file__)) / 'backend/alembic/versions/o2d3e4f5a6b7_backfill_formatter_tool_policies.py'
    spec = spec_from_file_location('formatter_policy_migration_test', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    for key in ['AGR_RUNTIME_ROOT', 'AGR_RUNTIME_CONFIG_DIR', 'AGR_RUNTIME_PACKAGES_DIR',
                'TOOL_POLICY_DEFAULTS_CONFIG_PATH', 'APP_VERSION', 'AGR_RUNTIME_PACKAGE_API_VERSION']:
        monkeypatch.delenv(key, raising=False)
    calls = []
    monkeypatch.setattr(module.op, 'get_bind', lambda: SimpleNamespace(execute=lambda sql, values: calls.append((str(sql), values))))
    module.upgrade()
    assert {v['tool_key'] for _, v in calls} == set(module.FORMATTER_TOOLS)
    assert len(calls) == 9
    for sql, values in calls:
        assert 'ON CONFLICT (tool_key) DO NOTHING' in sql
        assert not values['allow_attach']
        assert not values['curator_visible']
        assert values['allow_execute']
        assert isinstance(json.loads(values['config']), dict)


def test_chat_output_policy_backfill_seeds_only_new_runtime_helpers(monkeypatch):
    root = find_repo_root(Path(__file__))
    path = root / 'backend/alembic/versions/q4f5a6b7c8d9_backfill_chat_output_tool_policies.py'
    spec = spec_from_file_location('chat_output_policy_migration_test', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == 'p3e4f5a6b7c8'
    for key in ['AGR_RUNTIME_ROOT', 'AGR_RUNTIME_CONFIG_DIR', 'AGR_RUNTIME_PACKAGES_DIR',
                'TOOL_POLICY_DEFAULTS_CONFIG_PATH', 'APP_VERSION', 'AGR_RUNTIME_PACKAGE_API_VERSION']:
        monkeypatch.delenv(key, raising=False)
    calls = []
    monkeypatch.setattr(module.op, 'get_bind', lambda: SimpleNamespace(execute=lambda sql, values: calls.append((str(sql), values))))
    module.upgrade()
    assert [values['tool_key'] for _, values in calls] == ['read_output_value', 'finalize_chat_output']
    for sql, values in calls:
        assert 'ON CONFLICT (tool_key) DO NOTHING' in sql
        assert values['category'] == 'Output'
        assert not values['allow_attach']
        assert not values['curator_visible']
        assert values['allow_execute']
        assert isinstance(json.loads(values['config']), dict)


def test_extraction_resolver_helpers_stop_being_inherited(monkeypatch):
    root = find_repo_root(Path(__file__))
    path = root / 'backend/alembic/versions/s6t7u8v9w0x1_stop_inheriting_extraction_resolver_helpers.py'
    spec = spec_from_file_location('resolver_inheritance_migration_test', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    # Chained after the model migration (q4f5a6b7c8d9 -> r5a6b7c8d9e0 -> here): one head.
    assert module.down_revision == 'r5a6b7c8d9e0'
    calls = []
    monkeypatch.setattr(module.op, 'get_bind', lambda: SimpleNamespace(execute=lambda sql, values: calls.append((str(sql), values))))

    module.upgrade()

    assert [values['tool_key'] for _, values in calls] == [
        'search_domain_field_terms', 'inspect_ontology_term', 'resolve_domain_field_term',
    ]
    for sql, values in calls:
        # Only the inheritance designation changes; the row is never inserted or replaced.
        assert sql.strip().startswith('UPDATE tool_policies')
        assert "'{system_managed_inheritance}'" in sql
        assert values['value'] == 'false'


def _topic_entity_tag_migration():
    root = find_repo_root(Path(__file__))
    path = root / 'backend/alembic/versions/a9b8c7d6e5f4_backfill_topic_entity_tag_tool_policies.py'
    spec = spec_from_file_location('topic_entity_tag_policy_migration_test', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_topic_entity_tag_policy_backfill_seeds_the_five_builder_tools(monkeypatch):
    module = _topic_entity_tag_migration()
    assert module.down_revision == 'b5c6d7e8f9a0'
    for key in ['AGR_RUNTIME_ROOT', 'AGR_RUNTIME_CONFIG_DIR', 'AGR_RUNTIME_PACKAGES_DIR',
                'TOOL_POLICY_DEFAULTS_CONFIG_PATH', 'APP_VERSION', 'AGR_RUNTIME_PACKAGE_API_VERSION']:
        monkeypatch.delenv(key, raising=False)
    calls = []
    monkeypatch.setattr(module.op, 'get_bind', lambda: SimpleNamespace(execute=lambda sql, values: calls.append((str(sql), values))))
    module.upgrade()
    assert [values['tool_key'] for _, values in calls] == [
        'stage_topic_entity_tag', 'patch_topic_entity_tag', 'discard_topic_entity_tag',
        'list_staged_topic_entity_tags', 'finalize_topic_entity_tag_extraction',
    ]
    for sql, values in calls:
        # Insert-only: a row an operator already has is left as it is.
        assert 'ON CONFLICT (tool_key) DO NOTHING' in sql
        assert 'UPDATE' not in sql
        # Same policy as the neighbouring builder tools, read from the package defaults.
        assert values['category'] == 'Runtime Helpers'
        assert not values['allow_attach']
        assert not values['curator_visible']
        assert values['allow_execute']
        assert json.loads(values['config']) == {'system_managed_inheritance': True}
    module.downgrade()
    assert len(calls) == 5


def test_topic_entity_tag_policy_backfill_inserts_nothing_without_the_owning_package(monkeypatch, tmp_path):
    """A deployment with only the core package migrates cleanly and gets no rows."""
    module = _topic_entity_tag_migration()
    (tmp_path / 'core').symlink_to(find_repo_root(Path(__file__)) / 'packages/core', target_is_directory=True)
    for key in ['AGR_RUNTIME_ROOT', 'AGR_RUNTIME_CONFIG_DIR',
                'TOOL_POLICY_DEFAULTS_CONFIG_PATH', 'APP_VERSION', 'AGR_RUNTIME_PACKAGE_API_VERSION']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('AGR_RUNTIME_PACKAGES_DIR', str(tmp_path))
    calls = []
    monkeypatch.setattr(module.op, 'get_bind', lambda: SimpleNamespace(execute=lambda sql, values: calls.append((str(sql), values))))
    module.upgrade()
    assert calls == []
