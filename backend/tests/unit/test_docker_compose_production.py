"""Contract tests for the standalone production compose file."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml


WORKSPACE_ROOT = Path("/workspace")
if not (WORKSPACE_ROOT / "docker-compose.production.yml").exists():
    WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

COMPOSE_PATH = WORKSPACE_ROOT / "docker-compose.production.yml"
DEV_COMPOSE_PATH = WORKSPACE_ROOT / "docker-compose.yml"
ENV_TEMPLATE_PATH = WORKSPACE_ROOT / "scripts/install/lib/templates/env.standalone"
START_VERIFY_PATH = WORKSPACE_ROOT / "scripts/install/06_start_verify.sh"
MAKEFILE_PATH = WORKSPACE_ROOT / "Makefile"
PREFLIGHT_PATH = WORKSPACE_ROOT / "scripts/testing/production_compose_preflight.py"
if not PREFLIGHT_PATH.exists():
    PREFLIGHT_PATH = Path("/app/scripts/testing/production_compose_preflight.py")
WEAVIATE_IMAGE = (
    "semitechnologies/weaviate@sha256:"
    "5f0dc1fe066685558e22f324fbe9fadbc18730ce155ff47c27f891e62c652d2a"
)
MINIO_IMAGE = (
    "minio/minio@sha256:"
    "14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
)

spec = importlib.util.spec_from_file_location("production_compose_preflight", PREFLIGHT_PATH)
assert spec and spec.loader
production_preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(production_preflight)


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _load_dev_compose() -> dict:
    return yaml.safe_load(DEV_COMPOSE_PATH.read_text(encoding="utf-8"))


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
    assert "${BACKEND_IMAGE_TAG:?" in backend["image"]
    assert frontend["image"].startswith(
        "${FRONTEND_IMAGE:-public.ecr.aws/v4p5b7m9/agr-ai-curation-frontend}:"
    )
    assert "${FRONTEND_IMAGE_TAG:?" in frontend["image"]
    assert trace_review_backend["image"].startswith(
        "${TRACE_REVIEW_BACKEND_IMAGE:-public.ecr.aws/v4p5b7m9/agr-ai-curation-trace-review-backend}:"
    )
    assert "${TRACE_REVIEW_BACKEND_IMAGE_TAG:?" in trace_review_backend["image"]
    assert services["weaviate"]["image"] == WEAVIATE_IMAGE
    assert services["minio"]["image"] == MINIO_IMAGE


def test_dev_compose_trace_review_defaults_to_local_langfuse_bootstrap_keys():
    compose = _load_dev_compose()
    env_entries = compose["services"]["trace_review_backend"]["environment"]
    env = dict(entry.split("=", 1) for entry in env_entries)

    assert env["LANGFUSE_HOST"] == "${TRACE_REVIEW_LANGFUSE_HOST:-http://langfuse:3000}"
    assert env["LANGFUSE_LOCAL_HOST"] == (
        "${TRACE_REVIEW_LANGFUSE_LOCAL_HOST:-http://langfuse:3000}"
    )
    assert env["LANGFUSE_PUBLIC_KEY"] == (
        "${TRACE_REVIEW_LANGFUSE_PUBLIC_KEY:-"
        "${LANGFUSE_LOCAL_PUBLIC_KEY:-"
        "${LANGFUSE_PUBLIC_KEY:-"
        "${LANGFUSE_INIT_PROJECT_PUBLIC_KEY:-pk-lf-local-public-key-default}}}}"
    )
    assert env["LANGFUSE_SECRET_KEY"] == (
        "${TRACE_REVIEW_LANGFUSE_SECRET_KEY:-"
        "${LANGFUSE_LOCAL_SECRET_KEY:-"
        "${LANGFUSE_SECRET_KEY:-"
        "${LANGFUSE_INIT_PROJECT_SECRET_KEY:-sk-lf-local-secret-key-default}}}}"
    )
    assert env["LANGFUSE_LOCAL_PUBLIC_KEY"] == (
        "${TRACE_REVIEW_LANGFUSE_LOCAL_PUBLIC_KEY:-"
        "${LANGFUSE_LOCAL_PUBLIC_KEY:-"
        "${LANGFUSE_INIT_PROJECT_PUBLIC_KEY:-pk-lf-local-public-key-default}}}"
    )
    assert env["LANGFUSE_LOCAL_SECRET_KEY"] == (
        "${TRACE_REVIEW_LANGFUSE_LOCAL_SECRET_KEY:-"
        "${LANGFUSE_LOCAL_SECRET_KEY:-"
        "${LANGFUSE_INIT_PROJECT_SECRET_KEY:-sk-lf-local-secret-key-default}}}"
    )


def test_production_compose_requires_pinned_app_image_tags():
    compose = _load_compose()
    services = compose["services"]
    env_template = ENV_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert (
        services["backend"]["image"]
        == "${BACKEND_IMAGE:-public.ecr.aws/v4p5b7m9/agr-ai-curation-backend}:${BACKEND_IMAGE_TAG:?set a release or sha tag in the production env}"
    )
    assert (
        services["frontend"]["image"]
        == "${FRONTEND_IMAGE:-public.ecr.aws/v4p5b7m9/agr-ai-curation-frontend}:${FRONTEND_IMAGE_TAG:?set a release or sha tag in the production env}"
    )
    assert "BACKEND_IMAGE_TAG=CHANGE_ME_PINNED_RELEASE_TAG" in env_template
    assert "FRONTEND_IMAGE_TAG=CHANGE_ME_PINNED_RELEASE_TAG" in env_template

    for name, service in services.items():
        image = service["image"]
        assert not image.endswith(":latest"), name
        if name in production_preflight.STATEFUL_SERVICES:
            assert "@sha256:" in image, name


def _safe_rendered_config() -> dict:
    digest = "example.invalid/image@sha256:" + "a" * 64
    services: dict[str, dict] = {
        name: {"image": digest} for name in production_preflight.STATEFUL_SERVICES
    }
    services.update(
        {
            "frontend": {
                "image": "example.invalid/frontend:v0.9.0",
                "environment": {"VITE_DEV_MODE": "false"},
            },
            "backend": {
                "image": "example.invalid/backend:v0.9.0",
                "environment": {
                    "AUTH_PROVIDER": "oidc",
                    "OIDC_ISSUER_URL": "https://issuer.example.org",
                    "OIDC_CLIENT_ID": "curation-production",
                    "OIDC_REDIRECT_URI": "https://curation.example.org/auth/callback",
                    "DEBUG": "false",
                    "DEV_MODE": "false",
                    "HEALTH_CHECK_REQUIRE_EXTERNAL_VALIDATION_DEPS": "true",
                    "HEALTH_CHECK_REQUIRE_LITERATURE_DB": "true",
                    "HEALTH_CHECK_STRICT_MODE": "true",
                    "SECURE_COOKIES": "true",
                    "SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS": "2000",
                    "SENTRY_TRANSACTION_RETAINED_SPANS_MAX": "50",
                },
            },
            "trace_review_backend": {
                "image": "example.invalid/trace-review:v0.9.0",
                "environment": {"DEV_MODE": "false", "SECURE_COOKIES": "true"},
            },
        }
    )
    services["weaviate"]["environment"] = {
        "AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED": "false",
        "AUTHENTICATION_APIKEY_ENABLED": "true",
        "AUTHENTICATION_APIKEY_ALLOWED_KEYS": "test-production-key",
        "AUTHORIZATION_ADMINLIST_USERS": "curation-backend",
    }
    return {"services": services}


def test_effective_production_contract_accepts_secure_rendered_config():
    assert production_preflight.validate_config(_safe_rendered_config()) == []


def test_effective_production_contract_defaults_match_sentry_runtime_configuration():
    compose = _load_compose()
    backend_env = compose["services"]["backend"]["environment"]

    assert backend_env["SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS"] == (
        "${SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS:-2000}"
    )
    assert backend_env["SENTRY_TRANSACTION_RETAINED_SPANS_MAX"] == (
        "${SENTRY_TRANSACTION_RETAINED_SPANS_MAX:-50}"
    )
    assert production_preflight.validate_config(
        _safe_rendered_config(), enforce_operational_defaults=True
    ) == []


@pytest.mark.parametrize(
    ("service", "key", "unsafe_value", "expected_error"),
    [
        ("frontend", "VITE_DEV_MODE", "true", "frontend.VITE_DEV_MODE"),
        ("backend", "DEV_MODE", "true", "backend.DEV_MODE"),
        ("backend", "DEBUG", "true", "backend.DEBUG"),
        ("backend", "AUTH_PROVIDER", "dev", "backend.AUTH_PROVIDER"),
        ("backend", "SECURE_COOKIES", "false", "backend.SECURE_COOKIES"),
        ("backend", "HEALTH_CHECK_STRICT_MODE", "false", "backend.HEALTH_CHECK_STRICT_MODE"),
        (
            "backend",
            "HEALTH_CHECK_REQUIRE_EXTERNAL_VALIDATION_DEPS",
            "false",
            "backend.HEALTH_CHECK_REQUIRE_EXTERNAL_VALIDATION_DEPS",
        ),
        (
            "backend",
            "HEALTH_CHECK_REQUIRE_LITERATURE_DB",
            "false",
            "backend.HEALTH_CHECK_REQUIRE_LITERATURE_DB",
        ),
        ("trace_review_backend", "DEV_MODE", "true", "trace_review_backend.DEV_MODE"),
        (
            "trace_review_backend",
            "SECURE_COOKIES",
            "false",
            "trace_review_backend.SECURE_COOKIES",
        ),
        (
            "weaviate",
            "AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED",
            "true",
            "weaviate.AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED",
        ),
        (
            "backend",
            "SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS",
            "20000",
            "SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS",
        ),
        (
            "backend",
            "SENTRY_TRANSACTION_RETAINED_SPANS_MAX",
            "",
            "SENTRY_TRANSACTION_RETAINED_SPANS_MAX",
        ),
    ],
)
def test_effective_production_contract_rejects_unsafe_environment_values(
    service: str, key: str, unsafe_value: str, expected_error: str
):
    config = _safe_rendered_config()
    config["services"][service]["environment"][key] = unsafe_value
    assert any(
        expected_error in error
        for error in production_preflight.validate_config(
            config, enforce_operational_defaults=True
        )
    )


def test_effective_production_contract_rejects_mutable_stateful_image_and_data_port():
    config = _safe_rendered_config()
    config["services"]["postgres"]["image"] = "postgres:latest"
    config["services"]["weaviate"]["ports"] = [{"target": 8080, "published": "8080"}]

    errors = production_preflight.validate_config(config)

    assert any("postgres.image must not use" in error for error in errors)
    assert any("postgres.image must be pinned by digest" in error for error in errors)
    assert any("weaviate must not publish data ports" in error for error in errors)


def test_preflight_renders_the_exact_supported_compose_path(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("BACKEND_IMAGE_TAG=v0.9.0\n", encoding="utf-8")
    completed = Mock(returncode=0, stdout='{"services": {}}', stderr="")

    with patch.object(production_preflight.subprocess, "run", return_value=completed) as run:
        production_preflight.render_config(env_file)

    assert run.call_args.args[0] == [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(production_preflight.PRODUCTION_COMPOSE),
        "config",
        "--format",
        "json",
    ]


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
    assert backend_env["TRACE_REVIEW_INTERNAL_API_TOKEN"] == "${TRACE_REVIEW_INTERNAL_API_TOKEN:-}"
    assert backend_env["PDF_STORAGE_PATH"] == "/runtime/state/pdf_storage"
    assert backend_env["FILE_OUTPUT_STORAGE_PATH"] == "/runtime/state/file_outputs"
    assert backend_env["DATABASE_URL"] == "${DATABASE_URL:?set in standalone env}"
    assert backend_env["CURATION_DB_CREDENTIALS_SOURCE"] == "${CURATION_DB_CREDENTIALS_SOURCE:-env}"
    assert backend_env["MAINTENANCE_MESSAGE_FILE"] == (
        "${MAINTENANCE_MESSAGE_FILE:-/runtime/config/maintenance_message.txt}"
    )
    assert backend_env["RERANK_PROVIDER"] == "${RERANK_PROVIDER:-none}"
    assert backend_env["BEDROCK_RERANK_MODEL_ARN"] == (
        "${BEDROCK_RERANK_MODEL_ARN:-arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0}"
    )
    assert backend_env["RERANKER_URL"] == (
        "${RERANKER_URL:-http://reranker-transformers:8080}"
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
    assert trace_review_backend["environment"]["TRACE_REVIEW_INTERNAL_API_TOKEN"] == (
        "${TRACE_REVIEW_INTERNAL_API_TOKEN:-}"
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
        "TRACE_REVIEW_INTERNAL_API_TOKEN=",
    ):
        assert key in env_template

    assert "main_compose_file" in start_verify_script
    assert "docker-compose.production.yml" in start_verify_script
    assert "production_compose_preflight.py" in start_verify_script


def test_make_prod_is_the_single_source_checkout_production_launch_path():
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")

    assert "production_compose_preflight.py --env-file" in makefile
    assert 'docker compose --env-file "$(ENV_FILE)" -f docker-compose.production.yml up -d' in makefile
    assert "docker-compose.prod.yml" not in makefile
    assert "prod-build:" not in makefile
