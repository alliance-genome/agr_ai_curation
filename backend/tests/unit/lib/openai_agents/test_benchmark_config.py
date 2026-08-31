from pathlib import Path

import pytest

from src.lib.config import models_loader
from src.lib.openai_agents import config


REPOSITORY_ROOT = Path(__file__).resolve().parents[5]


def test_approved_benchmark_models_load_from_canonical_registry(tmp_path):
    models = models_loader.load_models(
        models_path=REPOSITORY_ROOT / "config" / "models.yaml",
        packages_dir=tmp_path / "missing-packages",
        force_reload=True,
    )

    assert [(model_id, models[model_id].provider) for model_id in models] == [
        ("gpt-5.6-sol", "openai"),
        ("gpt-5.6-terra", "openai"),
        ("deepseek/deepseek-v4-pro-0813", "openrouter"),
        ("google/gemini-3.7-flash", "openrouter"),
        ("qwen/qwen3.8-27b", "openrouter"),
    ]
    assert [model_id for model_id, model in models.items() if model.default] == [
        "gpt-5.6-sol"
    ]


def test_benchmark_operational_defaults(monkeypatch):
    for key in (
        "BENCHMARK_ENABLED",
        "BENCHMARK_ROOT",
        "BENCHMARK_SOURCE_TIMEOUT_SECONDS",
        "BENCHMARK_MAX_INPUT_BYTES",
        "BENCHMARK_MAX_CASES",
        "BENCHMARK_MAX_CONFIGURATIONS",
        "BENCHMARK_MAX_REPETITIONS",
        "BENCHMARK_MAX_CELLS",
        "BENCHMARK_MAX_CONCURRENCY",
        "BENCHMARK_MATRIX_LIMIT",
        "BENCHMARK_CASE_LIMIT",
        "BENCHMARK_RESULT_LIMIT",
        "BENCHMARK_TIMEOUT_SECONDS",
        "BENCHMARK_RETRIES",
        "BENCHMARK_PREVIEW_MAX_CHARS",
        "BENCHMARK_INLINE_MAX_BYTES",
        "BENCHMARK_ADJUDICATION_ENABLED",
        "BENCHMARK_ADJUDICATION_MODEL",
        "BENCHMARK_ADJUDICATION_CASE_LIMIT",
        "BENCHMARK_ADJUDICATION_TURN_LIMIT",
        "BENCHMARK_ADJUDICATION_TOOL_CALL_LIMIT",
        "BENCHMARK_ADJUDICATION_TIMEOUT_SECONDS",
        "BENCHMARK_ADJUDICATION_RETRIES",
        "BENCHMARK_ADJUDICATION_RESULT_MAX_BYTES",
        "BENCHMARK_ARTIFACT_UPLOAD_ENABLED",
        "BENCHMARK_ARTIFACT_MAX_BYTES",
        "BENCHMARK_ARTIFACT_PART_SIZE_BYTES",
        "BENCHMARK_ARTIFACT_UPLOAD_RETRIES",
        "BENCHMARK_ARTIFACT_RETRY_BACKOFF_SECONDS",
        "BENCHMARK_ARTIFACT_UPLOAD_TIMEOUT_SECONDS",
        "BENCHMARK_ARTIFACT_UPLOAD_CONCURRENCY",
        "BENCHMARK_ARTIFACT_SECRET_PATTERNS",
    ):
        monkeypatch.delenv(key, raising=False)

    assert config.get_benchmark_enabled() is False
    assert config.get_benchmark_root() == ""
    assert config.get_benchmark_source_timeout_seconds() == 30
    assert config.get_benchmark_max_input_bytes() == 52_428_800
    assert config.get_benchmark_max_cases() == 50
    assert config.get_benchmark_max_configurations() == 10
    assert config.get_benchmark_max_repetitions() == 5
    assert config.get_benchmark_max_cells() == 250
    assert config.get_benchmark_max_concurrency() == 2
    assert config.get_benchmark_matrix_limit() == 20
    assert config.get_benchmark_case_limit() == 20
    assert config.get_benchmark_result_limit() == 20
    assert config.get_benchmark_timeout_seconds() == 300
    assert config.get_benchmark_retries() == 0
    assert config.get_benchmark_preview_max_chars() == 1000
    assert config.get_benchmark_inline_max_bytes() == 20000
    assert config.get_benchmark_adjudication_enabled() is False
    assert config.get_benchmark_adjudication_model() == "gpt-5.6-sol"
    assert config.get_benchmark_adjudication_case_limit() == 2
    assert config.get_benchmark_adjudication_turn_limit() == 1
    assert config.get_benchmark_adjudication_tool_call_limit() == 0
    assert config.get_benchmark_adjudication_timeout_seconds() == 60
    assert config.get_benchmark_adjudication_retries() == 1
    assert config.get_benchmark_adjudication_result_max_bytes() == 10000
    assert config.get_benchmark_artifact_upload_enabled() is False
    assert config.get_benchmark_artifact_max_bytes() == 10_485_760
    assert config.get_benchmark_artifact_part_size_bytes() == 8_388_608
    assert config.get_benchmark_artifact_upload_retries() == 3
    assert config.get_benchmark_artifact_retry_backoff_seconds() == 0.5
    assert config.get_benchmark_artifact_upload_timeout_seconds() == 30
    assert config.get_benchmark_artifact_upload_concurrency() == 2
    assert config.get_benchmark_artifact_secret_patterns() == ()


