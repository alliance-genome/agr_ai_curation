"""Tests for the minimal agr.core runtime package contract."""

from pathlib import Path

from . import find_repo_root
from src.lib.packages.manifest_loader import load_package_manifest
from src.lib.packages.models import ExportKind

REPO_ROOT = find_repo_root(Path(__file__))
CORE_PACKAGE_DIR = REPO_ROOT / "packages" / "core"


def test_core_package_manifest_exports_foundation_runtime_assets_only():
    manifest = load_package_manifest(CORE_PACKAGE_DIR / "package.yaml")

    actual_exports = {
        (export.kind, export.name, export.path)
        for export in manifest.exports
    }

    assert actual_exports == {
        (ExportKind.TOOL_BINDING, "default", "tools/bindings.yaml"),
        (ExportKind.MODEL, "default_models", "config/models.yaml"),
        (ExportKind.PROVIDER, "default_providers", "config/providers.yaml"),
        (
            ExportKind.TOOL_POLICY_DEFAULTS,
            "default_tool_policies",
            "config/tool_policy_defaults.yaml",
        ),
    }
    assert {
        export.kind
        for export in manifest.exports
    }.isdisjoint(
        {
            ExportKind.AGENT,
            ExportKind.PROMPT,
            ExportKind.SCHEMA,
            ExportKind.GROUP_RULE,
        }
    )
    assert all(not export.path.startswith("agents/") for export in manifest.exports)
