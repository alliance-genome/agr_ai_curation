"""Regression tests for Promtail Docker service discovery."""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = (
    Path("/workspace")
    if Path("/workspace").exists()
    else Path(__file__).resolve().parents[3]
)
PROMTAIL_CONFIG_PATH = REPO_ROOT / "promtail-config.yml"
COMPOSE_PATHS = [
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "docker-compose.production.yml",
]


def _load_promtail_config() -> dict:
    return yaml.safe_load(PROMTAIL_CONFIG_PATH.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_promtail_docker_discovery_is_compose_project_name_agnostic():
    raw_config = PROMTAIL_CONFIG_PATH.read_text(encoding="utf-8")
    config = _load_promtail_config()

    docker_sd_configs = config["scrape_configs"][0]["docker_sd_configs"]
    filters = docker_sd_configs[0]["filters"]

    assert "ai_curation_prototype" not in raw_config
    assert filters == [
        {
            "name": "label",
            "values": ["com.docker.compose.project"],
        }
    ]


def test_promtail_positions_use_durable_container_path():
    config = _load_promtail_config()

    assert config["positions"]["filename"] == "/var/lib/promtail/positions.yaml"


def test_promtail_keeps_compose_project_and_service_labels_for_queries():
    config = _load_promtail_config()

    relabel_configs = config["scrape_configs"][0]["relabel_configs"]
    target_by_label = {
        relabel_config["target_label"]: relabel_config
        for relabel_config in relabel_configs
        if "target_label" in relabel_config
    }

    assert target_by_label["service"]["source_labels"] == [
        "__meta_docker_container_label_com_docker_compose_service"
    ]
    assert target_by_label["compose_project"]["source_labels"] == [
        "__meta_docker_container_label_com_docker_compose_project"
    ]


def test_compose_files_mount_durable_promtail_positions_volume():
    for compose_path in COMPOSE_PATHS:
        compose = _load_yaml(compose_path)
        promtail = compose["services"]["promtail"]

        assert "promtail-positions" in compose["volumes"], compose_path.name
        assert (
            "promtail-positions:/var/lib/promtail" in promtail["volumes"]
        ), compose_path.name
