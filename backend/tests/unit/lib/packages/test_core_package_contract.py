"""Tests for the minimal agr.core runtime package contract."""

from pathlib import Path

from . import find_repo_root
from src.lib.packages.manifest_loader import load_package_manifest
from src.lib.packages.models import ExportKind

REPO_ROOT = find_repo_root(Path(__file__))
CORE_PACKAGE_DIR = REPO_ROOT / "packages" / "core"
CORE_AGENTS_DIR = CORE_PACKAGE_DIR / "agents"
CORE_CONFIG_DIR = CORE_PACKAGE_DIR / "config"
RUNTIME_CONFIG_FILES = (
    "models.yaml",
    "providers.yaml",
    "tool_policy_defaults.yaml",
)


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


def test_core_package_contains_only_minimal_supervisor_runtime_assets():
    actual_agent_names = {
        agent_dir.name for agent_dir in _iter_shipped_agent_dirs(CORE_AGENTS_DIR)
    }

    assert actual_agent_names == {"supervisor"}
    assert not (CORE_PACKAGE_DIR / "tools").exists()
    assert not (CORE_AGENTS_DIR / "README.md").exists()
    assert _iter_source_files(CORE_PACKAGE_DIR / "python" / "src" / "agr_ai_curation_core") == {
        Path("__init__.py")
    }


def test_core_package_manifest_exports_foundation_runtime_assets_only():
    manifest = load_package_manifest(CORE_PACKAGE_DIR / "package.yaml")

    actual_exports = {
        (export.kind, export.name, export.path)
        for export in manifest.exports
    }

    assert actual_exports == {
        (ExportKind.MODEL, "default_models", "config/models.yaml"),
        (ExportKind.PROVIDER, "default_providers", "config/providers.yaml"),
        (
            ExportKind.TOOL_POLICY_DEFAULTS,
            "default_tool_policies",
            "config/tool_policy_defaults.yaml",
        ),
        (ExportKind.AGENT, "supervisor", "agents/supervisor"),
        (ExportKind.PROMPT, "supervisor.system", "agents/supervisor/prompt.yaml"),
        (
            ExportKind.GROUP_RULE,
            "supervisor.MGI",
            "agents/supervisor/group_rules/mgi.yaml",
        ),
        (
            ExportKind.GROUP_RULE,
            "supervisor.RGD",
            "agents/supervisor/group_rules/rgd.yaml",
        ),
    }
    assert not any(export.kind is ExportKind.TOOL_BINDING for export in manifest.exports)


def test_core_package_mirrors_shipped_runtime_config_files():
    config_dir = REPO_ROOT / "config"

    for filename in RUNTIME_CONFIG_FILES:
        config_path = config_dir / filename
        core_path = CORE_CONFIG_DIR / filename

        assert core_path.exists()
        assert core_path.read_text(encoding="utf-8") == config_path.read_text(
            encoding="utf-8"
        )
