"""Contract tests for the standalone production compose file."""

from __future__ import annotations

from pathlib import Path

import yaml


WORKSPACE_ROOT = Path("/workspace")
COMPOSE_PATH = WORKSPACE_ROOT / "docker-compose.production.yml"
ENV_TEMPLATE_PATH = WORKSPACE_ROOT / "scripts/install/lib/templates/env.standalone"
START_VERIFY_PATH = WORKSPACE_ROOT / "scripts/install/06_start_verify.sh"
WEAVIATE_IMAGE = (
    "semitechnologies/weaviate@sha256:"
    "5f0dc1fe066685558e22f324fbe9fadbc18730ce155ff47c27f891e62c652d2a"
)
MINIO_IMAGE = (
    "minio/minio@sha256:"
    "14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
)


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _bind_targets(service: dict) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for volume in service.get("volumes", []):
        if isinstance(volume, dict) and volume.get("type") == "bind":
            bindings[str(volume["target"])] = str(volume["source"])
    return bindings


def test_production_compose_uses_published_app_images_without_local_builds():
    compose = _load_compose()
    services = compose["services"]

    backend = services["backend"]
    frontend = services["frontend"]
    trace_review_backend = services["trace_review_backend"]

    assert "build" not in backend
    assert "build" not in frontend
    assert "build" not in trace_review_backend

    assert backend["image"].startswith(
        "${BACKEND_IMAGE:-public.ecr.aws/v4p5b7m9/agr-ai-curation-backend}:"
    )
    assert "${BACKEND_IMAGE_TAG:-" in backend["image"]
    assert frontend["image"].startswith(
        "${FRONTEND_IMAGE:-public.ecr.aws/v4p5b7m9/agr-ai-curation-frontend}:"
    )
    assert "${FRONTEND_IMAGE_TAG:-" in frontend["image"]
    assert trace_review_backend["image"].startswith(
        "${TRACE_REVIEW_BACKEND_IMAGE:-public.ecr.aws/v4p5b7m9/agr-ai-curation-trace-review-backend}:"
    )
    assert "${TRACE_REVIEW_BACKEND_IMAGE_TAG:-" in trace_review_backend["image"]
    assert services["weaviate"]["image"] == WEAVIATE_IMAGE
    assert services["minio"]["image"] == MINIO_IMAGE


def test_production_compose_mounts_modular_runtime_contract_and_keeps_diagnostics_first_class():
    compose = _load_compose()
    services = compose["services"]

    backend_bindings = _bind_targets(services["backend"])
    weaviate_bindings = _bind_targets(services["weaviate"])

    assert backend_bindings == {
        "/runtime/config": "${AGR_RUNTIME_CONFIG_HOST_DIR:-./config}",
        "/app/config": "${AGR_REPO_CONFIG_HOST_DIR:-./config}",
        "/runtime/packages": "${AGR_RUNTIME_PACKAGES_HOST_DIR:-./packages}",
        "/runtime/state": "${AGR_RUNTIME_STATE_HOST_DIR:-./runtime_state}",
        "/runtime/state/pdf_storage": "${PDF_STORAGE_HOST_DIR:-./pdf_storage}",
        "/runtime/state/file_outputs": "${FILE_OUTPUT_STORAGE_HOST_DIR:-./file_outputs}",
    }
    assert weaviate_bindings == {
        "/var/lib/weaviate": "${WEAVIATE_DATA_HOST_DIR:-./weaviate_data}",
    }

    backend_sources = set(backend_bindings.values())
    assert not any(source.startswith("./backend") for source in backend_sources)
    assert not any(source.startswith("./frontend") for source in backend_sources)
    assert not any(source.startswith("./trace_review") for source in backend_sources)
    assert not any(source.startswith("./scripts") for source in backend_sources)

    backend_env = services["backend"]["environment"]
    langfuse_worker_env = services["langfuse-worker"]["environment"]
    langfuse_env = services["langfuse"]["environment"]

    assert backend_env["AGR_RUNTIME_ROOT"] == "/runtime"
    assert backend_env["RUN_DB_BOOTSTRAP_ON_START"] == "${RUN_DB_BOOTSTRAP_ON_START:-true}"
    assert backend_env["RUN_DB_MIGRATIONS_ON_START"] == "${RUN_DB_MIGRATIONS_ON_START:-true}"
    assert backend_env["LLM_PROVIDER_STRICT_MODE"] == "${LLM_PROVIDER_STRICT_MODE:-false}"
    assert backend_env["TRACE_REVIEW_URL"] == "${TRACE_REVIEW_URL:-http://trace_review_backend:8001}"
    assert backend_env["PDF_STORAGE_PATH"] == "/runtime/state/pdf_storage"
    assert backend_env["FILE_OUTPUT_STORAGE_PATH"] == "/runtime/state/file_outputs"
    assert backend_env["DATABASE_URL"] == "${DATABASE_URL:?set in standalone env}"
    assert backend_env["CURATION_DB_CREDENTIALS_SOURCE"] == "${CURATION_DB_CREDENTIALS_SOURCE:-env}"
    assert backend_env["MAINTENANCE_MESSAGE_FILE"] == (
        "${MAINTENANCE_MESSAGE_FILE:-/runtime/config/maintenance_message.txt}"
    )

    assert langfuse_worker_env["DATABASE_URL"] == (
        "${LANGFUSE_LOCAL_DATABASE_URL:?set in standalone env}"
    )
    assert langfuse_worker_env["CLICKHOUSE_PASSWORD"] is None
    assert langfuse_worker_env["LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY"] is None
    assert langfuse_worker_env["LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY"] is None
    assert langfuse_worker_env["LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT"] == (
        "${LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT:-http://minio:9000}"
    )
    assert langfuse_worker_env["LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY"] is None
    assert langfuse_worker_env["REDIS_AUTH"] is None

    assert langfuse_env["LANGFUSE_INIT_USER_PASSWORD"] is None

    trace_review_backend = services["trace_review_backend"]
    assert trace_review_backend["ports"] == ["${TRACE_REVIEW_BACKEND_HOST_PORT:-8001}:8001"]
    assert trace_review_backend["environment"]["BACKEND_HOST"] == (
        "${TRACE_REVIEW_BACKEND_HOST:-0.0.0.0}"
    )
    assert trace_review_backend["environment"]["BACKEND_PORT"] == (
        "${TRACE_REVIEW_BACKEND_PORT:-8001}"
    )


def test_standalone_template_and_installer_reference_the_production_compose_path():
    env_template = ENV_TEMPLATE_PATH.read_text(encoding="utf-8")
    start_verify_script = START_VERIFY_PATH.read_text(encoding="utf-8")

    for key in (
        "AGR_RUNTIME_CONFIG_HOST_DIR=",
        "AGR_REPO_CONFIG_HOST_DIR=",
        "AGR_RUNTIME_PACKAGES_HOST_DIR=",
        "AGR_RUNTIME_STATE_HOST_DIR=",
        "PDF_STORAGE_HOST_DIR=",
        "FILE_OUTPUT_STORAGE_HOST_DIR=",
        "WEAVIATE_DATA_HOST_DIR=",
        "BACKEND_IMAGE=",
        "BACKEND_IMAGE_TAG=",
        "FRONTEND_IMAGE=",
        "FRONTEND_IMAGE_TAG=",
        "TRACE_REVIEW_BACKEND_IMAGE=",
        "TRACE_REVIEW_BACKEND_IMAGE_TAG=",
    ):
        assert key in env_template

    assert "main_compose_file" in start_verify_script
    assert "docker-compose.production.yml" in start_verify_script
