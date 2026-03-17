"""Tests for the tracked shipped runtime package agent catalogs."""

from pathlib import Path

from . import find_repo_root
from src.lib.packages.manifest_loader import load_package_manifest
from src.lib.packages.models import ExportKind

REPO_ROOT = find_repo_root(Path(__file__))
CONFIG_AGENTS_DIR = REPO_ROOT / "config" / "agents"
CONFIG_DIR = REPO_ROOT / "config"
CORE_PACKAGE_DIR = REPO_ROOT / "packages" / "core"
ALLIANCE_PACKAGE_DIR = REPO_ROOT / "packages" / "alliance"
CORE_AGENTS_DIR = CORE_PACKAGE_DIR / "agents"
ALLIANCE_AGENTS_DIR = ALLIANCE_PACKAGE_DIR / "agents"
CORE_CONFIG_DIR = CORE_PACKAGE_DIR / "config"
RUNTIME_CONFIG_FILES = (
    "models.yaml",
    "providers.yaml",
    "tool_policy_defaults.yaml",
)
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


def _expected_agent_exports(agents_dir: Path) -> set[tuple[ExportKind, str, str]]:
    exports: set[tuple[ExportKind, str, str]] = set()

    for agent_dir in _iter_shipped_agent_dirs(agents_dir):
        agent_name = agent_dir.name
        exports.add((ExportKind.AGENT, agent_name, f"agents/{agent_name}"))
        exports.add(
            (ExportKind.PROMPT, f"{agent_name}.system", f"agents/{agent_name}/prompt.yaml")
        )

        schema_path = agent_dir / "schema.py"
        if schema_path.exists():
            exports.add(
                (ExportKind.SCHEMA, f"{agent_name}.schema", f"agents/{agent_name}/schema.py")
            )

        rules_dir = agent_dir / "group_rules"
        if rules_dir.exists():
            for rule_path in sorted(rules_dir.glob("*.yaml")):
                exports.add(
                    (
                        ExportKind.GROUP_RULE,
                        f"{agent_name}.{rule_path.stem.upper()}",
                        f"agents/{agent_name}/group_rules/{rule_path.name}",
                    )
                )

    return exports


def _assert_package_agents_match_config(package_agents_dir: Path, agent_names: set[str]) -> None:
    actual_agent_names = {
        agent_dir.name
        for agent_dir in _iter_shipped_agent_dirs(package_agents_dir)
    }
    assert actual_agent_names == agent_names

    for agent_name in sorted(agent_names):
        config_agent_dir = CONFIG_AGENTS_DIR / agent_name
        package_agent_dir = package_agents_dir / agent_name
        expected_files = _iter_source_files(config_agent_dir)
        actual_files = _iter_source_files(package_agent_dir)

        assert actual_files == expected_files

        for relative_path in sorted(expected_files):
            config_path = config_agent_dir / relative_path
            package_path = package_agent_dir / relative_path
            assert package_path.read_text(encoding="utf-8") == config_path.read_text(
                encoding="utf-8"
            )


def test_core_package_mirrors_shipped_supervisor_files():
    assert not (CORE_AGENTS_DIR / "_examples").exists()
    _assert_package_agents_match_config(CORE_AGENTS_DIR, CORE_AGENT_NAMES)


def test_alliance_package_mirrors_shipped_specialist_files():
    shipped_specialists = {
        agent_dir.name
        for agent_dir in _iter_shipped_agent_dirs(CONFIG_AGENTS_DIR)
        if agent_dir.name not in CORE_AGENT_NAMES
    }

    _assert_package_agents_match_config(ALLIANCE_AGENTS_DIR, shipped_specialists)


def test_core_package_manifest_exports_runtime_foundation_and_supervisor_assets():
    manifest = load_package_manifest(CORE_PACKAGE_DIR / "package.yaml")

    expected_exports = {
        (ExportKind.TOOL_BINDING, "default", "tools/bindings.yaml"),
        (ExportKind.MODEL, "default_models", "config/models.yaml"),
        (ExportKind.PROVIDER, "default_providers", "config/providers.yaml"),
        (
            ExportKind.TOOL_POLICY_DEFAULTS,
            "default_tool_policies",
            "config/tool_policy_defaults.yaml",
        ),
    }
    expected_exports |= _expected_agent_exports(CORE_AGENTS_DIR)

    actual_exports = {
        (export.kind, export.name, export.path)
        for export in manifest.exports
    }

    assert actual_exports == expected_exports


def test_alliance_package_manifest_exports_all_shipped_specialist_assets():
    manifest = load_package_manifest(ALLIANCE_PACKAGE_DIR / "package.yaml")

    actual_exports = {
        (export.kind, export.name, export.path)
        for export in manifest.exports
    }

    assert actual_exports == _expected_agent_exports(ALLIANCE_AGENTS_DIR)


def test_core_package_mirrors_shipped_runtime_config_files():
    for filename in RUNTIME_CONFIG_FILES:
        config_path = CONFIG_DIR / filename
        core_path = CORE_CONFIG_DIR / filename

        assert core_path.exists()
        assert core_path.read_text(encoding="utf-8") == config_path.read_text(
            encoding="utf-8"
        )