def test_benchmark_operational_overrides_are_bounded(monkeypatch):
    monkeypatch.setenv("BENCHMARK_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_ROOT", "  /tmp/custom-benchmarks  ")
    monkeypatch.setenv("BENCHMARK_SOURCE_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("BENCHMARK_MAX_INPUT_BYTES", "0")
    monkeypatch.setenv("BENCHMARK_MAX_CASES", "0")
    monkeypatch.setenv("BENCHMARK_MAX_CONFIGURATIONS", "3")
    monkeypatch.setenv("BENCHMARK_MAX_REPETITIONS", "4")
    monkeypatch.setenv("BENCHMARK_MAX_CELLS", "200")
    monkeypatch.setenv("BENCHMARK_MAX_CONCURRENCY", "0")
    monkeypatch.setenv("BENCHMARK_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("BENCHMARK_RETRIES", "-2")
    monkeypatch.setenv("BENCHMARK_ADJUDICATION_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_ADJUDICATION_MODEL", "  deployment-judge  ")
    monkeypatch.setenv("BENCHMARK_ADJUDICATION_CASE_LIMIT", "0")
    monkeypatch.setenv("BENCHMARK_ADJUDICATION_TOOL_CALL_LIMIT", "-1")
    monkeypatch.setenv("BENCHMARK_ARTIFACT_UPLOAD_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_ARTIFACT_MAX_BYTES", "0")
    monkeypatch.setenv("BENCHMARK_ARTIFACT_PART_SIZE_BYTES", "1")
    monkeypatch.setenv("BENCHMARK_ARTIFACT_UPLOAD_RETRIES", "-1")
    monkeypatch.setenv("BENCHMARK_ARTIFACT_RETRY_BACKOFF_SECONDS", "-1")
    monkeypatch.setenv("BENCHMARK_ARTIFACT_UPLOAD_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("BENCHMARK_ARTIFACT_UPLOAD_CONCURRENCY", "0")
    monkeypatch.setenv("BENCHMARK_ARTIFACT_SECRET_PATTERNS", " secret-a\nsecret-b ")

    assert config.get_benchmark_enabled() is True
    assert config.get_benchmark_root() == "/tmp/custom-benchmarks"
    assert config.get_benchmark_source_timeout_seconds() == 0.1
    assert config.get_benchmark_max_input_bytes() == 1
    assert config.get_benchmark_max_cases() == 1
    assert config.get_benchmark_max_configurations() == 3
    assert config.get_benchmark_max_repetitions() == 4
    assert config.get_benchmark_max_cells() == 200
    assert config.get_benchmark_max_concurrency() == 1
    assert config.get_benchmark_timeout_seconds() == 0.1
    assert config.get_benchmark_retries() == 0
    assert config.get_benchmark_adjudication_enabled() is True
    assert config.get_benchmark_adjudication_model() == "deployment-judge"
    assert config.get_benchmark_adjudication_case_limit() == 1
    assert config.get_benchmark_adjudication_tool_call_limit() == 0
    assert config.get_benchmark_artifact_upload_enabled() is True
    assert config.get_benchmark_artifact_max_bytes() == 1
    assert config.get_benchmark_artifact_part_size_bytes() == 5_242_880
    assert config.get_benchmark_artifact_upload_retries() == 0
    assert config.get_benchmark_artifact_retry_backoff_seconds() == 0
    assert config.get_benchmark_artifact_upload_timeout_seconds() == 0.1
    assert config.get_benchmark_artifact_upload_concurrency() == 1
    assert config.get_benchmark_artifact_secret_patterns() == ("secret-a", "secret-b")


def test_benchmark_oidc_defaults_and_overrides(monkeypatch):
    keys = (
        "BENCHMARK_OIDC_ISSUER_URL",
        "BENCHMARK_OIDC_AUDIENCE",
        "BENCHMARK_OIDC_ALLOWED_CLIENT_IDS",
        "BENCHMARK_OIDC_READ_SCOPES",
        "BENCHMARK_OPERATOR_READ_GROUPS",
        "BENCHMARK_OIDC_JWKS_TIMEOUT_SECONDS",
        "BENCHMARK_OIDC_JWKS_CACHE_TTL_SECONDS",
        "BENCHMARK_OIDC_CLOCK_SKEW_SECONDS",
    )
    for key in keys:
        monkeypatch.delenv(key, raising=False)

    assert config.get_benchmark_oidc_issuer_url() == ""
    assert config.get_benchmark_oidc_audience() == ""
    assert config.get_benchmark_oidc_allowed_client_ids() == ()
    assert config.get_benchmark_oidc_capability_scopes("benchmark:read") == ()
    assert config.get_benchmark_operator_capability_groups("benchmark:read") == ()
    assert config.get_benchmark_oidc_jwks_timeout_seconds() == 5
    assert config.get_benchmark_oidc_jwks_cache_ttl_seconds() == 300
    assert config.get_benchmark_oidc_clock_skew_seconds() == 60

    monkeypatch.setenv("BENCHMARK_OIDC_ISSUER_URL", " https://issuer.example.org/ ")
    monkeypatch.setenv("BENCHMARK_OIDC_AUDIENCE", " benchmark-api ")
    monkeypatch.setenv(
        "BENCHMARK_OIDC_ALLOWED_CLIENT_IDS", " portal-client, operator-client "
    )
    monkeypatch.setenv("BENCHMARK_OIDC_READ_SCOPES", " portal.read, alternate.read ")
    monkeypatch.setenv(
        "BENCHMARK_OPERATOR_READ_GROUPS", " benchmark-readers, benchmark-admins "
    )
    monkeypatch.setenv("BENCHMARK_OIDC_JWKS_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("BENCHMARK_OIDC_JWKS_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("BENCHMARK_OIDC_CLOCK_SKEW_SECONDS", "-1")

    assert config.get_benchmark_oidc_issuer_url() == "https://issuer.example.org/"
    assert config.get_benchmark_oidc_audience() == "benchmark-api"
    assert config.get_benchmark_oidc_allowed_client_ids() == (
        "portal-client",
        "operator-client",
    )
    assert config.get_benchmark_oidc_capability_scopes("benchmark:read") == (
        "portal.read",
        "alternate.read",
    )
    assert config.get_benchmark_operator_capability_groups("benchmark:read") == (
        "benchmark-readers",
        "benchmark-admins",
    )
    assert config.get_benchmark_oidc_jwks_timeout_seconds() == 0.1
    assert config.get_benchmark_oidc_jwks_cache_ttl_seconds() == 1
    assert config.get_benchmark_oidc_clock_skew_seconds() == 0


def test_every_benchmark_capability_has_independent_scope_and_group_config(monkeypatch):
    mappings = {
        "benchmark:read": "READ",
        "benchmark:run": "RUN",
        "benchmark:cancel": "CANCEL",
        "benchmark:delete": "DELETE",
        "benchmark:source:read": "SOURCE_READ",
    }
    for capability, suffix in mappings.items():
        monkeypatch.setenv(f"BENCHMARK_OIDC_{suffix}_SCOPES", f"scope.{suffix.lower()}")
        monkeypatch.setenv(f"BENCHMARK_OPERATOR_{suffix}_GROUPS", f"group-{suffix.lower()}")
        assert config.get_benchmark_oidc_capability_scopes(capability) == (
            f"scope.{suffix.lower()}",
        )
        assert config.get_benchmark_operator_capability_groups(capability) == (
            f"group-{suffix.lower()}",
        )

    with pytest.raises(ValueError, match="Unknown benchmark capability"):
        config.get_benchmark_oidc_capability_scopes("benchmark:unknown")
