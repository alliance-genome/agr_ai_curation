"""Tests for config.package_default_sources."""

from pathlib import Path

from src.lib.config import package_default_sources


def test_find_project_root_skips_backend_src_lib_false_positive(monkeypatch, tmp_path: Path):
    repo_root = tmp_path / "repo"
    module_path = repo_root / "backend" / "src" / "lib" / "config" / "package_default_sources.py"
    internal_lib_packages_dir = repo_root / "backend" / "src" / "lib" / "packages"

    module_path.parent.mkdir(parents=True)
    module_path.write_text("", encoding="utf-8")
    internal_lib_packages_dir.mkdir(parents=True)
    (repo_root / "packages").mkdir()

    config_dir = repo_root / "config"
    config_dir.mkdir()
    (config_dir / "models.yaml").write_text("models: []\n", encoding="utf-8")
    (config_dir / "providers.yaml").write_text("providers: {}\n", encoding="utf-8")
    (config_dir / "tool_policy_defaults.yaml").write_text(
        "tool_policies: {}\n",
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

    assert package_default_sources._find_project_root() == repo_root
    assert package_default_sources.get_default_packages_dir() == repo_root / "packages"

    resolved_override, explicitly_configured = package_default_sources._resolve_runtime_override_path(
        None,
        env_var="TOOL_POLICY_DEFAULTS_CONFIG_PATH",
        filename="tool_policy_defaults.yaml",
    )
    assert resolved_override == config_dir / "tool_policy_defaults.yaml"
    assert explicitly_configured is False
