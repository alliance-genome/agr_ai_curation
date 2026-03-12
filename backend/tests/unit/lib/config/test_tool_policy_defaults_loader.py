"""Tests for config.tool_policy_defaults_loader."""

from pathlib import Path

import pytest

from src.lib.config import package_default_sources
from src.lib.config.tool_policy_defaults_loader import load_tool_policy_defaults


def _write_tool_policy_package(
    packages_dir: Path,
    *,
    directory_name: str,
    package_id: str,
    policies_text: str,
) -> None:
    package_dir = packages_dir / directory_name
    (package_dir / "config").mkdir(parents=True)
    (package_dir / "requirements").mkdir(parents=True)
    (package_dir / "requirements" / "runtime.txt").write_text("", encoding="utf-8")
    (package_dir / "package.yaml").write_text(
        f"""package_id: {package_id}
display_name: {package_id} package
version: 1.0.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: src/{package_id.replace('.', '_')}
requirements_file: requirements/runtime.txt
exports:
  - kind: tool_policy_defaults
    name: default_tool_policies
    path: config/tool_policy_defaults.yaml
    description: Default tool policies
""",
        encoding="utf-8",
    )
    (package_dir / "config" / "tool_policy_defaults.yaml").write_text(
        policies_text.strip() + "\n",
        encoding="utf-8",
    )


def test_load_tool_policy_defaults_uses_sorted_package_order_for_collisions(tmp_path: Path):
    packages_dir = tmp_path / "packages"
    _write_tool_policy_package(
        packages_dir,
        directory_name="agr-base",
        package_id="agr.base",
        policies_text="""
tool_policies:
  shared_tool:
    display_name: Base Shared
    category: Database
    curator_visible: true
""",
    )
    _write_tool_policy_package(
        packages_dir,
        directory_name="org-custom",
        package_id="org.custom",
        policies_text="""
tool_policies:
  shared_tool:
    display_name: Org Shared
    category: Output
    curator_visible: false
""",
    )

    loaded = load_tool_policy_defaults(packages_dir=packages_dir)

    assert loaded["shared_tool"].display_name == "Org Shared"
    assert loaded["shared_tool"].category == "Output"
    assert loaded["shared_tool"].curator_visible is False
    assert loaded["shared_tool"].source_label is not None
    assert "package default 'org.custom'" in loaded["shared_tool"].source_label


def test_load_tool_policy_defaults_runtime_override_wins_over_package_defaults(tmp_path: Path):
    packages_dir = tmp_path / "packages"
    override_path = tmp_path / "tool_policy_defaults.yaml"
    _write_tool_policy_package(
        packages_dir,
        directory_name="agr-base",
        package_id="agr.base",
        policies_text="""
tool_policies:
  shared_tool:
    display_name: Base Shared
    category: Database
    curator_visible: true
    allow_attach: true
    allow_execute: true
    config: {}
""",
    )
    override_path.write_text(
        """
tool_policies:
  shared_tool:
    display_name: Runtime Shared
    category: Output
    curator_visible: false
    allow_attach: false
    allow_execute: true
    config:
      scope: runtime
  runtime_only:
    display_name: Runtime Only
    category: Document
    curator_visible: true
    allow_attach: true
    allow_execute: false
    config: {}
""".strip(),
        encoding="utf-8",
    )

    loaded = load_tool_policy_defaults(
        tool_policies_path=override_path,
        packages_dir=packages_dir,
    )

    assert loaded["shared_tool"].display_name == "Runtime Shared"
    assert loaded["shared_tool"].config == {"scope": "runtime"}
    assert loaded["shared_tool"].source_label == (
        f"runtime override 'tool_policy_defaults.yaml' at {override_path}"
    )
    assert loaded["runtime_only"].allow_execute is False


def test_load_tool_policy_defaults_reports_runtime_override_source_on_invalid_config(tmp_path: Path):
    override_path = tmp_path / "tool_policy_defaults.yaml"
    override_path.write_text(
        """
tool_policies:
  bad_tool:
    display_name: Bad Tool
    config:
      - nope
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime override 'tool_policy_defaults.yaml'") as exc_info:
        load_tool_policy_defaults(tool_policies_path=override_path)

    assert str(override_path) in str(exc_info.value)


def test_load_tool_policy_defaults_defaults_to_repo_root_sources_when_runtime_mount_absent(
    monkeypatch,
    tmp_path: Path,
):
    repo_root = tmp_path / "repo"
    module_path = repo_root / "backend" / "src" / "lib" / "config" / "package_default_sources.py"
    internal_lib_packages_dir = repo_root / "backend" / "src" / "lib" / "packages"
    packages_dir = repo_root / "packages"
    package_dir = packages_dir / "core"
    config_dir = repo_root / "config"

    module_path.parent.mkdir(parents=True)
    module_path.write_text("", encoding="utf-8")
    internal_lib_packages_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)

    _write_tool_policy_package(
        packages_dir,
        directory_name="core",
        package_id="agr.core",
        policies_text="""
tool_policies:
  packaged_tool:
    display_name: Packaged Tool
    category: Database
    curator_visible: true
    allow_attach: true
    allow_execute: true
    config: {}
""",
    )
    (config_dir / "tool_policy_defaults.yaml").write_text(
        """
tool_policies:
  packaged_tool:
    display_name: Runtime Tool
    category: Output
    curator_visible: false
    allow_attach: false
    allow_execute: true
    config: {}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(package_default_sources, "__file__", str(module_path))
    monkeypatch.setattr(
        package_default_sources,
        "get_runtime_packages_dir",
        lambda: repo_root / "runtime" / "packages",
    )
    monkeypatch.setattr(
        package_default_sources,
        "get_runtime_config_dir",
        lambda: repo_root / "runtime" / "config",
    )

    loaded = load_tool_policy_defaults()

    assert package_dir.exists()
    assert loaded["packaged_tool"].display_name == "Runtime Tool"
    assert loaded["packaged_tool"].source_label == (
        f"runtime override 'tool_policy_defaults.yaml' at {config_dir / 'tool_policy_defaults.yaml'}"
    )
