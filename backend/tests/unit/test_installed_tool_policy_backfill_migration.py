"""Installed default backfill must not overwrite explicit policy decisions."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace


def _load_migration():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "n1c2d3e4f5a6_backfill_installed_tool_policies.py"
    spec = spec_from_file_location("installed_policy_backfill_test", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_defaults_require_installed_binding(monkeypatch):
    module = _load_migration()
    seeds = SimpleNamespace(_load_default_tool_policies=lambda: {
        "read_chunk": {"allow_execute": True},
        "uninstalled_tool": {"allow_execute": True},
    })
    bindings = SimpleNamespace(_installed_tool_binding_ids=lambda: {"read_chunk"})
    monkeypatch.setattr(module, "_migration_helper", lambda filename: seeds if filename.startswith("z8") else bindings)
    assert module._installed_defaults() == {"read_chunk": {"allow_execute": True}}


def test_real_package_defaults_include_read_chunk(monkeypatch):
    module = _load_migration()
    for name in ("AGR_RUNTIME_ROOT", "AGR_RUNTIME_PACKAGES_DIR", "AGR_RUNTIME_CONFIG_DIR", "TOOL_POLICY_DEFAULTS_CONFIG_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_VERSION", "1.5.0")
    monkeypatch.setenv("AGR_RUNTIME_PACKAGE_API_VERSION", "1.0.0")
    policy = module._installed_defaults()["read_chunk"]
    assert policy["curator_visible"] is True
    assert policy["allow_attach"] is True
    assert policy["allow_execute"] is True


def test_upgrade_is_insert_only_and_downgrade_never_deletes(monkeypatch):
    module = _load_migration()
    calls = []
    connection = SimpleNamespace(execute=lambda statement, parameters: calls.append((str(statement), parameters)))
    monkeypatch.setattr(module, "op", SimpleNamespace(get_bind=lambda: connection))
    policy = {
        "display_name": "Read Chunk", "description": "Read evidence spans",
        "category": "Document", "curator_visible": True,
        "allow_attach": True, "allow_execute": True, "config": {},
    }
    monkeypatch.setattr(module, "_installed_defaults", lambda: {"read_chunk": policy})
    module.upgrade()
    assert len(calls) == 1
    statement, parameters = calls[0]
    assert "ON CONFLICT (tool_key) DO NOTHING" in statement
    assert "UPDATE" not in statement
    assert parameters == {**policy, "tool_key": "read_chunk", "config": "{}"}
    module.downgrade()
    assert len(calls) == 1
