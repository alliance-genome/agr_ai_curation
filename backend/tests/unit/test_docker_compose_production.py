"""Contract tests for the standalone production compose file."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
from unittest.mock import Mock, patch

import pytest
import yaml

from src.lib.openai_agents.config import (
    get_pdf_max_file_size_bytes,
    get_pdf_upload_max_page_count,
)


WORKSPACE_ROOT = Path("/workspace")
if not (WORKSPACE_ROOT / "docker-compose.production.yml").exists():
    WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

COMPOSE_PATH = WORKSPACE_ROOT / "docker-compose.production.yml"
DEV_COMPOSE_PATH = WORKSPACE_ROOT / "docker-compose.yml"
TEST_COMPOSE_PATH = WORKSPACE_ROOT / "docker-compose.test.yml"
ENV_TEMPLATE_PATH = WORKSPACE_ROOT / "scripts/install/lib/templates/env.standalone"
ENV_EXAMPLE_PATH = WORKSPACE_ROOT / ".env.example"
PROVIDER_BOUNDARY_ENV_PATH = (
    WORKSPACE_ROOT
    / "scripts/testing/fixtures/agent_studio_provider_boundary_env.txt"
)
FRONTEND_DOCKERFILE_PATH = WORKSPACE_ROOT / "frontend" / "Dockerfile"
if not FRONTEND_DOCKERFILE_PATH.exists():
    FRONTEND_DOCKERFILE_PATH = Path("/app/frontend/Dockerfile")
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


def _load_test_compose() -> dict:
    return yaml.safe_load(TEST_COMPOSE_PATH.read_text(encoding="utf-8"))


def _list_environment(entries: list[str]) -> dict[str, str]:
    return dict(entry.split("=", 1) for entry in entries)


def _bind_targets(service: dict) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for volume in service.get("volumes", []):
        if isinstance(volume, dict) and volume.get("type") == "bind":
            bindings[str(volume["target"])] = str(volume["source"])
    return bindings


def _load_env_assignments(path: Path) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        assert key not in assignments, f"duplicate environment key in {path}: {key}"
        assignments[key] = value
    return assignments


def _load_provider_boundary_env_contract() -> list[tuple[str, str, str, tuple[str, ...]]]:
    records = []
    for raw_line in PROVIDER_BOUNDARY_ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        category, key, default, services = line.split("|", 3)
        records.append((category, key, default, tuple(services.split(","))))
    return records


def test_provider_boundary_operational_limits_have_deployment_parity():
    compose = _load_compose()
    env_example = _load_env_assignments(ENV_EXAMPLE_PATH)
    standalone_env = _load_env_assignments(ENV_TEMPLATE_PATH)
    contract = _load_provider_boundary_env_contract()

    assert contract
    assert len({key for _, key, _, _ in contract}) == len(contract)

    for category, key, default, services in contract:
        assert env_example.get(key) == default, f"{category}: .env.example drift for {key}"
        assert standalone_env.get(key) == default, f"{category}: standalone drift for {key}"
        for service_name in services:
            actual = compose["services"][service_name]["environment"].get(key)
            assert actual == f"${{{key}:-{default}}}", (
                f"{category}: production {service_name} does not deliver {key} "
                f"with default {default}"
            )


def test_backend_test_services_mount_repo_config_as_explicit_runtime_override():
    services = _load_test_compose()["services"]

    for service_name in (
        "backend-tests",
        "backend-unit-tests",
        "backend-integration-tests",
        "backend-persistence-tests",
        "backend-contract-tests",
    ):
        assert "./config:/runtime/config:ro" in services[service_name]["volumes"]


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
    assert env["TRACE_REVIEW_LANGFUSE_OBSERVATION_PAGE_LIMIT"] == (
        "${TRACE_REVIEW_LANGFUSE_OBSERVATION_PAGE_LIMIT:-1000}"
    )
    assert env["TRACE_REVIEW_LANGFUSE_REQUEST_TIMEOUT_SECONDS"] == (
        "${TRACE_REVIEW_LANGFUSE_REQUEST_TIMEOUT_SECONDS:-30}"
    )


def test_dev_curator_credentials_are_development_compose_only():
    dev_backend = _list_environment(_load_dev_compose()["services"]["backend"]["environment"])
    production_backend = _load_compose()["services"]["backend"]["environment"]
    expected = {
        "DOCUMENT_SOURCE_DEV_CURATOR_AUTH_MODE": "${DOCUMENT_SOURCE_DEV_CURATOR_AUTH_MODE:-none}",
        "DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_REGION": "${DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_REGION:-us-east-1}",
        "DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_USER_POOL_ID": "${DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_USER_POOL_ID:-}",
        "DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_CLIENT_ID": "${DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_CLIENT_ID:-}",
        "DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_CLIENT_SECRET": "${DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_CLIENT_SECRET:-}",
        "DOCUMENT_SOURCE_DEV_CURATOR_USERNAME": "${DOCUMENT_SOURCE_DEV_CURATOR_USERNAME:-}",
        "DOCUMENT_SOURCE_DEV_CURATOR_PASSWORD": "${DOCUMENT_SOURCE_DEV_CURATOR_PASSWORD:-}",
        "DOCUMENT_SOURCE_DEV_CURATOR_REFRESH_SKEW_SECONDS": "${DOCUMENT_SOURCE_DEV_CURATOR_REFRESH_SKEW_SECONDS:-600}",
    }

    for key, value in expected.items():
        assert dev_backend[key] == value
        assert key not in production_backend
    assert production_backend["DEV_MODE"] == "false"


def test_development_sentry_dsn_uses_an_isolated_compose_input():
    dev_backend = _list_environment(
        _load_dev_compose()["services"]["backend"]["environment"]
    )
    production_backend = _load_compose()["services"]["backend"]["environment"]
    production_only_explicit_keys = {
        key for key in production_backend if key.startswith("SENTRY_")
    } - {"SENTRY_DSN"}

    assert dev_backend["SENTRY_DSN"] == "${SENTRY_DEV_DSN:-}"
    assert dev_backend["SENTRY_DEV_DSN"] == ""
    assert _load_dev_compose()["services"]["backend"]["env_file"] == [
        {"path": "${AGR_DEV_ENV_FILE:-.env}", "required": False}
    ]
    assert "export AGR_DEV_ENV_FILE := $(ENV_FILE)" in MAKEFILE_PATH.read_text(
        encoding="utf-8"
    )
    assert production_only_explicit_keys.isdisjoint(dev_backend)
    assert production_backend["SENTRY_DSN"] == "${SENTRY_DSN:-}"


def test_env_example_documents_the_development_sentry_dsn_input():
    assignments = _load_env_assignments(ENV_EXAMPLE_PATH)

    assert assignments["SENTRY_DEV_DSN"] == ""
    assert assignments["SENTRY_DSN"] == ""


def test_compose_model_defaults_match_supported_gpt56_runtime_contract():
    dev_env = _list_environment(
        _load_dev_compose()["services"]["backend"]["environment"]
    )
    production_env = _load_compose()["services"]["backend"]["environment"]
    live_test_env = _list_environment(
        _load_test_compose()["services"]["backend-integration-tests"][
            "environment"
        ]
    )

    expected_backend_defaults = {
        "DEFAULT_AGENT_MODEL": "${DEFAULT_AGENT_MODEL:-gpt-5.6-terra}",
        "DEFAULT_AGENT_REASONING": "${DEFAULT_AGENT_REASONING:-medium}",
        "HIERARCHY_LLM_MODEL": "${HIERARCHY_LLM_MODEL:-gpt-5.6-terra}",
        "HIERARCHY_LLM_REASONING": "${HIERARCHY_LLM_REASONING:-low}",
        "FIGURE_LOCATOR_LLM_MODEL": "${FIGURE_LOCATOR_LLM_MODEL:-gpt-5.6-terra}",
        "FIGURE_LOCATOR_LLM_REASONING": "${FIGURE_LOCATOR_LLM_REASONING:-low}",
        "FIGURE_LOCATOR_RESOLUTION_MAX_TURNS": "${FIGURE_LOCATOR_RESOLUTION_MAX_TURNS:-10}",
        "FIGURE_LOCATOR_RESOLUTION_BATCH_MAX_CHARS": "${FIGURE_LOCATOR_RESOLUTION_BATCH_MAX_CHARS:-60000}",
        "ABSTRACT_EXTRACTION_MODEL": "${ABSTRACT_EXTRACTION_MODEL:-gpt-5.6-sol}",
    }
    assert {key: dev_env[key] for key in expected_backend_defaults} == (
        expected_backend_defaults
    )
    assert {key: production_env[key] for key in expected_backend_defaults} == (
        expected_backend_defaults
    )
    assert live_test_env["LIVE_LLM_OPENAI_MODEL"] == (
        "${LIVE_LLM_OPENAI_MODEL:-gpt-5.6-terra}"
    )


def test_compose_propagates_optional_openrouter_key_name_only():
    dev_env = _list_environment(
        _load_dev_compose()["services"]["backend"]["environment"]
    )
    production_env = _load_compose()["services"]["backend"]["environment"]
    test_env = _list_environment(
        _load_test_compose()["services"]["backend-unit-tests"]["environment"]
    )

    expected = "${OPENROUTER_API_KEY:-}"
    assert dev_env["OPENROUTER_API_KEY"] == expected
    assert production_env["OPENROUTER_API_KEY"] == expected
    assert test_env["OPENROUTER_API_KEY"] == expected


def test_compose_and_install_surface_compatible_http_retry_limit():
    dev_env = _list_environment(
        _load_dev_compose()["services"]["backend"]["environment"]
    )
    production_env = _load_compose()["services"]["backend"]["environment"]
    standalone_env = _load_env_assignments(ENV_TEMPLATE_PATH)

    expected = "${OPENAI_COMPATIBLE_HTTP_MAX_RETRIES:-2}"
    assert dev_env["OPENAI_COMPATIBLE_HTTP_MAX_RETRIES"] == expected
    assert production_env["OPENAI_COMPATIBLE_HTTP_MAX_RETRIES"] == expected
    assert standalone_env["OPENAI_COMPATIBLE_HTTP_MAX_RETRIES"] == "2"


def test_agent_studio_compose_and_env_example_default_to_opus_5():
    dev_env = _list_environment(
        _load_dev_compose()["services"]["backend"]["environment"]
    )
    production_env = _load_compose()["services"]["backend"]["environment"]
    env_example = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

    assert dev_env["PROMPT_EXPLORER_MODEL_ID"] == (
        "${PROMPT_EXPLORER_MODEL_ID:-claude-opus-5}"
    )
    assert production_env["PROMPT_EXPLORER_MODEL_ID"] == (
        "${PROMPT_EXPLORER_MODEL_ID:-claude-opus-5}"
    )
    assert "PROMPT_EXPLORER_MODEL_ID=claude-opus-5" in env_example
    retired_env_var = "ANTHROPIC_" + "OPUS_MODEL"
    assert retired_env_var not in dev_env
    assert retired_env_var not in production_env
    assert f"{retired_env_var}=" not in env_example


def test_pdf_size_limit_is_shared_by_backend_and_frontend_compose_services(
    monkeypatch,
):
    dev_services = _load_dev_compose()["services"]
    production_services = _load_compose()["services"]
    expected = "${PDF_MAX_FILE_SIZE_BYTES:-524288000}"

    assert _list_environment(dev_services["frontend"]["environment"])[
        "PDF_MAX_FILE_SIZE_BYTES"
    ] == expected
    assert _list_environment(dev_services["backend"]["environment"])[
        "PDF_MAX_FILE_SIZE_BYTES"
    ] == expected
    assert production_services["frontend"]["environment"][
        "PDF_MAX_FILE_SIZE_BYTES"
    ] == expected
    assert production_services["backend"]["environment"][
        "PDF_MAX_FILE_SIZE_BYTES"
    ] == expected
    assert "PDF_MAX_FILE_SIZE_BYTES=524288000" in ENV_TEMPLATE_PATH.read_text(
        encoding="utf-8"
    )
    dockerfile = FRONTEND_DOCKERFILE_PATH.read_text(encoding="utf-8")
    image_default = re.search(
        r"^ENV PDF_MAX_FILE_SIZE_BYTES=(\d+)$",
        dockerfile,
        re.MULTILINE,
    )
    assert image_default is not None
    monkeypatch.delenv("PDF_MAX_FILE_SIZE_BYTES", raising=False)
    assert int(image_default.group(1)) == get_pdf_max_file_size_bytes()


def test_pdf_page_count_limit_is_shared_by_backend_env_templates(monkeypatch):
    dev_backend_env = _list_environment(
        _load_dev_compose()["services"]["backend"]["environment"]
    )
    production_backend_env = _load_compose()["services"]["backend"]["environment"]
    expected = "${PDF_UPLOAD_MAX_PAGE_COUNT:-300}"

    assert dev_backend_env["PDF_UPLOAD_MAX_PAGE_COUNT"] == expected
    assert production_backend_env["PDF_UPLOAD_MAX_PAGE_COUNT"] == expected
    assert "PDF_UPLOAD_MAX_PAGE_COUNT=300" in ENV_TEMPLATE_PATH.read_text(
        encoding="utf-8"
    )
    assert "PDF_UPLOAD_MAX_PAGE_COUNT=300" in ENV_EXAMPLE_PATH.read_text(
        encoding="utf-8"
    )
    monkeypatch.delenv("PDF_UPLOAD_MAX_PAGE_COUNT", raising=False)
    assert get_pdf_upload_max_page_count() == 300


def test_pdf_extraction_timeout_is_shared_by_backend_env_templates():
    dev_backend_env = _list_environment(
        _load_dev_compose()["services"]["backend"]["environment"]
    )
    production_backend_env = _load_compose()["services"]["backend"]["environment"]
    expected = "${PDF_EXTRACTION_TIMEOUT:-3600}"

    assert dev_backend_env["PDF_EXTRACTION_TIMEOUT"] == expected
    assert production_backend_env["PDF_EXTRACTION_TIMEOUT"] == expected
    assert "PDF_EXTRACTION_TIMEOUT=3600" in ENV_TEMPLATE_PATH.read_text(
        encoding="utf-8"
    )
    assert "PDF_EXTRACTION_TIMEOUT=3600" in ENV_EXAMPLE_PATH.read_text(
        encoding="utf-8"
    )


def test_document_intake_vite_limits_reach_standard_frontend_builds():
    frontend_build_args = _load_dev_compose()["services"]["frontend"]["build"]["args"]
    expected = {
        "VITE_PDF_UPLOAD_MAX_SELECTED_FILES": (
            "${VITE_PDF_UPLOAD_MAX_SELECTED_FILES:-10}"
        ),
        "VITE_PDF_JOB_WINDOW_DAYS": "${VITE_PDF_JOB_WINDOW_DAYS:-7}",
        "VITE_PDF_JOB_LIMIT": "${VITE_PDF_JOB_LIMIT:-50}",
        "VITE_PDF_JOB_FALLBACK_POLL_INTERVAL_MS": (
            "${VITE_PDF_JOB_FALLBACK_POLL_INTERVAL_MS:-5000}"
        ),
    }
    assert {key: frontend_build_args[key] for key in expected} == expected

    dockerfile = FRONTEND_DOCKERFILE_PATH.read_text(encoding="utf-8")
    for name, default in (
        ("VITE_PDF_UPLOAD_MAX_SELECTED_FILES", "10"),
        ("VITE_PDF_JOB_WINDOW_DAYS", "7"),
        ("VITE_PDF_JOB_LIMIT", "50"),
        ("VITE_PDF_JOB_FALLBACK_POLL_INTERVAL_MS", "5000"),
    ):
        assert f"ARG {name}={default}" in dockerfile
        assert f"ENV {name}=${name}" in dockerfile


def test_dev_compose_uses_package_mount_for_agent_studio_prompt_source():
    compose = _load_dev_compose()
    backend_volumes = compose["services"]["backend"]["volumes"]

    assert "./packages:/runtime/packages:ro" in backend_volumes
    assert not any("alliance_config" in volume for volume in backend_volumes)


def test_production_compose_uses_runtime_packages_for_agent_studio_prompt_source():
    compose = _load_compose()
    backend_bindings = _bind_targets(compose["services"]["backend"])

    assert backend_bindings["/runtime/packages"] == (
        "${AGR_RUNTIME_PACKAGES_HOST_DIR:-./packages}"
    )
    assert "/app/alliance_config" not in backend_bindings


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


def test_production_frontend_never_publishes_dev_mode_at_runtime():
    frontend = _load_compose()["services"]["frontend"]
    runtime_environment = frontend.get("environment", {})

    assert not any(
        key == "VITE_DEV_MODE" or key.startswith("VITE_DEV_USER_")
        for key in runtime_environment
    )
    assert "VITE_DEV_MODE" not in runtime_environment["FRONTEND_RUNTIME_CONFIG_KEYS"]
    assert "VITE_DEV_USER_" not in runtime_environment["FRONTEND_RUNTIME_CONFIG_KEYS"]
    assert not frontend.get("env_file")


def test_frontend_build_metadata_requires_production_mode_and_source_revision():
    assert production_preflight.validate_frontend_build_metadata(
        {"schema_version": 1, "vite_dev_mode": False, "git_sha": "abcdef1"}
    ) == []

    assert production_preflight.validate_frontend_build_metadata(
        {"schema_version": 1, "vite_dev_mode": True, "git_sha": "unknown"}
    ) == [
        "frontend image must be compiled with vite_dev_mode=false",
        "frontend build metadata git_sha must identify the compiled source",
    ]


def test_preflight_reads_build_metadata_from_selected_frontend_image():
    pull = Mock(returncode=0, stdout="", stderr="")
    inspect = Mock(
        returncode=0,
        stdout='{"schema_version":1,"vite_dev_mode":false,"git_sha":"abcdef1"}',
        stderr="",
    )

    with patch.object(
        production_preflight.subprocess,
        "run",
        side_effect=[pull, inspect],
    ) as run:
        metadata = production_preflight.inspect_frontend_build_metadata(
            "example.invalid/frontend:v0.9.0"
        )

    assert metadata["vite_dev_mode"] is False
    assert run.call_args_list[0].args[0] == [
        "docker",
        "pull",
        "example.invalid/frontend:v0.9.0",
    ]
    assert run.call_args_list[1].args[0] == [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--entrypoint",
        "cat",
        "example.invalid/frontend:v0.9.0",
        "/usr/share/nginx/html/build-metadata.json",
    ]


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


def test_runtime_preflight_allows_explicit_sentry_operational_overrides():
    config = _safe_rendered_config()
    backend_env = config["services"]["backend"]["environment"]
    backend_env["SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS"] = "4096"
    backend_env["SENTRY_TRANSACTION_RETAINED_SPANS_MAX"] = "75"

    assert production_preflight.validate_config(config) == []
    assert production_preflight.validate_config(
        config, enforce_operational_defaults=True
    ) == [
        "backend.SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS must render as 2000",
        "backend.SENTRY_TRANSACTION_RETAINED_SPANS_MAX must render as 50",
    ]


@pytest.mark.parametrize(
    ("service", "key", "unsafe_value", "expected_error"),
    [
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


@pytest.mark.parametrize("unsafe_tag", ["main", "develop", "release-0.9.0", "latest"])
def test_effective_production_contract_rejects_mutable_or_undocumented_app_tags(
    unsafe_tag: str,
):
    config = _safe_rendered_config()
    for service in production_preflight.APP_SERVICES:
        config["services"][service]["image"] = f"example.invalid/{service}:{unsafe_tag}"

    errors = production_preflight.validate_config(config)

    for service in production_preflight.APP_SERVICES:
        assert any(error.startswith(f"{service}.image must") for error in errors)


@pytest.mark.parametrize("safe_tag", ["v0.9.0", "sha-abcdef1", "sha-0123456789abcdef"])
def test_effective_production_contract_accepts_documented_app_tags(safe_tag: str):
    config = _safe_rendered_config()
    for service in production_preflight.APP_SERVICES:
        config["services"][service]["image"] = f"example.invalid/{service}:{safe_tag}"

    assert production_preflight.validate_config(config) == []


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
        "/runtime/packages": "${AGR_RUNTIME_PACKAGES_HOST_DIR:-./packages}",
        "/runtime/state": "${AGR_RUNTIME_STATE_HOST_DIR:-./runtime_state}",
        "/runtime/state/pdf_storage": "${PDF_STORAGE_HOST_DIR:-./pdf_storage}",
        "/runtime/state/file_outputs": "${FILE_OUTPUT_STORAGE_HOST_DIR:-./file_outputs}",
    }
    assert weaviate_bindings == {
        "/var/lib/weaviate": "${WEAVIATE_DATA_HOST_DIR:-./weaviate_data}",
        "/var/lib/weaviate-backups": "${WEAVIATE_BACKUP_HOST_DIR:-./weaviate_native_backups}",
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
    assert trace_review_backend["environment"][
        "TRACE_REVIEW_LANGFUSE_OBSERVATION_PAGE_LIMIT"
    ] == "${TRACE_REVIEW_LANGFUSE_OBSERVATION_PAGE_LIMIT:-1000}"
    assert trace_review_backend["environment"][
        "TRACE_REVIEW_LANGFUSE_REQUEST_TIMEOUT_SECONDS"
    ] == "${TRACE_REVIEW_LANGFUSE_REQUEST_TIMEOUT_SECONDS:-30}"


def test_standalone_template_and_installer_reference_the_production_compose_path():
    env_template = ENV_TEMPLATE_PATH.read_text(encoding="utf-8")
    start_verify_script = START_VERIFY_PATH.read_text(encoding="utf-8")

    assert "TRACE_REVIEW_LANGFUSE_OBSERVATION_PAGE_LIMIT=1000" in env_template
    assert "TRACE_REVIEW_LANGFUSE_REQUEST_TIMEOUT_SECONDS=30" in env_template

    for key in (
        "AGR_RUNTIME_CONFIG_HOST_DIR=",
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
    assert "AGR_REPO_CONFIG_HOST_DIR=" not in env_template

    assert "main_compose_file" in start_verify_script
    assert "docker-compose.production.yml" in start_verify_script
    assert "production_compose_preflight.py" in start_verify_script


def test_make_prod_is_the_single_source_checkout_production_launch_path():
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")

    assert "production_compose_preflight.py --env-file" in makefile
    assert 'docker compose --env-file "$(ENV_FILE)" -f docker-compose.production.yml up -d' in makefile
    assert "docker-compose.prod.yml" not in makefile
    assert "prod-build:" not in makefile


def test_generated_production_launches_cannot_bypass_preflight():
    start_verify_script = START_VERIFY_PATH.read_text(encoding="utf-8")

    assert (
        'Restart command: ${repo_root}/scripts/install/install.sh --from-stage 6'
        in start_verify_script
    )
    assert "Restart command: docker compose" not in start_verify_script
    assert "docker compose --env-file ${env_output_path} -f ${main_compose_file} restart" not in (
        start_verify_script
    )
