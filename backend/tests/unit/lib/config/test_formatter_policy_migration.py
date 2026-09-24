"""Only missing formatter policy rows are seeded; explicit decisions survive."""
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
    assert module.down_revision == 'q4f5a6b7c8d9'
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
