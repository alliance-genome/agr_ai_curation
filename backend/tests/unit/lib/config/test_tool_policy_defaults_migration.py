"""Regression tests for the tool policy seed migration helper."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

import pytest
import yaml

from ..packages import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__))
MIGRATION_PATH = (
    REPO_ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "z8a9b0c1d2e3_add_tool_policies_table.py"
)


def _load_migration_module(monkeypatch):
    dummy_loader = types.ModuleType("src.lib.config.tool_policy_defaults_loader")
    monkeypatch.setitem(sys.modules, "src.lib.config.tool_policy_defaults_loader", dummy_loader)

    spec = spec_from_file_location("tool_policy_defaults_migration_test", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _clear_runtime_env(monkeypatch) -> None:
    for env_var in (
        "AGR_RUNTIME_ROOT",
        "AGR_RUNTIME_CONFIG_DIR",
        "AGR_RUNTIME_PACKAGES_DIR",
        "TOOL_POLICY_DEFAULTS_CONFIG_PATH",
        "APP_VERSION",
        "AGR_RUNTIME_PACKAGE_API_VERSION",
    ):
        monkeypatch.delenv(env_var, raising=False)


def test_migration_loads_repo_defaults_without_live_loader_import(monkeypatch):
    _clear_runtime_env(monkeypatch)

    module = _load_migration_module(monkeypatch)
    policies = module._load_default_tool_policies()

    assert "search_document" in policies
    assert policies["search_document"]["display_name"] == "Search Document"


def test_migration_merges_package_defaults_before_runtime_override(tmp_path, monkeypatch):
    _clear_runtime_env(monkeypatch)

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("APP_VERSION", "1.5.0")
    monkeypatch.setenv("AGR_RUNTIME_PACKAGE_API_VERSION", "1.0.0")

    alpha_package = runtime_root / "packages" / "pkg-zeta"
    _write_yaml(
        alpha_package / "package.yaml",
        {
            "package_id": "demo.alpha",
            "package_api_version": "1.0.0",
            "min_runtime_version": "1.0.0",
            "max_runtime_version": "2.0.0",
            "exports": [
                {
                    "kind": "tool_policy_defaults",
                    "name": "default_tool_policies",
                    "path": "config/tool_policy_defaults.yaml",
                }
            ],
        },
    )
    _write_yaml(
        alpha_package / "config" / "tool_policy_defaults.yaml",
        {
            "tool_policies": {
                "package_collision": {
                    "display_name": "Alpha Package Collision",
                },
                "shared": {
                    "display_name": "Alpha Shared",
                },
            }
        },
    )

    beta_package = runtime_root / "packages" / "pkg-alpha"
    _write_yaml(
        beta_package / "package.yaml",
        {
            "package_id": "demo.beta",
            "package_api_version": "1.0.0",
            "min_runtime_version": "1.0.0",
            "max_runtime_version": "2.0.0",
            "exports": [
                {
                    "kind": "tool_policy_defaults",
                    "name": "default_tool_policies",
                    "path": "config/tool_policy_defaults.yaml",
                }
            ],
        },
    )
    _write_yaml(
        beta_package / "config" / "tool_policy_defaults.yaml",
        {
            "tool_policies": {
                "package_collision": {
                    "display_name": "Beta Package Collision",
                },
                "shared": {
                    "display_name": "Beta Shared",
                },
            }
        },
    )

    _write_yaml(
        runtime_root / "config" / "tool_policy_defaults.yaml",
        {
            "tool_policies": {
                "shared": {
                    "display_name": "Runtime Shared",
                    "allow_execute": False,
                },
                "runtime_only": {
                    "display_name": "Runtime Only",
                },
            }
        },
    )

    module = _load_migration_module(monkeypatch)
    policies = module._load_default_tool_policies()

    assert policies["package_collision"]["display_name"] == "Beta Package Collision"
    assert policies["shared"]["display_name"] == "Runtime Shared"
    assert policies["shared"]["allow_execute"] is False
    assert policies["runtime_only"]["display_name"] == "Runtime Only"
