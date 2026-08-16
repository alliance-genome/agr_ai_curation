"""Tests for explicit package-owned Agent Studio prompt selection."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from src.lib.packages.agent_studio_prompt_loader import (
    AgentStudioPromptLoadError,
    load_installed_agent_studio_prompt,
    resolve_agent_studio_prompt,
)
from src.lib.packages import agent_studio_prompt_loader as prompt_loader
from src.lib.packages.manifest_loader import load_runtime_overrides
from src.lib.packages.registry import load_package_registry

from . import find_repo_root


REPO_ROOT = find_repo_root(Path(__file__))
PACKAGES_DIR = REPO_ROOT / "packages"


def test_core_only_profile_resolves_neutral_package_prompt():
    registry = load_package_registry(PACKAGES_DIR)
    core_package = registry.get_package("agr.core")
    assert core_package is not None

    loaded = resolve_agent_studio_prompt(
        replace(registry, loaded_packages=(core_package,))
    )

    assert loaded.source.package_id == "agr.core"
    assert loaded.source.export_name == "system"
    assert loaded.source.prompt_path == (
        PACKAGES_DIR / "core" / "config" / "agent_studio_system_prompt.md"
    ).resolve()
    assert "currently\ninstalled AI curation packages" in loaded.content
    assert "Alliance of Genome Resources" not in loaded.content
    assert "{{USER_GREETING}}" in loaded.content
    assert "{{PACKAGE_DIAGNOSTIC_TOOLS}}" in loaded.content


def test_shipped_alliance_profile_uses_explicit_runtime_selection():
    alliance_overrides_path = (
        PACKAGES_DIR / "alliance" / "config" / "runtime_overrides.yaml"
    )
    assert (REPO_ROOT / "config" / "overrides.yaml").read_text(
        encoding="utf-8"
    ) == alliance_overrides_path.read_text(encoding="utf-8")

    loaded = load_installed_agent_studio_prompt(
        PACKAGES_DIR,
        overrides_path=alliance_overrides_path,
    )

    assert loaded.source.package_id == "agr.alliance"
    assert loaded.source.export_name == "system"
    assert loaded.source.prompt_path == (
        PACKAGES_DIR / "alliance" / "config" / "agent_studio_system_prompt.md"
    ).resolve()
    assert "Alliance of Genome Resources" in loaded.content
    assert "domain envelopes are the semantic source of truth" in loaded.content


def test_multiple_prompt_exports_without_selection_fail_with_full_provenance():
    registry = load_package_registry(PACKAGES_DIR)

    with pytest.raises(AgentStudioPromptLoadError) as exc_info:
        resolve_agent_studio_prompt(registry)

    message = str(exc_info.value)
    assert "Multiple active packages export" in message
    assert "package 'agr.core'" in message
    assert "package 'agr.alliance'" in message
    assert "packages/core/package.yaml" in message
    assert "packages/alliance/package.yaml" in message
    assert "config/agent_studio_system_prompt.md" in message
    assert "export_kind 'agent_studio_prompt'" in message


def test_missing_prompt_file_fails_with_package_export_and_path(tmp_path: Path):
    package_dir = tmp_path / "packages" / "org.example"
    package_dir.mkdir(parents=True)
    (package_dir / "package.yaml").write_text(
        """\
package_id: org.example
display_name: Example
version: 1.0.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: python/src/org_example
requirements_file: requirements/runtime.txt
exports:
  - kind: agent_studio_prompt
    name: system
    path: config/missing.md
""",
        encoding="utf-8",
    )
    registry = load_package_registry(package_dir.parent)

    with pytest.raises(AgentStudioPromptLoadError) as exc_info:
        resolve_agent_studio_prompt(registry)

    message = str(exc_info.value)
    assert "package 'org.example'" in message
    assert str(package_dir / "package.yaml") in message
    assert "export 'system'" in message
    assert str(package_dir / "config" / "missing.md") in message
    assert "does not exist or is not a file" in message


def test_disabled_alliance_package_leaves_core_prompt_active(tmp_path: Path):
    overrides_path = tmp_path / "overrides.yaml"
    overrides_path.write_text(
        """\
overrides_api_version: 1.0.0
disabled_packages: [agr.alliance]
""",
        encoding="utf-8",
    )
    registry = load_package_registry(PACKAGES_DIR)

    loaded = resolve_agent_studio_prompt(
        registry,
        runtime_overrides=load_runtime_overrides(overrides_path),
    )

    assert loaded.source.package_id == "agr.core"


def test_explicit_selection_must_match_the_only_active_prompt(tmp_path: Path):
    overrides_path = tmp_path / "overrides.yaml"
    overrides_path.write_text(
        """\
overrides_api_version: 1.0.0
selections:
  - export_kind: agent_studio_prompt
    name: system
    package_id: agr.alliance
""",
        encoding="utf-8",
    )
    registry = load_package_registry(PACKAGES_DIR)
    core_package = registry.get_package("agr.core")
    assert core_package is not None

    with pytest.raises(AgentStudioPromptLoadError) as exc_info:
        resolve_agent_studio_prompt(
            replace(registry, loaded_packages=(core_package,)),
            runtime_overrides=load_runtime_overrides(overrides_path),
        )

    message = str(exc_info.value)
    assert "agr.alliance:system" in message
    assert "not an active candidate" in message
    assert "package 'agr.core'" in message


def test_installed_prompt_is_cached_per_resolved_runtime_profile(
    monkeypatch,
    tmp_path: Path,
):
    packages_dir = tmp_path / "packages"
    shutil.copytree(PACKAGES_DIR / "core", packages_dir / "core")
    overrides_path = tmp_path / "config" / "overrides.yaml"
    overrides_path.parent.mkdir()
    shutil.copy2(
        PACKAGES_DIR / "core" / "config" / "runtime_overrides.yaml",
        overrides_path,
    )
    real_load_registry = prompt_loader.load_package_registry
    load_count = 0

    def count_registry_load(*args, **kwargs):
        nonlocal load_count
        load_count += 1
        return real_load_registry(*args, **kwargs)

    monkeypatch.setattr(prompt_loader, "load_package_registry", count_registry_load)

    first = load_installed_agent_studio_prompt(
        packages_dir,
        overrides_path=overrides_path,
    )
    second = load_installed_agent_studio_prompt(
        packages_dir,
        overrides_path=overrides_path,
    )

    assert second is first
    assert load_count == 1
