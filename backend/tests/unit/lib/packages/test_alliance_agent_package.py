"""Tests for the shipped agr.alliance specialist catalog and source mirror."""

from pathlib import Path

from . import find_repo_root
from src.lib.packages.manifest_loader import load_package_manifest
from src.lib.packages.models import ExportKind

REPO_ROOT = find_repo_root(Path(__file__))
CONFIG_AGENTS_DIR = REPO_ROOT / "config" / "agents"
ALLIANCE_PACKAGE_DIR = REPO_ROOT / "packages" / "alliance"
ALLIANCE_AGENTS_DIR = ALLIANCE_PACKAGE_DIR / "agents"
CORE_AGENT_NAMES = {"supervisor"}


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


def test_alliance_package_mirrors_shipped_specialist_agent_files():
    shipped_agents = _iter_shipped_agent_dirs(CONFIG_AGENTS_DIR)
    expected_agent_names = {
        agent_dir.name for agent_dir in shipped_agents if agent_dir.name not in CORE_AGENT_NAMES
    }
    actual_agent_names = {
        agent_dir.name
        for agent_dir in _iter_shipped_agent_dirs(ALLIANCE_AGENTS_DIR)
    }

    assert actual_agent_names == expected_agent_names
    assert not (ALLIANCE_AGENTS_DIR / "_examples").exists()

    for config_agent_dir in shipped_agents:
        if config_agent_dir.name in CORE_AGENT_NAMES:
            continue
        alliance_agent_dir = ALLIANCE_AGENTS_DIR / config_agent_dir.name
        expected_files = _iter_source_files(config_agent_dir)
        actual_files = _iter_source_files(alliance_agent_dir)

        assert actual_files == expected_files

        for relative_path in sorted(expected_files):
            config_path = config_agent_dir / relative_path
            alliance_path = alliance_agent_dir / relative_path
            assert alliance_path.read_text(encoding="utf-8") == config_path.read_text(
                encoding="utf-8"
            )


def test_alliance_package_manifest_exports_shipped_specialist_catalog():
    manifest = load_package_manifest(ALLIANCE_PACKAGE_DIR / "package.yaml")

    assert manifest.package_id == "agr.alliance"
    assert manifest.display_name == "AGR Alliance Package"

    expected_exports = {
        (ExportKind.TOOL_BINDING, "default", "tools/bindings.yaml"),
    }
    for agent_dir in _iter_shipped_agent_dirs(CONFIG_AGENTS_DIR):
        if agent_dir.name in CORE_AGENT_NAMES:
            continue

        agent_name = agent_dir.name
        expected_exports.add((ExportKind.AGENT, agent_name, f"agents/{agent_name}"))
        expected_exports.add(
            (ExportKind.PROMPT, f"{agent_name}.system", f"agents/{agent_name}/prompt.yaml")
        )

        schema_path = agent_dir / "schema.py"
        if schema_path.exists():
            expected_exports.add(
                (ExportKind.SCHEMA, f"{agent_name}.schema", f"agents/{agent_name}/schema.py")
            )

        rules_dir = agent_dir / "group_rules"
        if rules_dir.exists():
            for rule_path in sorted(rules_dir.glob("*.yaml")):
                expected_exports.add(
                    (
                        ExportKind.GROUP_RULE,
                        f"{agent_name}.{rule_path.stem.upper()}",
                        f"agents/{agent_name}/group_rules/{rule_path.name}",
                    )
                )

    actual_exports = {
        (export.kind, export.name, export.path)
        for export in manifest.exports
    }

    assert actual_exports == expected_exports
