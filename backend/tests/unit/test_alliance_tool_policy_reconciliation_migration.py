"""Regression coverage for Alliance tool-policy seed reconciliation."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest


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


def test_binding_discovery_tracks_installed_runtime_packages(monkeypatch):
    module = _load_migration()
    monkeypatch.setenv("APP_VERSION", "1.5.0")
    monkeypatch.setenv("AGR_RUNTIME_PACKAGE_API_VERSION", "1.0.0")

    installed = module._installed_tool_binding_ids(REPO_PACKAGES_DIR)
    assert module.ALLIANCE_OWNED_TOOL_IDS <= installed

    monkeypatch.setenv("APP_VERSION", "3.0.0")
    assert module._installed_tool_binding_ids(REPO_PACKAGES_DIR) == set()


@pytest.mark.parametrize("packages_dir_exists", [False, True])
def test_binding_discovery_rejects_undiscoverable_packages(
    monkeypatch, tmp_path, packages_dir_exists
):
    module = _load_migration()
    packages_dir = tmp_path / "packages"
    if packages_dir_exists:
        packages_dir.mkdir()

    expected_message = (
        "No package manifests discovered"
        if packages_dir_exists
        else "Runtime packages directory not found"
    )
    with pytest.raises(FileNotFoundError, match=expected_message):
        module._installed_tool_binding_ids(packages_dir)


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

    installed = module._installed_tool_binding_ids(REPO_PACKAGES_DIR)
    assert module.ALLIANCE_OWNED_TOOL_IDS.isdisjoint(installed)


def test_upgrade_deletes_all_stale_moved_policies(monkeypatch):
    module = _load_migration()
    executed = []
    monkeypatch.setattr(module, "_installed_tool_binding_ids", lambda: {"search_document"})
    monkeypatch.setattr(module, "op", SimpleNamespace(execute=executed.append))

    module.upgrade()

    assert len(executed) == 1
    assert "DELETE FROM tool_policies" in str(executed[0])
    assert executed[0].compile().params == {
        "tool_keys": sorted(module.ALLIANCE_OWNED_TOOL_IDS - {"search_document"})
    }


def test_upgrade_preserves_policies_when_bindings_are_installed(monkeypatch):
    module = _load_migration()
    executed = []
    monkeypatch.setattr(
        module,
        "_installed_tool_binding_ids",
        lambda: set(module.ALLIANCE_OWNED_TOOL_IDS),
    )
    monkeypatch.setattr(module, "op", SimpleNamespace(execute=executed.append))

    module.upgrade()

    assert executed == []
