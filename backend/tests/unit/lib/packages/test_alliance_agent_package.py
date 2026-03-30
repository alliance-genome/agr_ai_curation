"""Tests for the shipped agr.alliance specialist catalog and source mirror."""

from pathlib import Path

import yaml

from . import find_repo_root
from src.lib.packages.manifest_loader import load_package_manifest
from src.lib.packages.models import ExportKind

REPO_ROOT = find_repo_root(Path(__file__))
ALLIANCE_PACKAGE_DIR = REPO_ROOT / "packages" / "alliance"
ALLIANCE_AGENTS_DIR = ALLIANCE_PACKAGE_DIR / "agents"


def _iter_shipped_agent_dirs(root: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_dir() and not path.name.startswith("_")
    )


def _iter_source_files(root: Path) -> set[Path]:
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    }


def _raw_manifest_agent_bundles() -> list[dict]:
    manifest_data = yaml.safe_load((ALLIANCE_PACKAGE_DIR / "package.yaml").read_text(encoding="utf-8"))
    return list(manifest_data.get("agent_bundles", []) or [])


def test_alliance_package_ships_manifest_agent_bundle_files():
    bundles = _raw_manifest_agent_bundles()
    expected_agent_names = {bundle["name"] for bundle in bundles}
    actual_agent_names = {
        agent_dir.name
        for agent_dir in _iter_shipped_agent_dirs(ALLIANCE_AGENTS_DIR)
    }

    assert actual_agent_names == expected_agent_names
    assert not (ALLIANCE_AGENTS_DIR / "_examples").exists()

    for bundle in bundles:
        agent_name = str(bundle["name"])
        alliance_agent_dir = ALLIANCE_AGENTS_DIR / agent_name
        actual_files = _iter_source_files(alliance_agent_dir)
        expected_files = {
            Path("agent.yaml"),
            Path("prompt.yaml"),
        }
        if bundle.get("has_schema"):
            expected_files.add(Path("schema.py"))
        for rule_name in bundle.get("group_rules", []):
            expected_files.add(Path("group_rules") / f"{rule_name}.yaml")
        assert actual_files == expected_files


def test_alliance_package_manifest_exports_shipped_specialist_catalog():
    manifest = load_package_manifest(ALLIANCE_PACKAGE_DIR / "package.yaml")

    assert manifest.package_id == "agr.alliance"
    assert manifest.display_name == "Alliance Defaults"

    expected_exports = {
        (ExportKind.TOOL_BINDING, "default", "tools/bindings.yaml"),
        (
            ExportKind.CURATION_ADAPTER,
            "default",
            "python/src/agr_ai_curation_alliance/curation_adapters.py",
        ),
    }
    for bundle in _raw_manifest_agent_bundles():
        agent_name = str(bundle["name"])
        expected_exports.add((ExportKind.AGENT, agent_name, f"agents/{agent_name}"))
        expected_exports.add(
            (ExportKind.PROMPT, f"{agent_name}.system", f"agents/{agent_name}/prompt.yaml")
        )

        if bundle.get("has_schema"):
            expected_exports.add(
                (ExportKind.SCHEMA, f"{agent_name}.schema", f"agents/{agent_name}/schema.py")
            )

        for rule_name in bundle.get("group_rules", []):
            expected_exports.add(
                (
                    ExportKind.GROUP_RULE,
                    f"{agent_name}.{str(rule_name).upper()}",
                    f"agents/{agent_name}/group_rules/{rule_name}.yaml",
                )
                )

    actual_exports = {
        (export.kind, export.name, export.path)
        for export in manifest.exports
    }

    assert actual_exports == expected_exports
