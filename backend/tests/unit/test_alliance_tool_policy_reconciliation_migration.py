"""Regression coverage for Alliance tool-policy seed reconciliation."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "a823b1c2d3e4_reconcile_alliance_tool_policy.py"
)
REPO_PACKAGES_DIR = BACKEND_DIR.parent / "packages"


def _load_migration():
    spec = spec_from_file_location("alliance_tool_policy_reconciliation", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_binding_discovery_tracks_installed_runtime_packages(monkeypatch, tmp_path):
    module = _load_migration()
    monkeypatch.setenv("APP_VERSION", "1.5.0")
    monkeypatch.setenv("AGR_RUNTIME_PACKAGE_API_VERSION", "1.0.0")

    assert module._alliance_api_binding_is_installed(REPO_PACKAGES_DIR) is True
    assert module._alliance_api_binding_is_installed(tmp_path) is False

    monkeypatch.setenv("APP_VERSION", "3.0.0")
    assert module._alliance_api_binding_is_installed(REPO_PACKAGES_DIR) is False


def test_binding_discovery_excludes_disabled_package(monkeypatch, tmp_path):
    module = _load_migration()
    monkeypatch.setenv("APP_VERSION", "1.5.0")
    monkeypatch.setenv("AGR_RUNTIME_PACKAGE_API_VERSION", "1.0.0")
    overrides_path = tmp_path / "overrides.yaml"
    overrides_path.write_text(
        """overrides_api_version: 1.0.0
disabled_packages:
  - agr.alliance
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGR_RUNTIME_OVERRIDES_PATH", str(overrides_path))

    assert module._alliance_api_binding_is_installed(REPO_PACKAGES_DIR) is False


def test_upgrade_deletes_stale_policy_without_binding(monkeypatch):
    module = _load_migration()
    executed = []
    monkeypatch.setattr(module, "_alliance_api_binding_is_installed", lambda: False)
    monkeypatch.setattr(module, "op", SimpleNamespace(execute=executed.append))

    module.upgrade()

    assert len(executed) == 1
    assert "DELETE FROM tool_policies" in str(executed[0])
    assert executed[0].compile().params == {"tool_key": "alliance_api_call"}


def test_upgrade_preserves_policy_when_binding_is_installed(monkeypatch):
    module = _load_migration()
    executed = []
    monkeypatch.setattr(module, "_alliance_api_binding_is_installed", lambda: True)
    monkeypatch.setattr(module, "op", SimpleNamespace(execute=executed.append))

    module.upgrade()

    assert executed == []
