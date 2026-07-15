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
        "CONNECTIONS_CONFIG_OVERLAY_PATH",
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


def test_load_connections_deep_merges_deployment_overlay(monkeypatch, tmp_path: Path):
    base_path = tmp_path / "runtime" / "config" / "connections.yaml"
    _write_connections_yaml(base_path)
    overlay_path = tmp_path / "private" / "connections.overlay.yaml"
    overlay_path.parent.mkdir(parents=True)
    overlay_path.write_text(
        "services:\n"
        "  weaviate:\n"
        "    url: http://custom-weaviate:9090\n"
        "  external_reporting_db:\n"
        "    description: Deployment-owned reporting database\n"
        "    url: ${EXTERNAL_REPORTING_DB_URL:-}\n"
        "    required: false\n"
        "    active: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONNECTIONS_CONFIG_PATH", str(base_path))
    monkeypatch.setenv("CONNECTIONS_CONFIG_OVERLAY_PATH", str(overlay_path))

    connections = connections_loader.load_connections(force_reload=True)

    assert set(connections) == {"weaviate", "external_reporting_db"}
    assert connections["weaviate"].url == "http://custom-weaviate:9090"
    assert connections["weaviate"].description == "Vector database"
    assert connections["weaviate"].required is True
    assert connections["external_reporting_db"].active is False


def test_load_connections_rejects_missing_configured_overlay(
    monkeypatch, tmp_path: Path
):
    base_path = tmp_path / "connections.yaml"
    _write_connections_yaml(base_path)
    missing_overlay = tmp_path / "missing.overlay.yaml"
    monkeypatch.setenv("CONNECTIONS_CONFIG_OVERLAY_PATH", str(missing_overlay))

    with pytest.raises(FileNotFoundError, match="Connections configuration overlay"):
        connections_loader.load_connections(base_path, force_reload=True)
