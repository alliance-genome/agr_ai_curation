"""Runtime config default-path tests for groups and connections loaders."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lib.config import connections_loader, groups_loader


@pytest.fixture(autouse=True)
def _reset_loader_state(monkeypatch):
    groups_loader.reset_cache()
    connections_loader.reset_cache()
    for variable in (
        "AGR_RUNTIME_ROOT",
        "AGR_RUNTIME_CONFIG_DIR",
        "GROUPS_CONFIG_PATH",
        "CONNECTIONS_CONFIG_PATH",
    ):
        monkeypatch.delenv(variable, raising=False)
    yield
    groups_loader.reset_cache()
    connections_loader.reset_cache()


def _write_groups_yaml(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "identity_provider:\n"
        "  type: oidc\n"
        "  group_claim: groups\n"
        "groups:\n"
        "  FB:\n"
        "    name: FlyBase\n"
        "    provider_groups:\n"
        "      - flybase-curators\n",
        encoding="utf-8",
    )


def _write_connections_yaml(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "services:\n"
        "  weaviate:\n"
        "    description: Vector database\n"
        "    url: http://weaviate:8080\n"
        "    required: true\n",
        encoding="utf-8",
    )


def test_load_groups_defaults_to_runtime_config_dir(monkeypatch, tmp_path: Path):
    runtime_config_dir = tmp_path / "runtime" / "config"
    _write_groups_yaml(runtime_config_dir / "groups.yaml")
    monkeypatch.setenv("AGR_RUNTIME_CONFIG_DIR", str(runtime_config_dir))

    groups = groups_loader.load_groups(force_reload=True)

    assert set(groups) == {"FB"}
    assert groups_loader.get_group_claim_key() == "groups"


def test_load_groups_prefers_explicit_env_path(monkeypatch, tmp_path: Path):
    runtime_config_dir = tmp_path / "runtime" / "config"
    runtime_config_dir.mkdir(parents=True)
    explicit_path = tmp_path / "custom" / "groups.yaml"
    _write_groups_yaml(explicit_path)
    monkeypatch.setenv("AGR_RUNTIME_CONFIG_DIR", str(runtime_config_dir))
    monkeypatch.setenv("GROUPS_CONFIG_PATH", str(explicit_path))

    groups = groups_loader.load_groups(force_reload=True)

    assert set(groups) == {"FB"}
    assert groups_loader._get_default_groups_path() == explicit_path


def test_load_connections_defaults_to_runtime_config_dir(monkeypatch, tmp_path: Path):
    runtime_config_dir = tmp_path / "runtime" / "config"
    _write_connections_yaml(runtime_config_dir / "connections.yaml")
    monkeypatch.setenv("AGR_RUNTIME_CONFIG_DIR", str(runtime_config_dir))

    connections = connections_loader.load_connections(force_reload=True)

    assert set(connections) == {"weaviate"}
    assert connections["weaviate"].url == "http://weaviate:8080"


def test_load_connections_prefers_explicit_env_path(monkeypatch, tmp_path: Path):
    runtime_config_dir = tmp_path / "runtime" / "config"
    runtime_config_dir.mkdir(parents=True)
    explicit_path = tmp_path / "custom" / "connections.yaml"
    _write_connections_yaml(explicit_path)
    monkeypatch.setenv("AGR_RUNTIME_CONFIG_DIR", str(runtime_config_dir))
    monkeypatch.setenv("CONNECTIONS_CONFIG_PATH", str(explicit_path))

    connections = connections_loader.load_connections(force_reload=True)

    assert set(connections) == {"weaviate"}
    assert connections_loader._get_default_connections_path() == explicit_path
