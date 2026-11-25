"""Contract tests for the standalone production compose file."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
from unittest.mock import Mock, patch

import pytest
import yaml

from src.lib.openai_agents.config import (
    get_agent_context_capacity_headroom_tokens,
    get_batch_zip_metadata_chunk_size,
    get_connections_health_snapshot_ttl_seconds,
    get_document_table_header_max_rows,
    get_document_table_header_min_filled_fraction,
    get_langfuse_timeout,
    get_max_parallel_validators,
    get_pdf_max_file_size_bytes,
    get_pdf_upload_max_page_count,
    get_weaviate_chunk_fetch_page_size,
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
# One search index engine for every compose file: the version production runs.
WEAVIATE_VERSION = "1.31.18"
WEAVIATE_IMAGE = (
    f"semitechnologies/weaviate:{WEAVIATE_VERSION}@sha256:"
    "9b903ac1b4d030a4939535a12a36f07fe3968ff1d9a8622d527e07e644fa4e41"
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


@pytest.mark.parametrize("configured,expected", [(None, 500), ("73", 73)])
def test_production_forwards_batch_zip_metadata_chunk_size(monkeypatch, configured, expected):
    key = "BATCH_ZIP_METADATA_CHUNK_SIZE"
    environment = _load_compose()["services"]["backend"]["environment"]
    # Assert the actual Compose substitution contract, including its source key.
    assert environment[key] == "${BATCH_ZIP_METADATA_CHUNK_SIZE:-500}"
    assert _load_env_assignments(ENV_EXAMPLE_PATH)[key] == "500"
    if configured is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, configured)
    assert get_batch_zip_metadata_chunk_size() == expected


@pytest.mark.parametrize(
    "key,default,override,getter,consumers",
    [
        ("AGENT_CONTEXT_CAPACITY_HEADROOM_TOKENS", 20000, 12000,
         get_agent_context_capacity_headroom_tokens, ("backend", "benchmark_worker")),
        ("LANGFUSE_TIMEOUT", 5, 13, get_langfuse_timeout, ("backend", "benchmark_worker")),
        ("WEAVIATE_CHUNK_FETCH_PAGE_SIZE", 250, 60,
         get_weaviate_chunk_fetch_page_size, ("backend", "benchmark_worker")),
        ("CONNECTIONS_HEALTH_SNAPSHOT_TTL_SECONDS", 45, 8.5,
         get_connections_health_snapshot_ttl_seconds, ("backend",)),
    ],
)
def test_compose_forwards_operational_settings_to_consumers(
    monkeypatch, key, default, override, getter, consumers
):
    for compose in (_load_dev_compose(), _load_compose()):
        for service in consumers:
            environment = compose["services"][service]["environment"]
            if isinstance(environment, list):
                environment = _list_environment(environment)
            assert environment[key] == f"${{{key}:-{default}}}"
    assert _load_env_assignments(ENV_EXAMPLE_PATH)[key] == str(default)
    monkeypatch.delenv(key, raising=False)
    assert getter() == default
    monkeypatch.setenv(key, str(override))
    assert getter() == override


@pytest.mark.parametrize(
    "key,default,configured,expected,getter",
    [
        ("DOCUMENT_TABLE_HEADER_MAX_ROWS", "5", None, 5, get_document_table_header_max_rows),
        ("DOCUMENT_TABLE_HEADER_MAX_ROWS", "5", "3", 3, get_document_table_header_max_rows),
        ("DOCUMENT_TABLE_HEADER_MIN_FILLED_FRACTION", "0.5", None, 0.5, get_document_table_header_min_filled_fraction),
        ("DOCUMENT_TABLE_HEADER_MIN_FILLED_FRACTION", "0.5", "0.75", 0.75, get_document_table_header_min_filled_fraction),
    ],
)
def test_compose_forwards_table_header_rule_to_every_chunking_service(
    monkeypatch, key, default, configured, expected, getter
):
    """The backend and the benchmark worker both cut text into pieces."""
    production = _load_compose()["services"]
    development = _load_dev_compose()["services"]
    reference = "${" + key + ":-" + default + "}"
    for service in ("backend", "benchmark_worker"):
        assert production[service]["environment"][key] == reference
    assert _list_environment(development["backend"]["environment"])[key] == reference
    assert development["benchmark_worker"]["environment"][key] == reference
    assert _load_env_assignments(ENV_EXAMPLE_PATH)[key] == default
    if configured is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, configured)
    assert getter() == expected


def test_compose_forwards_admin_emails_to_backend():
    production = _load_compose()["services"]["backend"]["environment"]
    development_entries = _load_dev_compose()["services"]["backend"]["environment"]
    assert production["ADMIN_EMAILS"] == "${ADMIN_EMAILS:-}"
    # Development Compose relies on AGR_DEV_ENV_FILE (.env) for ADMIN_EMAILS;
    # omitting it from backend.environment ensures Compose v2 never overwrites
    # env_file values with null/empty when unset in the shell or --env-file.
    assert "ADMIN_EMAILS" not in development_entries
    assert not any(entry.startswith("ADMIN_EMAILS=") for entry in development_entries)


def _load_dev_compose() -> dict:
    return yaml.safe_load(DEV_COMPOSE_PATH.read_text(encoding="utf-8"))


def test_standalone_template_documents_langfuse_timeout():
    assert _load_env_assignments(ENV_TEMPLATE_PATH)["LANGFUSE_TIMEOUT"] == "5"


@pytest.mark.parametrize("compose_path,backend_name,worker_name", [
    (DEV_COMPOSE_PATH, "backend", "benchmark_worker"),
    (COMPOSE_PATH, "backend", "benchmark_worker"),
    (TEST_COMPOSE_PATH, "backend-integration-tests", "benchmark-worker-test"),
])
def test_benchmark_api_settings_reach_api_process(compose_path, backend_name, worker_name):
    services = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]
    environment = services[backend_name]["environment"]
    if isinstance(environment, list):
        environment = _list_environment(environment)
    assert environment["BENCHMARK_API_ENABLED"] == (
        "false" if compose_path == COMPOSE_PATH else "${BENCHMARK_API_ENABLED:-false}"
    )
    assert environment["BENCHMARK_EXECUTION_ENABLED"] == (
        "false" if compose_path == COMPOSE_PATH else "${BENCHMARK_EXECUTION_ENABLED:-false}"
    )
    for name, default in (
        ("BENCHMARK_CATALOG_MAX_RESPONSE_BYTES", 1048576),
        ("BENCHMARK_EVENT_HEARTBEAT_SECONDS", 15),
        ("BENCHMARK_EVENT_REPLAY_BATCH_SIZE", 250),
        ("BENCHMARK_MAX_EVENT_CONNECTIONS_PER_PRINCIPAL", 5),
    ):
        assert environment[name] == "${" + name + ":-" + str(default) + "}"
    assert environment["BENCHMARK_ENVIRONMENT_ID"] == "${BENCHMARK_ENVIRONMENT_ID:-unconfigured}"
    assert "BENCHMARK_API_ENABLED" not in services[worker_name]["environment"]


def _load_test_compose() -> dict:
    return yaml.safe_load(TEST_COMPOSE_PATH.read_text(encoding="utf-8"))


def test_benchmark_worker_is_isolated_and_production_dispatch_is_disabled():
    production = _load_compose()["services"]["benchmark_worker"]
    development = _load_dev_compose()["services"]["benchmark_worker"]
    test_service = _load_test_compose()["services"]["benchmark-worker-test"]

    assert production["command"] == "python -m src.lib.benchmarks.worker"
    assert production["environment"]["BENCHMARK_WORKER_ENABLED"] == "false"
    assert production["environment"]["BENCHMARK_EXECUTION_ENABLED"] == "false"
    assert production["restart"] == "on-failure"
    assert development["environment"]["BENCHMARK_WORKER_ENABLED"].endswith(
        ":-false}"
    )
    assert development["environment"]["BENCHMARK_EXECUTION_ENABLED"].endswith(
        ":-false}"
    )
    assert "DATABASE_URL" not in development["environment"]
    assert development["restart"] == "on-failure"
    assert test_service["command"] == "python -m src.lib.benchmarks.worker"
    assert test_service["environment"]["POSTGRES_HOST"] == "postgres-test"
    assert test_service["environment"]["POSTGRES_PASSWORD"].endswith(
        ":-CHANGE_ME_TEST_DB_PASSWORD}"
    )


def _list_environment(entries: list[str]) -> dict[str, str | None]:
    parsed: dict[str, str | None] = {}
    for entry in entries:
        if "=" in entry:
            key, value = entry.split("=", 1)
            parsed[key] = value
        else:
            parsed[entry] = None
    return parsed


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


def test_benchmark_cognito_m2m_profile_has_compose_and_env_parity():
    dev_env = _list_environment(_load_dev_compose()["services"]["backend"]["environment"])
    production_env = _load_compose()["services"]["backend"]["environment"]
    test_env = _list_environment(
        _load_test_compose()["services"]["backend-integration-tests"]["environment"]
    )
    env_example = _load_env_assignments(ENV_EXAMPLE_PATH)
    expected = {
        "BENCHMARK_OIDC_COGNITO_M2M_ENABLED": "${BENCHMARK_OIDC_COGNITO_M2M_ENABLED:-false}",
        "BENCHMARK_OIDC_COGNITO_M2M_CLIENT_IDS": "${BENCHMARK_OIDC_COGNITO_M2M_CLIENT_IDS:-}",
    }

    for key, value in expected.items():
        assert dev_env[key] == value
        assert production_env[key] == value
        assert test_env[key] == value
    assert env_example["BENCHMARK_OIDC_COGNITO_M2M_ENABLED"] == "false"
    assert env_example["BENCHMARK_OIDC_COGNITO_M2M_CLIENT_IDS"] == ""


def test_flow_export_keys_reach_the_production_backend_and_are_documented():
    production_env = _load_compose()["services"]["backend"]["environment"]
    env_example = _load_env_assignments(ENV_EXAMPLE_PATH)
    for key in ("FLOW_EXPORT_SIGNING_KEY", "FLOW_EXPORT_ISSUER", "FLOW_EXPORT_BEARER_CLIENT_IDS"):
        assert production_env[key] == f"${{{key}:-}}"
        assert env_example[key] == ""
    for key in ("FLOW_IMPORT_EXPORT_ISSUER", "FLOW_IMPORT_EXPORT_PUBLIC_KEY"):
        assert env_example[key] == ""


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
    assert env["TRACE_REVIEW_LANGFUSE_SCORE_PAGE_LIMIT"] == "${TRACE_REVIEW_LANGFUSE_SCORE_PAGE_LIMIT:-100}"
    assert env["TRACE_REVIEW_LANGFUSE_OBSERVATION_PAGE_LIMIT"] == (
        "${TRACE_REVIEW_LANGFUSE_OBSERVATION_PAGE_LIMIT:-1000}"
    )
    assert env["TRACE_REVIEW_LANGFUSE_REQUEST_TIMEOUT_SECONDS"] == (
        "${TRACE_REVIEW_LANGFUSE_REQUEST_TIMEOUT_SECONDS:-30}"
    )


def test_dev_reader_settings_are_development_compose_only():
    dev_backend = _list_environment(_load_dev_compose()["services"]["backend"]["environment"])
    production_backend = _load_compose()["services"]["backend"]["environment"]
    key = "DOCUMENT_SOURCE_DEV_READER_REFRESH_SKEW_SECONDS"

    assert dev_backend[key] == "${DOCUMENT_SOURCE_DEV_READER_REFRESH_SKEW_SECONDS:-600}"
    assert key not in production_backend
    assert production_backend["DEV_MODE"] == "false"


def test_no_user_password_document_source_settings_remain():
    # Services reach ABC only through a machine reader; user sign-in settings are gone.
    dev_backend = _list_environment(_load_dev_compose()["services"]["backend"]["environment"])
    production_backend = _load_compose()["services"]["backend"]["environment"]
    for environment in (dev_backend, production_backend):
        assert not [
            key for key in environment
            if key.startswith("DOCUMENT_SOURCE_DEV_CURATOR_")
            or key in {"ABC_LITERATURE_COGNITO_USERNAME", "ABC_LITERATURE_COGNITO_PASSWORD"}
        ]


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


def test_compose_model_defaults_match_supported_runtime_roles():
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
        "DEFAULT_AGENT_MODEL": "${DEFAULT_AGENT_MODEL:-gpt-6.1-sol}",
        "DEFAULT_AGENT_REASONING": "${DEFAULT_AGENT_REASONING:-low}",
        "HIERARCHY_LLM_MODEL": "${HIERARCHY_LLM_MODEL:-gpt-6.1-sol}",
        "HIERARCHY_LLM_REASONING": "${HIERARCHY_LLM_REASONING:-low}",
        "FIGURE_LOCATOR_LLM_MODEL": "${FIGURE_LOCATOR_LLM_MODEL:-gpt-6.1-sol}",
        "FIGURE_LOCATOR_LLM_REASONING": "${FIGURE_LOCATOR_LLM_REASONING:-low}",
        "FIGURE_LOCATOR_RESOLUTION_MAX_TURNS": "${FIGURE_LOCATOR_RESOLUTION_MAX_TURNS:-10}",
        "FIGURE_LOCATOR_RESOLUTION_BATCH_MAX_CHARS": "${FIGURE_LOCATOR_RESOLUTION_BATCH_MAX_CHARS:-60000}",
    }
    assert {key: dev_env[key] for key in expected_backend_defaults} == (
        expected_backend_defaults
    )
    assert {key: production_env[key] for key in expected_backend_defaults} == (
        expected_backend_defaults
    )
    assert live_test_env["LIVE_LLM_OPENAI_MODEL"] == (
        "${LIVE_LLM_OPENAI_MODEL:-gpt-6.1-sol}"
    )


def test_removed_abstract_lookup_settings_are_declared_nowhere():
    """The abstract lookup and its three settings were removed; none may return."""
    declaration_files = [
        *sorted(WORKSPACE_ROOT.glob("docker-compose*.yml")),
        ENV_EXAMPLE_PATH,
        ENV_TEMPLATE_PATH,
    ]

    assert COMPOSE_PATH in declaration_files and DEV_COMPOSE_PATH in declaration_files
    for path in declaration_files:
        assert "ABSTRACT_EXTRACTION_" not in path.read_text(encoding="utf-8"), path


def test_compose_forwards_document_file_converted_text_cap():
    dev_env = _list_environment(
        _load_dev_compose()["services"]["backend"]["environment"]
    )
    production_env = _load_compose()["services"]["backend"]["environment"]
    documented_default = _load_env_assignments(ENV_EXAMPLE_PATH)[
        "DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES"
    ]

    assert documented_default == "1000000"
    expected = f"${{DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES:-{documented_default}}}"
    assert dev_env["DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES"] == expected
    assert production_env["DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES"] == expected
    # Benchmark preparation downloads through the same adapter.
    assert _load_dev_compose()["services"]["benchmark_worker"]["environment"][
        "DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES"
    ] == expected
    assert _load_compose()["services"]["benchmark_worker"]["environment"][
        "DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES"
    ] == expected


@pytest.mark.parametrize(
    ("name", "default"),
    [
        # The main paper's import deadline and ABC's poll interval
        # (KANBAN-1976 / ALL-1460, P7w).
        ("DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS", "3600"),
        ("DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS", "30"),
        ("DOCUMENT_SOURCE_DEAD_WAIT_POLLS", "3"),
        # The supplement wait (KANBAN-1976 / ALL-1460, P7b).
        ("DOCUMENT_SUPPLEMENT_WAIT_TIMEOUT_SECONDS", "10800"),
        ("DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS", "60"),
        ("DOCUMENT_SOURCE_LISTING_MAX_FAILED_READS", "3"),
        ("DOCUMENT_SUPPLEMENT_ADD_RESERVE_SECONDS", "900"),
        ("DOCUMENT_SUPPLEMENT_MAX_CONCURRENT_WAITS", "10"),
    ],
)
def test_compose_forwards_provider_import_wait_settings(name, default):
    dev_env = _list_environment(_load_dev_compose()["services"]["backend"]["environment"])
    production_env = _load_compose()["services"]["backend"]["environment"]
    documented_default = _load_env_assignments(ENV_EXAMPLE_PATH)[name]

    assert documented_default == default
    expected = f"${{{name}:-{default}}}"
    assert dev_env[name] == expected
    assert production_env[name] == expected


def test_compose_forwards_request_ceiling_preserving_empty_string():
    name = "DOCUMENT_SOURCE_MAX_POLLS_PER_WAIT"
    dev_env = _list_environment(_load_dev_compose()["services"]["backend"]["environment"])
    production_env = _load_compose()["services"]["backend"]["environment"]
    documented_default = _load_env_assignments(ENV_EXAMPLE_PATH)[name]

    assert documented_default == "400"
    # Omit ':' so an unset variable defaults to 400 while an explicit empty string
    # reaches the backend as "" and fails strict startup parsing.
    expected = f"${{{name}-{documented_default}}}"
    assert dev_env[name] == expected
    assert production_env[name] == expected


@pytest.mark.parametrize(
    ("name", "default"),
    [
        ("DOCUMENT_FILE_DOWNLOAD_CONCURRENCY", "4"),
        ("DOCUMENT_FILE_LISTING_CHECK_LIMIT", "20"),
        ("DOCUMENT_FILE_LISTING_CURSOR_CACHE_MAX_ENTRIES", "512"),
        ("DOCUMENT_MAX_FILES_PER_PAPER", "20"),
        ("DOCUMENT_PAPER_MAX_TEXT_BYTES", "2000000"),
        ("DOCUMENT_FILE_MAX_PDF_BYTES", "20000000"),
        ("DOCUMENT_FILE_FEW_CHARACTERS_NOTE_BELOW", "200"),
        ("DOCUMENT_FILE_PIECE_COUNT_LOCK_WAIT_SECONDS", "10"),
    ],
)
def test_compose_forwards_document_file_listing_settings(name, default):
    dev_env = _list_environment(_load_dev_compose()["services"]["backend"]["environment"])
    production_env = _load_compose()["services"]["backend"]["environment"]
    documented_default = _load_env_assignments(ENV_EXAMPLE_PATH)[name]

    assert documented_default == default
    expected = f"${{{name}:-{default}}}"
    assert dev_env[name] == expected
    assert production_env[name] == expected


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


def test_agent_studio_has_no_retired_provider_configuration():
    dev_env = _list_environment(
        _load_dev_compose()["services"]["backend"]["environment"]
    )
    production_env = _load_compose()["services"]["backend"]["environment"]
    env_example = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

    retired_keys = {
        "ANTHROPIC_API_KEY",
        "PROMPT_EXPLORER_MODEL_ID",
        "AGENT_STUDIO_OPUS_CONTEXT_EDITING_TRIGGER_TOKENS",
        "AGENT_STUDIO_OPUS_CONTEXT_EDITING_KEEP_TOOL_USES",
    }
    assert retired_keys.isdisjoint(dev_env)
    assert retired_keys.isdisjoint(production_env)
    for key in retired_keys:
        assert f"{key}=" not in env_example


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


def test_native_compaction_controls_reach_standalone_backend():
    for name, default in (
        ("AGENT_CONTEXT_COMPACTION_ENABLED", "true"),
        ("AGENT_CONTEXT_COMPACTION_TOKEN_THRESHOLD", "250000"),
        ("AGENT_CONTEXT_CAPACITY_HEADROOM_TOKENS", "20000"),
    ):
        for service in ("backend", "benchmark_worker"):
            environment = _load_compose()["services"][service]["environment"]
            assert environment[name] == "${" + name + ":-" + default + "}"
        assert f"{name}={default}" in ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        assert f"{name}={default}" in ENV_TEMPLATE_PATH.read_text(encoding="utf-8")


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
                    "AGR_PACKAGE_ENV_BOOTSTRAP_MODE": "prebuilt",
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
            "benchmark_worker": {
                "image": "example.invalid/backend:v0.9.0",
                "environment": {
                    "WEAVIATE_EXPECTED_VERSION": "1.31.18",
                    "AGR_PACKAGE_ENV_BOOTSTRAP_MODE": "prebuilt",
                },
            },
        }
    )
    services["weaviate"]["image"] = "example.invalid/weaviate:1.31.18@sha256:" + "a" * 64
    services["backend"]["environment"]["WEAVIATE_EXPECTED_VERSION"] = "1.31.18"
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
        ("backend", "AGR_PACKAGE_ENV_BOOTSTRAP_MODE", "dynamic", "backend.AGR_PACKAGE_ENV_BOOTSTRAP_MODE"),
        ("backend", "AGR_PACKAGE_ENV_BOOTSTRAP_MODE", "", "backend.AGR_PACKAGE_ENV_BOOTSTRAP_MODE"),
        ("benchmark_worker", "AGR_PACKAGE_ENV_BOOTSTRAP_MODE", "dynamic", "benchmark_worker.AGR_PACKAGE_ENV_BOOTSTRAP_MODE"),
        ("benchmark_worker", "AGR_PACKAGE_ENV_BOOTSTRAP_MODE", "", "benchmark_worker.AGR_PACKAGE_ENV_BOOTSTRAP_MODE"),
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


@pytest.mark.parametrize(
    "image",
    [
        "semitechnologies/weaviate:latest",
        "semitechnologies/weaviate:1.31.18",
        "semitechnologies/weaviate@sha256:" + "a" * 64,
    ],
)
def test_effective_production_contract_rejects_weaviate_image_without_version_and_digest(image):
    config = _safe_rendered_config()
    config["services"]["weaviate"]["image"] = image

    errors = production_preflight.validate_config(config)

    assert any("weaviate.image must name an engine version and a digest" in e for e in errors)


def test_effective_production_contract_rejects_expected_version_other_than_the_image():
    config = _safe_rendered_config()
    config["services"]["backend"]["environment"]["WEAVIATE_EXPECTED_VERSION"] = "1.40.7"
    config["services"]["benchmark_worker"] = {
        "image": "example.invalid/backend:v0.9.0",
        "environment": {},
    }

    errors = production_preflight.validate_config(config)

    assert "backend.WEAVIATE_EXPECTED_VERSION must equal the weaviate image version 1.31.18" in errors
    assert (
        "benchmark_worker.WEAVIATE_EXPECTED_VERSION must equal the weaviate image version 1.31.18"
        in errors
    )


def _weaviate_clients(services: dict) -> dict:
    """Services that connect to the search index: Weaviate host settings or a dependency on it."""
    clients = {}
    for name, service in services.items():
        env = service.get("environment") or {}
        if isinstance(env, list):
            env = _list_environment(env)
        depends = service.get("depends_on") or {}
        connects = any(key.startswith("WEAVIATE_") and "HOST" in key for key in env) or any(
            dep.startswith("weaviate") for dep in depends
        )
        if connects or name in {"benchmark_worker", "benchmark-worker-test"}:
            clients[name] = env
    return clients


@pytest.mark.parametrize(
    "compose_path,weaviate_service",
    [(COMPOSE_PATH, "weaviate"), (DEV_COMPOSE_PATH, "weaviate"), (TEST_COMPOSE_PATH, "weaviate-test")],
)
def test_every_compose_file_pins_the_same_weaviate_engine(compose_path, weaviate_service):
    services = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]

    assert services[weaviate_service]["image"] == WEAVIATE_IMAGE
    clients = _weaviate_clients(services)
    assert clients, "expected at least one service that connects to the search index"
    for name, env in clients.items():
        # The value is literal (no ${...}) so it cannot drift from the image tag.
        assert str(env.get("WEAVIATE_EXPECTED_VERSION", "")).split("#")[0].strip() == WEAVIATE_VERSION, name


def test_no_compose_file_names_a_floating_weaviate_image():
    for path in sorted(WORKSPACE_ROOT.glob("docker-compose*.yml")):
        services = yaml.safe_load(path.read_text(encoding="utf-8")).get("services") or {}
        for name, service in services.items():
            image = str(service.get("image", ""))
            if "semitechnologies/weaviate:" in image or "semitechnologies/weaviate@" in image:
                assert image == WEAVIATE_IMAGE, f"{path.name}:{name}"


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
    assert trace_review_backend["environment"]["TRACE_REVIEW_LANGFUSE_SCORE_PAGE_LIMIT"] == "${TRACE_REVIEW_LANGFUSE_SCORE_PAGE_LIMIT:-100}"
    assert trace_review_backend["environment"][
        "TRACE_REVIEW_LANGFUSE_OBSERVATION_PAGE_LIMIT"
    ] == "${TRACE_REVIEW_LANGFUSE_OBSERVATION_PAGE_LIMIT:-1000}"
    assert trace_review_backend["environment"][
        "TRACE_REVIEW_LANGFUSE_REQUEST_TIMEOUT_SECONDS"
    ] == "${TRACE_REVIEW_LANGFUSE_REQUEST_TIMEOUT_SECONDS:-30}"


def test_standalone_template_and_installer_reference_the_production_compose_path():
    env_template = ENV_TEMPLATE_PATH.read_text(encoding="utf-8")
    start_verify_script = START_VERIFY_PATH.read_text(encoding="utf-8")

    assert "TRACE_REVIEW_LANGFUSE_SCORE_PAGE_LIMIT=100" in env_template
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


def test_worker_sentry_configuration_matches_backend_in_each_environment():
    prod = _load_compose()["services"]
    prod_sentry = {
        key: value for key, value in prod["backend"]["environment"].items()
        if key.startswith("SENTRY_")
    }
    assert prod_sentry
    assert {
        key: value for key, value in prod["benchmark_worker"]["environment"].items()
        if key.startswith("SENTRY_")
    } == prod_sentry

    dev = _load_dev_compose()["services"]
    backend_env = _list_environment(dev["backend"]["environment"])
    worker_env = dev["benchmark_worker"]["environment"]
    assert worker_env["SENTRY_DSN"] == backend_env["SENTRY_DSN"] == "${SENTRY_DEV_DSN:-}"
    assert worker_env["SENTRY_DEV_DSN"] == backend_env["SENTRY_DEV_DSN"] == ""
    # Release, environment and the remaining Sentry settings come from this shared env file.
    assert dev["benchmark_worker"]["env_file"] == dev["backend"]["env_file"]


@pytest.mark.parametrize("compose_path", [DEV_COMPOSE_PATH, COMPOSE_PATH])
def test_tracereview_shares_provider_configuration_and_has_its_own_callback(compose_path):
    services = yaml.safe_load(compose_path.read_text())["services"]
    environments = []
    for service in ("backend", "trace_review_backend"):
        environment = services[service]["environment"]
        environments.append(_list_environment(environment) if isinstance(environment, list) else environment)
    backend, review = environments
    for name in (
        "AUTH_PROVIDER", "OIDC_ISSUER_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET",
        "OIDC_SCOPES", "OIDC_GROUP_CLAIM", "OIDC_LOGOUT_URL", "OIDC_LOGOUT_REDIRECT_PARAM",
        "AUTH_PROVIDER_TIMEOUT_SECONDS", "AUTH_JWKS_TIMEOUT_SECONDS", "AUTH_JWKS_CACHE_TTL_SECONDS",
        "AUTH_OAUTH_COOKIE_MAX_AGE_SECONDS", "AUTH_SESSION_COOKIE_MAX_AGE_SECONDS",
    ):
        assert review[name] == backend[name], name
    assert review["OIDC_REDIRECT_URI"] == "${TRACE_REVIEW_OIDC_REDIRECT_URI:-http://localhost:3001/api/auth/callback}"
    assert review["COGNITO_REDIRECT_URI"] == "${TRACE_REVIEW_COGNITO_REDIRECT_URI:-http://localhost:3001/api/auth/callback}"
    if compose_path == COMPOSE_PATH:
        assert review["DEV_MODE"] == "false"


def test_standalone_tracereview_forwards_auth_settings_from_environment():
    compose_path = WORKSPACE_ROOT / "trace_review/docker-compose.yml"
    if not compose_path.exists():
        compose_path = Path("/app/trace_review/docker-compose.yml")
    environment = _list_environment(yaml.safe_load(compose_path.read_text())["services"]["backend"]["environment"])
    assert environment["AUTH_PROVIDER"] == "${AUTH_PROVIDER}"
    assert environment["OIDC_ISSUER_URL"] == "${OIDC_ISSUER_URL:-}"
    assert environment["OIDC_CLIENT_ID"] == "${OIDC_CLIENT_ID:-}"
    assert environment["OIDC_REDIRECT_URI"] == "${OIDC_REDIRECT_URI:-http://localhost:${TRACE_REVIEW_FRONTEND_HOST_PORT:-3001}/api/auth/callback}"
    assert environment["COGNITO_REDIRECT_URI"] == "${COGNITO_REDIRECT_URI:-http://localhost:${TRACE_REVIEW_FRONTEND_HOST_PORT:-3001}/api/auth/callback}"


def test_optional_capacity_override_accepts_effective_compose_empty_default(monkeypatch):
    from src.lib.openai_agents.config import get_agent_model_capacity_overrides
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    for service in ("backend", "benchmark_worker"):
        env = compose["services"][service]["environment"]
        value = env["AGENT_MODEL_CAPACITY_OVERRIDES_JSON"] if isinstance(env, dict) else next(
            item.split("=", 1)[1] for item in env if item.startswith("AGENT_MODEL_CAPACITY_OVERRIDES_JSON="))
        assert value == "${AGENT_MODEL_CAPACITY_OVERRIDES_JSON:-}"
        monkeypatch.setenv("AGENT_MODEL_CAPACITY_OVERRIDES_JSON", "")
        assert get_agent_model_capacity_overrides() == {}


def test_application_pool_capacity_reaches_backend_and_worker():
    services = _load_compose()["services"]
    for service in ("backend", "benchmark_worker"):
        environment = services[service]["environment"]
        assert environment["APP_DB_POOL_SIZE"] == "${APP_DB_POOL_SIZE-5}"
        assert environment["APP_DB_MAX_OVERFLOW"] == "${APP_DB_MAX_OVERFLOW-10}"


@pytest.mark.parametrize("name,default", [
    ("PDF_EXTRACTION_QUEUE_TIMEOUT_SECONDS", 5400),
    ("PDF_EXTRACTION_STATUS_REQUEST_TIMEOUT_SECONDS", 30),
])
def test_pdfx_wait_settings_reach_backend(name, default):
    dev = _list_environment(_load_dev_compose()["services"]["backend"]["environment"])
    prod = _load_compose()["services"]["backend"]["environment"]
    assert dev[name] == prod[name] == "${" + name + ":-" + str(default) + "}"
    for template in (ENV_TEMPLATE_PATH, ENV_EXAMPLE_PATH):
        assert f"{name}={default}" in template.read_text()


def test_validation_history_pagination_has_deployment_parity():
    key = "VALIDATION_HISTORY_PAGE_SIZE"
    expected = "${VALIDATION_HISTORY_PAGE_SIZE:-5}"
    assert _load_compose()["services"]["backend"]["environment"][key] == expected
    assert _list_environment(_load_dev_compose()["services"]["backend"]["environment"])[key] == expected
    assert _load_env_assignments(ENV_EXAMPLE_PATH)[key] == "5"


REVIEW_LIMIT_CONTRACT = [
    line.split("|") for line in (
        WORKSPACE_ROOT / "scripts/testing/fixtures/validation_review_env.txt"
    ).read_text().splitlines() if line and not line.startswith("#")
]


@pytest.mark.parametrize("key,default,override", REVIEW_LIMIT_CONTRACT)
def test_validation_review_limits_have_deployment_parity(key, default, override, monkeypatch):
    from src.lib.validation_review.policy import ReviewLimits

    monkeypatch.delenv(key, raising=False)
    field = key.removeprefix("VALIDATION_REVIEW_").lower()
    def configured_value():
        if field == "initial_corrective_continuations":
            from src.lib.openai_agents.config import get_validation_review_initial_corrective_continuations
            return get_validation_review_initial_corrective_continuations()
        return ReviewLimits.configured().snapshot()[field]

    value = configured_value()
    assert value == (int(default) if default else None)
    expected = "${" + key + ":-" + default + "}"
    for path, names in (
        (DEV_COMPOSE_PATH, ("backend", "benchmark_worker")),
        (COMPOSE_PATH, ("backend", "benchmark_worker")),
        (TEST_COMPOSE_PATH, ("backend-unit-tests", "backend-persistence-tests", "benchmark-worker-test")),
    ):
        services = yaml.safe_load(path.read_text())["services"]
        for name in names:
            env = services[name]["environment"]
            if isinstance(env, list):
                env = _list_environment(env)
            assert env[key] == expected
    assert _load_env_assignments(ENV_EXAMPLE_PATH)[key] == default
    monkeypatch.setenv(key, override)
    assert str(configured_value()) == override


def test_validation_review_deployment_contract_covers_every_getter():
    from src.lib.validation_review.policy import ReviewLimits

    assert {key for key, _, _ in REVIEW_LIMIT_CONTRACT} == {
        "VALIDATION_REVIEW_" + field.upper() for field in ReviewLimits.configured().snapshot()
    } | {"VALIDATION_REVIEW_INITIAL_CORRECTIVE_CONTINUATIONS"}


def test_workspace_review_concurrency_deployment_parity(monkeypatch):
    from src.lib.openai_agents.config import get_workspace_review_concurrency
    key = "WORKSPACE_REVIEW_CONCURRENCY"
    monkeypatch.delenv(key, raising=False)
    assert get_workspace_review_concurrency() == 1
    for path in (DEV_COMPOSE_PATH, COMPOSE_PATH):
        env = yaml.safe_load(path.read_text())["services"]["backend"]["environment"]
        if isinstance(env, list):
            env = _list_environment(env)
        assert env[key] == "${WORKSPACE_REVIEW_CONCURRENCY:-1}"
    assert _load_env_assignments(ENV_EXAMPLE_PATH)[key] == "1"
    monkeypatch.setenv(key, "3")
    assert get_workspace_review_concurrency() == 3


QUICK_CHECK_CONTRACT = [
    ("VALIDATION_REVIEW_QUICK_CHECK_MODE", "get_validation_review_quick_check_mode", "off", "shadow", "shadow"),
    ("VALIDATION_REVIEW_QUICK_CHECK_TIMEOUT_SECONDS", "get_validation_review_quick_check_timeout_seconds",
     "30", "12.5", 12.5),
    ("VALIDATION_REVIEW_QUICK_CHECK_MAX_RETRIES", "get_validation_review_quick_check_max_retries", "2", "0", 0),
]


@pytest.mark.parametrize("key,getter,default,override,parsed", QUICK_CHECK_CONTRACT)
def test_review_quick_check_settings_reach_every_review_runtime(key, getter, default, override, parsed, monkeypatch):
    from src.lib.openai_agents import config

    monkeypatch.delenv(key, raising=False)
    assert str(getattr(config, getter)()) in {default, default + ".0"}
    for path in (DEV_COMPOSE_PATH, COMPOSE_PATH):
        services = yaml.safe_load(path.read_text())["services"]
        for name in ("backend", "benchmark_worker"):
            env = services[name]["environment"]
            if isinstance(env, list):
                env = _list_environment(env)
            assert env[key] == "${" + key + ":-" + default + "}"
    # Test stacks never inherit a host shadow setting, so no test can reach the provider.
    test_services = yaml.safe_load(TEST_COMPOSE_PATH.read_text())["services"]
    assert all(key not in str(service.get("environment") or {}) for service in test_services.values())
    assert _load_env_assignments(ENV_EXAMPLE_PATH)[key] == default
    monkeypatch.setenv(key, override)
    assert getattr(config, getter)() == parsed


@pytest.mark.parametrize("key,value", [
    ("VALIDATION_REVIEW_QUICK_CHECK_MODE", "on"),
    ("VALIDATION_REVIEW_QUICK_CHECK_MODE", ""),
    ("VALIDATION_REVIEW_QUICK_CHECK_TIMEOUT_SECONDS", "0"),
    ("VALIDATION_REVIEW_QUICK_CHECK_TIMEOUT_SECONDS", "inf"),
    ("VALIDATION_REVIEW_QUICK_CHECK_TIMEOUT_SECONDS", "soon"),
    ("VALIDATION_REVIEW_QUICK_CHECK_MAX_RETRIES", "-1"),
    ("VALIDATION_REVIEW_QUICK_CHECK_MAX_RETRIES", "1.5"),
])
def test_review_quick_check_settings_reject_invalid_values(key, value, monkeypatch):
    from src.lib.openai_agents import config

    getter = next(name for setting, name, *_ in QUICK_CHECK_CONTRACT if setting == key)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        getattr(config, getter)()


@pytest.mark.parametrize("configured,expected", [(None, 8), ("3", 3)])
def test_validator_worker_capacity_reaches_all_consumers(monkeypatch, configured, expected):
    key = "MAX_PARALLEL_VALIDATORS"
    for compose in (_load_compose(), _load_dev_compose()):
        for service in ("backend", "benchmark_worker"):
            env = compose["services"][service]["environment"]
            if isinstance(env, list):
                env = _list_environment(env)
            assert env[key] == "${MAX_PARALLEL_VALIDATORS:-8}"
    assert _load_env_assignments(ENV_EXAMPLE_PATH)[key] == "8"
    if configured is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, configured)
    assert get_max_parallel_validators() == expected


FANOUT_CONTRACT = [
    line.split("|") for line in (
        WORKSPACE_ROOT / "scripts/testing/fixtures/extraction_fanout_env.txt"
    ).read_text().splitlines() if line and not line.startswith("#")
]
FANOUT_POSITIVE_SETTINGS = (
    "EXTRACTION_FANOUT_ITEMS_PER_WRITER",
    "EXTRACTION_FANOUT_MAX_WRITERS",
)
FANOUT_INTEGER_SETTINGS = (
    *FANOUT_POSITIVE_SETTINGS,
    "EXTRACTION_FANOUT_MIN_ITEMS",
    "EXTRACTION_FANOUT_MAX_PLAN_REMINDERS",
    "EXTRACTION_FANOUT_MAX_FINALIZE_CORRECTIONS",
    "EXTRACTION_FANOUT_WRITER_RERUNS",
)


@pytest.mark.parametrize("key", FANOUT_INTEGER_SETTINGS)
def test_extraction_fanout_integer_setting_rejects_invalid_values(key, monkeypatch):
    from src.lib.openai_agents import config

    invalid_values = ["", "abc", "-1"]
    if key in FANOUT_POSITIVE_SETTINGS:
        invalid_values.append("0")
    for value in invalid_values:
        monkeypatch.setenv(key, value)
        with pytest.raises(ValueError, match=key):
            getattr(config, "get_" + key.lower())()


def _fanout_value(raw):
    return {"true": True, "false": False}[raw] if raw in {"true", "false"} else int(raw)


@pytest.mark.parametrize("key,default,override", FANOUT_CONTRACT)
def test_extraction_fanout_settings_reach_every_extraction_consumer(key, default, override, monkeypatch):
    from src.lib.openai_agents import config

    getter = getattr(config, "get_" + key.lower())
    monkeypatch.delenv(key, raising=False)
    assert getter() == _fanout_value(default)
    default_operator = "-" if key in FANOUT_INTEGER_SETTINGS else ":-"
    expected = "${" + key + default_operator + default + "}"
    for path in (DEV_COMPOSE_PATH, COMPOSE_PATH):
        services = yaml.safe_load(path.read_text())["services"]
        for name in ("backend", "benchmark_worker"):
            env = services[name]["environment"]
            if isinstance(env, list):
                env = _list_environment(env)
            assert env[key] == expected
    assert _load_env_assignments(ENV_EXAMPLE_PATH)[key] == default
    monkeypatch.setenv(key, override)
    assert getter() == _fanout_value(override)


def test_extraction_fanout_contract_covers_every_getter():
    from src.lib.openai_agents import config

    getters = {name for name in dir(config) if name.startswith("get_extraction_fanout_")}
    assert getters == {"get_" + key.lower() for key, _, _ in FANOUT_CONTRACT}


def test_package_env_bootstrap_settings_have_deployment_parity(monkeypatch):
    from src.lib.openai_agents import config

    prod_services = _load_compose()["services"]
    dev_services = _load_dev_compose()["services"]
    env_example = _load_env_assignments(ENV_EXAMPLE_PATH)
    standalone_env = _load_env_assignments(ENV_TEMPLATE_PATH)

    for service in ("backend", "benchmark_worker"):
        assert prod_services[service]["environment"]["AGR_PACKAGE_ENV_BOOTSTRAP_MODE"] == (
            "${AGR_PACKAGE_ENV_BOOTSTRAP_MODE-prebuilt}"
        )
        assert prod_services[service]["environment"]["AGR_PREBUILT_PACKAGE_ENVS_DIR"] == (
            "${AGR_PREBUILT_PACKAGE_ENVS_DIR:-/opt/package-envs}"
        )
        dev_env = dev_services[service]["environment"]
        if isinstance(dev_env, list):
            dev_env = _list_environment(dev_env)
        assert dev_env["AGR_PACKAGE_ENV_BOOTSTRAP_MODE"] == (
            "${AGR_PACKAGE_ENV_BOOTSTRAP_MODE-dynamic}"
        )
        assert dev_env["AGR_PREBUILT_PACKAGE_ENVS_DIR"] == (
            "${AGR_PREBUILT_PACKAGE_ENVS_DIR:-/opt/package-envs}"
        )

    assert env_example["AGR_PACKAGE_ENV_BOOTSTRAP_MODE"] == "dynamic"
    assert env_example["AGR_PREBUILT_PACKAGE_ENVS_DIR"] == "/opt/package-envs"
    assert standalone_env["AGR_PACKAGE_ENV_BOOTSTRAP_MODE"] == "prebuilt"
    assert standalone_env["AGR_PREBUILT_PACKAGE_ENVS_DIR"] == "/opt/package-envs"

    monkeypatch.delenv("AGR_PACKAGE_ENV_BOOTSTRAP_MODE", raising=False)
    monkeypatch.delenv("AGR_PREBUILT_PACKAGE_ENVS_DIR", raising=False)
    assert config.get_package_env_bootstrap_mode() == "dynamic"
    assert config.get_prebuilt_package_envs_dir() == Path("/opt/package-envs")

    monkeypatch.setenv("AGR_PACKAGE_ENV_BOOTSTRAP_MODE", "prebuilt")
    monkeypatch.setenv("AGR_PREBUILT_PACKAGE_ENVS_DIR", "/srv/package-envs")
    assert config.get_package_env_bootstrap_mode() == "prebuilt"
    assert config.get_prebuilt_package_envs_dir() == Path("/srv/package-envs")

    for invalid in ("", "auto", "fallback"):
        monkeypatch.setenv("AGR_PACKAGE_ENV_BOOTSTRAP_MODE", invalid)
        with pytest.raises(ValueError, match="AGR_PACKAGE_ENV_BOOTSTRAP_MODE"):
            config.get_package_env_bootstrap_mode()
