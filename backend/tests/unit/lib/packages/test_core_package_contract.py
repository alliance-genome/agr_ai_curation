"""Tests for the minimal agr.core runtime package contract."""

from pathlib import Path

import yaml

from . import find_repo_root
from src.lib.packages.manifest_loader import load_package_manifest
from src.lib.packages.models import ExportKind

REPO_ROOT = find_repo_root(Path(__file__))
CORE_PACKAGE_DIR = REPO_ROOT / "packages" / "core"
ALLIANCE_PACKAGE_DIR = REPO_ROOT / "packages" / "alliance"
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


def test_core_package_contains_only_generic_runtime_agent_assets():
    actual_agent_names = {
        agent_dir.name for agent_dir in _iter_shipped_agent_dirs(CORE_AGENTS_DIR)
    }

    assert actual_agent_names == {"curation_handoff", "curation_prep", "supervisor"}
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
        (
            ExportKind.AGENT_STUDIO_PROMPT,
            "system",
            "config/agent_studio_system_prompt.md",
        ),
        (ExportKind.AGENT, "curation_handoff", "agents/curation_handoff"),
        (
            ExportKind.PROMPT,
            "curation_handoff.system",
            "agents/curation_handoff/prompt.yaml",
        ),
        (ExportKind.AGENT, "curation_prep", "agents/curation_prep"),
        (
            ExportKind.PROMPT,
            "curation_prep.system",
            "agents/curation_prep/prompt.yaml",
        ),
        (ExportKind.AGENT, "supervisor", "agents/supervisor"),
        (ExportKind.PROMPT, "supervisor.system", "agents/supervisor/prompt.yaml"),
    }
    assert not any(export.kind is ExportKind.TOOL_BINDING for export in manifest.exports)


def test_alliance_package_declares_core_dependency_and_supervisor_rule_exports():
    manifest = load_package_manifest(ALLIANCE_PACKAGE_DIR / "package.yaml")

    assert [dependency.package_id for dependency in manifest.dependencies] == [
        "agr.core"
    ]
    assert {
        (export.kind, export.name, export.path)
        for export in manifest.exports
        if export.name.startswith("supervisor.")
    } == {
        (
            ExportKind.GROUP_RULE,
            "supervisor.MGI",
            "group_rules/supervisor/mgi.yaml",
        ),
        (
            ExportKind.GROUP_RULE,
            "supervisor.RGD",
            "group_rules/supervisor/rgd.yaml",
        ),
    }


def test_core_package_and_supervisor_override_omit_alliance_group_rule_content():
    source_roots = (
        CORE_PACKAGE_DIR,
        REPO_ROOT / "config" / "agents" / "supervisor",
    )
    forbidden_fragments = (
        "MGI",
        "RGD",
        "Mus musculus",
        "Rattus norvegicus",
        "Allele Specialist",
    )

    for source_root in source_roots:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in source_root.rglob("*")
            if path.is_file()
        )
        for fragment in forbidden_fragments:
            assert fragment not in combined, f"{fragment!r} found below {source_root}"


def test_core_package_mirrors_shipped_runtime_config_files():
    config_dir = REPO_ROOT / "config"

    for filename in RUNTIME_CONFIG_FILES:
        config_path = config_dir / filename
        core_path = CORE_CONFIG_DIR / filename

        assert core_path.exists()
        assert core_path.read_text(encoding="utf-8") == config_path.read_text(
            encoding="utf-8"
        )


def test_shipped_catalog_keeps_gpt56_default_and_approved_openrouter_routes():
    runtime_catalog = yaml.safe_load(
        (REPO_ROOT / "config" / "models.yaml").read_text(encoding="utf-8")
    )["models"]
    package_catalog = yaml.safe_load(
        (CORE_CONFIG_DIR / "models.yaml").read_text(encoding="utf-8")
    )["models"]

    assert runtime_catalog == package_catalog
    assert [model["model_id"] for model in runtime_catalog] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "deepseek/deepseek-v4-pro-0813",
        "google/gemini-3.7-flash",
        "qwen/qwen3.8-27b",
    ]
    assert [model["default"] for model in runtime_catalog] == [
        True,
        False,
        False,
        False,
        False,
    ]
    assert all(model["provider"] == "openrouter" for model in runtime_catalog[2:])
    for model in runtime_catalog[:2]:
        assert model["reasoning_options"] == ["low", "medium", "high", "xhigh"]
        assert model["default_reasoning"] == "medium"
