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

    resolved_override, explicitly_configured = package_default_sources.resolve_runtime_config_path(
        None,
        env_var="TOOL_POLICY_DEFAULTS_CONFIG_PATH",
        filename="tool_policy_defaults.yaml",
    )
    assert resolved_override == config_dir / "tool_policy_defaults.yaml"
    assert explicitly_configured is False


def test_resolve_runtime_config_path_prefers_runtime_file_when_present(monkeypatch, tmp_path: Path):
    runtime_config_dir = tmp_path / "runtime" / "config"
    runtime_config_dir.mkdir(parents=True)
    runtime_groups_path = runtime_config_dir / "groups.yaml"
    runtime_groups_path.write_text("groups: {}\n", encoding="utf-8")

    monkeypatch.setattr(
        package_default_sources,
        "get_runtime_config_dir",
        lambda: runtime_config_dir,
    )

    resolved_path, explicitly_configured = package_default_sources.resolve_runtime_config_path(
        None,
        env_var="GROUPS_CONFIG_PATH",
        filename="groups.yaml",
    )

    assert resolved_path == runtime_groups_path
    assert explicitly_configured is False


def test_resolve_runtime_config_path_reports_explicit_env_override(monkeypatch, tmp_path: Path):
    explicit_path = tmp_path / "custom" / "connections.yaml"
    monkeypatch.setenv("CONNECTIONS_CONFIG_PATH", str(explicit_path))

    resolved_path, explicitly_configured = package_default_sources.resolve_runtime_config_path(
        None,
        env_var="CONNECTIONS_CONFIG_PATH",
        filename="connections.yaml",
    )

    assert resolved_path == explicit_path
    assert explicitly_configured is True


def test_resolve_runtime_config_path_does_not_fallback_to_repo_config_when_runtime_packages_exist(
    monkeypatch,
    tmp_path: Path,
):
    repo_root = tmp_path / "repo"
    runtime_packages_dir = tmp_path / "runtime" / "packages"
    runtime_config_dir = tmp_path / "runtime" / "config"
    repo_config_dir = repo_root / "config"
    module_path = (
        repo_root / "backend" / "src" / "lib" / "config" / "package_default_sources.py"
    )

    module_path.parent.mkdir(parents=True)
    module_path.write_text("", encoding="utf-8")
    repo_config_dir.mkdir(parents=True)
    runtime_packages_dir.mkdir(parents=True)
    runtime_config_dir.mkdir(parents=True)
    (repo_config_dir / "providers.yaml").write_text("providers: {}\n", encoding="utf-8")
    (runtime_packages_dir / "agr.core").mkdir()
    (runtime_packages_dir / "agr.core" / "package.yaml").write_text(
        "package_id: agr.core\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(package_default_sources, "__file__", str(module_path))
    monkeypatch.setattr(
        package_default_sources,
        "get_runtime_packages_dir",
        lambda: runtime_packages_dir,
    )
    monkeypatch.setattr(
        package_default_sources,
        "get_runtime_config_dir",
        lambda: runtime_config_dir,
    )

    resolved_path, explicitly_configured = package_default_sources.resolve_runtime_config_path(
        None,
        env_var="PROVIDERS_CONFIG_PATH",
        filename="providers.yaml",
    )

    assert resolved_path == runtime_config_dir / "providers.yaml"
    assert resolved_path.exists() is False
    assert explicitly_configured is False
