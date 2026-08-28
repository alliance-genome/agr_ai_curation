"""Tests for strict model/provider config behavior."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.lib.openai_agents.config import (
    AgentConfig,
    build_default_model_retry,
    build_model_settings,
    get_agent_config,
    get_agent_studio_chat_recall_chunk_max_chars,
    get_agent_studio_chat_recall_page_size,
    get_agent_studio_flow_custom_instructions_max_chars,
    get_agent_studio_flow_description_max_chars,
    get_agent_studio_flow_inspection_chunk_max_chars,
    get_agent_studio_flow_inspection_page_limit,
    get_agent_studio_flow_max_steps,
    get_agent_studio_flow_name_max_chars,
    get_agent_studio_flow_output_filename_template_max_chars,
    get_agent_studio_flow_step_goal_max_chars,
    get_agent_studio_service_log_default_lines,
    get_agent_studio_service_log_default_lookback_minutes,
    get_agent_studio_service_log_max_lines,
    get_agent_studio_service_log_max_lookback_minutes,
    get_agent_studio_service_log_page_max_chars,
    get_agent_studio_service_log_timeout_seconds,
    get_agent_studio_trace_review_aggregate_page_size,
    get_agent_studio_trace_review_chunk_max_chars,
    get_agent_studio_trace_review_page_size,
    get_agent_studio_trace_review_summary_max_chars,
    get_api_key,
    get_base_url,
    get_background_task_observability_value_max_chars,
    get_domain_runtime_inspection_default_limit,
    get_domain_runtime_inspection_max_limit,
    get_flow_supervisor_parallel_tool_calls_enabled,
    get_flow_definition_max_nodes,
    get_groq_tool_call_max_retries,
    get_groq_tool_call_retry_delay_seconds,
    get_inspect_results_json_depth_limit,
    get_inspect_results_json_object_item_limit,
    get_inspect_results_list_page_size,
    get_inspect_results_validation_detail_list_limit,
    get_inspect_results_validation_page_size,
    get_openai_responses_websocket_ping_timeout_seconds,
    get_pdf_document_error_message_max_chars,
    get_pdf_max_file_size_bytes,
    get_pdf_no_job_orphan_batch_size,
    get_pdf_no_job_orphan_repair_apply,
    get_pdf_no_job_orphan_repair_retry_count,
    get_pdf_no_job_orphan_repair_timeout_seconds,
    get_pdf_no_job_orphan_threshold_seconds,
    get_pdf_upload_max_page_count,
    get_model_for_agent,
    get_sentry_log_event_level,
    get_supervisor_specialist_deadline_seconds,
    get_tool_failure_alert_summary_max_chars,
    get_trace_review_payload_preview_max_chars,
    get_weaviate_search_hybrid_alpha,
    get_weaviate_search_initial_limit,
    get_weaviate_search_mmr_enabled,
    get_weaviate_search_mmr_lambda,
    is_retryable_groq_tool_call_error,
    resolve_model_provider,
    supports_reasoning,
    supports_temperature,
)


def test_sentry_log_event_level_is_bounded_and_environment_configurable(
    monkeypatch,
    caplog,
):
    monkeypatch.delenv("SENTRY_LOG_EVENT_LEVEL", raising=False)
    assert get_sentry_log_event_level() is None

    monkeypatch.setenv("SENTRY_LOG_EVENT_LEVEL", "error")
    assert get_sentry_log_event_level() == 40

    monkeypatch.setenv("SENTRY_LOG_EVENT_LEVEL", "info")
    with caplog.at_level("WARNING"):
        assert get_sentry_log_event_level() is None
    assert "Log-event promotion remains disabled" in caplog.text


def test_supervisor_specialist_deadline_is_env_configured_and_positive(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_SPECIALIST_DEADLINE_SECONDS", raising=False)
    assert get_supervisor_specialist_deadline_seconds() == 900.0

    monkeypatch.setenv("SUPERVISOR_SPECIALIST_DEADLINE_SECONDS", "12.5")
    assert get_supervisor_specialist_deadline_seconds() == 12.5

    monkeypatch.setenv("SUPERVISOR_SPECIALIST_DEADLINE_SECONDS", "0")
    assert get_supervisor_specialist_deadline_seconds() == 0.1

    workspace_root = Path("/workspace")
    if not (workspace_root / ".env.example").exists():
        workspace_root = Path(__file__).resolve().parents[5]
    env_example = (workspace_root / ".env.example").read_text(encoding="utf-8")
    assert "SUPERVISOR_SPECIALIST_DEADLINE_SECONDS=900" in env_example
    assert "complete nested specialist invocation" in env_example
    assert "Preferred Agent and\n# Flow runs are not covered" in env_example


def test_weaviate_search_defaults_match_benchmark_winner(monkeypatch):
    for key in (
        "WEAVIATE_SEARCH_INITIAL_LIMIT",
        "WEAVIATE_SEARCH_HYBRID_ALPHA",
        "WEAVIATE_SEARCH_MMR_ENABLED",
        "WEAVIATE_SEARCH_MMR_LAMBDA",
    ):
        monkeypatch.delenv(key, raising=False)

    assert get_weaviate_search_initial_limit() == 50
    assert get_weaviate_search_hybrid_alpha() == 0.4
    assert get_weaviate_search_mmr_enabled() is False
    assert get_weaviate_search_mmr_lambda() == 0.5


def test_weaviate_search_defaults_are_bounded_and_configurable(monkeypatch):
    monkeypatch.setenv("WEAVIATE_SEARCH_INITIAL_LIMIT", "101")
    monkeypatch.setenv("WEAVIATE_SEARCH_HYBRID_ALPHA", "0.6")
    monkeypatch.setenv("WEAVIATE_SEARCH_MMR_ENABLED", "true")
    monkeypatch.setenv("WEAVIATE_SEARCH_MMR_LAMBDA", "nan")

    assert get_weaviate_search_initial_limit() == 100
    assert get_weaviate_search_hybrid_alpha() == 0.6
    assert get_weaviate_search_mmr_enabled() is True
    assert get_weaviate_search_mmr_lambda() == 0.5


@pytest.mark.parametrize(
    ("environment_name", "getter", "default"),
    [
        (
            "BACKGROUND_TASK_OBSERVABILITY_VALUE_MAX_CHARS",
            get_background_task_observability_value_max_chars,
            200,
        ),
        (
            "TOOL_FAILURE_ALERT_SUMMARY_MAX_CHARS",
            get_tool_failure_alert_summary_max_chars,
            500,
        ),
        (
            "AGENT_STUDIO_TRACE_REVIEW_PAGE_SIZE",
            get_agent_studio_trace_review_page_size,
            10,
        ),
        (
            "AGENT_STUDIO_TRACE_REVIEW_AGGREGATE_PAGE_SIZE",
            get_agent_studio_trace_review_aggregate_page_size,
            5,
        ),
        (
            "AGENT_STUDIO_SERVICE_LOG_DEFAULT_LINES",
            get_agent_studio_service_log_default_lines,
            20,
        ),
        (
            "AGENT_STUDIO_SERVICE_LOG_PAGE_MAX_CHARS",
            get_agent_studio_service_log_page_max_chars,
            8_000,
        ),
        (
            "TRACE_REVIEW_PAYLOAD_PREVIEW_MAX_CHARS",
            get_trace_review_payload_preview_max_chars,
            500,
        ),
        (
            "AGENT_STUDIO_SERVICE_LOG_DEFAULT_LOOKBACK_MINUTES",
            get_agent_studio_service_log_default_lookback_minutes,
            1_440,
        ),
        (
            "AGENT_STUDIO_TRACE_REVIEW_SUMMARY_MAX_CHARS",
            get_agent_studio_trace_review_summary_max_chars,
            200,
        ),
        (
            "AGENT_STUDIO_TRACE_REVIEW_CHUNK_MAX_CHARS",
            get_agent_studio_trace_review_chunk_max_chars,
            8_000,
        ),
    ],
)
def test_character_and_page_limits_use_env_with_invalid_fallback(
    monkeypatch,
    environment_name,
    getter,
    default,
):
    monkeypatch.delenv(environment_name, raising=False)
    assert getter() == default

    monkeypatch.setenv(environment_name, "17")
    assert getter() == 17

    monkeypatch.setenv(environment_name, "invalid")
    assert getter() == default

    monkeypatch.setenv(environment_name, "0")
    assert getter() == 1


def test_service_log_maxima_are_configurable_and_not_below_defaults(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_SERVICE_LOG_DEFAULT_LINES", "20")
    monkeypatch.setenv("AGENT_STUDIO_SERVICE_LOG_MAX_LINES", "10")
    monkeypatch.setenv("AGENT_STUDIO_SERVICE_LOG_DEFAULT_LOOKBACK_MINUTES", "1440")
    monkeypatch.setenv("AGENT_STUDIO_SERVICE_LOG_MAX_LOOKBACK_MINUTES", "60")
    assert get_agent_studio_service_log_max_lines() == 20
    assert get_agent_studio_service_log_max_lookback_minutes() == 1440

    monkeypatch.setenv("AGENT_STUDIO_SERVICE_LOG_MAX_LINES", "75")
    monkeypatch.setenv("AGENT_STUDIO_SERVICE_LOG_MAX_LOOKBACK_MINUTES", "2880")
    assert get_agent_studio_service_log_max_lines() == 75
    assert get_agent_studio_service_log_max_lookback_minutes() == 2880


def test_service_log_timeout_is_configurable_and_positive(monkeypatch):
    monkeypatch.delenv("AGENT_STUDIO_SERVICE_LOG_TIMEOUT_SECONDS", raising=False)
    assert get_agent_studio_service_log_timeout_seconds() == 15.0
    monkeypatch.setenv("AGENT_STUDIO_SERVICE_LOG_TIMEOUT_SECONDS", "2.5")
    assert get_agent_studio_service_log_timeout_seconds() == 2.5
    monkeypatch.setenv("AGENT_STUDIO_SERVICE_LOG_TIMEOUT_SECONDS", "0")
    assert get_agent_studio_service_log_timeout_seconds() == 0.1


@pytest.mark.parametrize(
    ("environment_name", "getter", "default", "override", "maximum"),
    [
        (
            "AGENT_STUDIO_FLOW_MAX_STEPS",
            get_agent_studio_flow_max_steps,
            30,
            4,
            30,
        ),
        (
            "AGENT_STUDIO_FLOW_NAME_MAX_CHARS",
            get_agent_studio_flow_name_max_chars,
            255,
            80,
            255,
        ),
        (
            "AGENT_STUDIO_FLOW_DESCRIPTION_MAX_CHARS",
            get_agent_studio_flow_description_max_chars,
            2_000,
            900,
            2_000,
        ),
        (
            "AGENT_STUDIO_FLOW_STEP_GOAL_MAX_CHARS",
            get_agent_studio_flow_step_goal_max_chars,
            500,
            120,
            500,
        ),
        (
            "AGENT_STUDIO_FLOW_CUSTOM_INSTRUCTIONS_MAX_CHARS",
            get_agent_studio_flow_custom_instructions_max_chars,
            2_000,
            700,
            2_000,
        ),
        (
            "AGENT_STUDIO_FLOW_OUTPUT_FILENAME_TEMPLATE_MAX_CHARS",
            get_agent_studio_flow_output_filename_template_max_chars,
            255,
            90,
            255,
        ),
        (
            "AGENT_STUDIO_FLOW_INSPECTION_PAGE_LIMIT",
            get_agent_studio_flow_inspection_page_limit,
            20,
            7,
            None,
        ),
        (
            "AGENT_STUDIO_FLOW_INSPECTION_CHUNK_MAX_CHARS",
            get_agent_studio_flow_inspection_chunk_max_chars,
            4_000,
            800,
            None,
        ),
    ],
)
def test_agent_studio_flow_admission_limits(
    monkeypatch,
    environment_name,
    getter,
    default,
    override,
    maximum,
):
    monkeypatch.delenv(environment_name, raising=False)
    assert getter() == default

    monkeypatch.setenv(environment_name, str(override))
    assert getter() == override

    monkeypatch.setenv(environment_name, "0")
    assert getter() == 1

    if maximum is not None:
        monkeypatch.setenv(environment_name, str(maximum + 1))
        assert getter() == maximum


def test_agent_studio_flow_step_limit_respects_canonical_node_capacity(monkeypatch):
    monkeypatch.delenv("FLOW_DEFINITION_MAX_NODES", raising=False)
    monkeypatch.delenv("AGENT_STUDIO_FLOW_MAX_STEPS", raising=False)
    assert get_flow_definition_max_nodes() == 31
    assert get_agent_studio_flow_max_steps() == 30

    monkeypatch.setenv("FLOW_DEFINITION_MAX_NODES", "10")
    monkeypatch.setenv("AGENT_STUDIO_FLOW_MAX_STEPS", "30")

    assert get_flow_definition_max_nodes() == 10
    assert get_agent_studio_flow_max_steps() == 9

    monkeypatch.setenv("FLOW_DEFINITION_MAX_NODES", "1")
    assert get_flow_definition_max_nodes() == 2
    assert get_agent_studio_flow_max_steps() == 1


def test_agent_studio_chat_recall_limits_are_environment_configurable(monkeypatch):
    monkeypatch.delenv("AGENT_STUDIO_CHAT_RECALL_PAGE_SIZE", raising=False)
    monkeypatch.delenv("AGENT_STUDIO_CHAT_RECALL_CHUNK_MAX_CHARS", raising=False)
    assert get_agent_studio_chat_recall_page_size() == 10
    assert get_agent_studio_chat_recall_chunk_max_chars() == 8_000

    monkeypatch.setenv("AGENT_STUDIO_CHAT_RECALL_PAGE_SIZE", "4")
    monkeypatch.setenv("AGENT_STUDIO_CHAT_RECALL_CHUNK_MAX_CHARS", "900")
    assert get_agent_studio_chat_recall_page_size() == 4
    assert get_agent_studio_chat_recall_chunk_max_chars() == 900

    monkeypatch.setenv("AGENT_STUDIO_CHAT_RECALL_PAGE_SIZE", "0")
    monkeypatch.setenv("AGENT_STUDIO_CHAT_RECALL_CHUNK_MAX_CHARS", "0")
    assert get_agent_studio_chat_recall_page_size() == 1
    assert get_agent_studio_chat_recall_chunk_max_chars() == 1


def test_agent_studio_flow_limit_clamps_are_reported(monkeypatch, caplog):
    monkeypatch.delenv("FLOW_DEFINITION_MAX_NODES", raising=False)
    monkeypatch.setenv("AGENT_STUDIO_FLOW_NAME_MAX_CHARS", "256")
    monkeypatch.setenv("AGENT_STUDIO_FLOW_MAX_STEPS", "31")

    assert get_agent_studio_flow_name_max_chars() == 255
    assert get_agent_studio_flow_max_steps() == 30
    assert "exceeds canonical maximum 255" in caplog.text
    assert "exceeds the canonical authored-step capacity 30" in caplog.text


def test_flow_supervisor_parallel_tool_calls_default_disabled(monkeypatch):
    monkeypatch.delenv("FLOW_SUPERVISOR_PARALLEL_TOOL_CALLS_ENABLED", raising=False)

    assert get_flow_supervisor_parallel_tool_calls_enabled() is False


def test_flow_supervisor_parallel_tool_calls_honors_override(monkeypatch):
    monkeypatch.setenv("FLOW_SUPERVISOR_PARALLEL_TOOL_CALLS_ENABLED", "true")

    assert get_flow_supervisor_parallel_tool_calls_enabled() is True


class TestGetAgentConfig:
    """Tests for the get_agent_config function."""

    def test_get_agent_config_returns_config(self):
        config = get_agent_config("gene")
        assert isinstance(config, AgentConfig)
        assert hasattr(config, "model")

    def test_get_agent_config_uses_registry_defaults(self):
        config = get_agent_config("gene")
        assert config.model is not None

    def test_get_agent_config_respects_env_override(self):
        with patch.dict(os.environ, {"AGENT_GENE_MODEL": "gpt-4o-test"}):
            config = get_agent_config("gene")
            assert config.model == "gpt-4o-test"

    def test_get_agent_config_unknown_agent_uses_fallback(self):
        config = get_agent_config("nonexistent_agent")
        assert config.model is not None

    def test_get_agent_config_env_var_pattern(self):
        with patch.dict(
            os.environ,
            {"AGENT_CUSTOM_MODEL": "custom-model", "AGENT_CUSTOM_REASONING": "high"},
        ):
            config = get_agent_config("custom")
            assert config.model == "custom-model"
            assert config.reasoning == "high"

    def test_get_agent_config_temperature_override(self):
        with patch.dict(os.environ, {"AGENT_TEST_TEMPERATURE": "0.7"}):
            config = get_agent_config("test")
            assert config.temperature == 0.7

    def test_get_agent_config_tool_choice_override(self):
        with patch.dict(os.environ, {"AGENT_TEST_TOOL_CHOICE": "required"}):
            config = get_agent_config("test")
            assert config.tool_choice == "required"


def test_get_agent_config_prefers_registry_model_over_global_fallback(monkeypatch):
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.AGENT_REGISTRY",
        {"gene_extractor": {"config_defaults": {"model": "gpt-5.4-mini"}}},
        raising=False,
    )
    monkeypatch.setattr("src.lib.openai_agents.config.get_default_model", lambda: "gpt-5.5")

    # DEFAULT_AGENT_REASONING is required (fail-fast); the registry entry sets no
    # reasoning, so provide it here while asserting the registry model still wins.
    with patch.dict(os.environ, {"DEFAULT_AGENT_REASONING": "low"}, clear=True):
        config = get_agent_config("gene_extractor")

    assert config.model == "gpt-5.4-mini"


def test_get_agent_config_env_override_beats_registry_model(monkeypatch):
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.AGENT_REGISTRY",
        {"gene_extractor": {"config_defaults": {"model": "gpt-5.4-mini"}}},
        raising=False,
    )
    monkeypatch.setattr("src.lib.openai_agents.config.get_default_model", lambda: "gpt-5.5")

    with patch.dict(
        os.environ,
        {"AGENT_GENE_EXTRACTOR_MODEL": "gpt-5.5", "DEFAULT_AGENT_REASONING": "low"},
        clear=True,
    ):
        config = get_agent_config("gene_extractor")

    assert config.model == "gpt-5.5"


def test_get_agent_config_does_not_evaluate_unused_global_defaults(monkeypatch):
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.AGENT_REGISTRY",
        {
            "supervisor": {
                "config_defaults": {
                    "model": "gpt-5.5",
                    "temperature": None,
                    "reasoning": "medium",
                    "tool_choice": "auto",
                }
            }
        },
        raising=False,
    )

    def fail_default(*_args, **_kwargs):
        raise AssertionError("unused global default was evaluated")

    monkeypatch.setattr("src.lib.openai_agents.config.get_default_model", fail_default)
    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_default_temperature", fail_default
    )
    monkeypatch.setattr("src.lib.openai_agents.config.get_default_reasoning", fail_default)

    with patch.dict(os.environ, {}, clear=True):
        config = get_agent_config("supervisor")

    assert config.model == "gpt-5.5"
    assert config.temperature is None
    assert config.reasoning == "medium"
    assert config.tool_choice == "auto"


def test_resolve_model_provider_uses_model_catalog_and_provider_registry(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: SimpleNamespace(provider="groq"),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: SimpleNamespace(provider_id=provider_id)
        if provider_id == "groq"
        else None,
    )

    assert resolve_model_provider("stub-groq-model") == "groq"


def test_resolve_model_provider_requires_known_model(monkeypatch):
    monkeypatch.setattr("src.lib.config.models_loader.get_model", lambda _model_id: None)
    with pytest.raises(ValueError, match="Unknown model_id"):
        resolve_model_provider("unknown-model")


def test_resolve_model_provider_rejects_unknown_override(monkeypatch):
    monkeypatch.setattr("src.lib.config.providers_loader.get_provider", lambda _provider_id: None)
    with pytest.raises(ValueError, match="Unknown provider_id"):
        resolve_model_provider("gpt-5.4-mini", provider_override="not-real")


def test_support_flags_require_model_catalog(monkeypatch):
    monkeypatch.setattr("src.lib.config.models_loader.get_model", lambda _model_id: None)
    with pytest.raises(ValueError, match="Unknown model_id"):
        supports_reasoning("custom-model")
    with pytest.raises(ValueError, match="Unknown model_id"):
        supports_temperature("custom-model")


def test_support_flags_read_model_catalog(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: SimpleNamespace(supports_reasoning=False, supports_temperature=True),
    )
    assert supports_reasoning("custom-model") is False
    assert supports_temperature("custom-model") is True


def test_is_retryable_groq_tool_call_error_matches_known_signatures():
    assert is_retryable_groq_tool_call_error(
        RuntimeError("GroqException - Failed to parse tool call arguments as JSON")
    ) is True
    assert is_retryable_groq_tool_call_error(
        RuntimeError("tool_use_failed: Tool call arguments are not valid JSON")
    ) is True
    assert is_retryable_groq_tool_call_error(RuntimeError("something else entirely")) is False


def test_groq_tool_call_retry_settings_parse_env(monkeypatch):
    monkeypatch.setenv("GROQ_TOOL_CALL_MAX_RETRIES", "3")
    monkeypatch.setenv("GROQ_TOOL_CALL_RETRY_DELAY_SECONDS", "1.5")

    assert get_groq_tool_call_max_retries() == 3
    assert get_groq_tool_call_retry_delay_seconds() == pytest.approx(1.5)


def test_groq_tool_call_retry_settings_clamp_invalid_values(monkeypatch):
    monkeypatch.setenv("GROQ_TOOL_CALL_MAX_RETRIES", "-8")
    monkeypatch.setenv("GROQ_TOOL_CALL_RETRY_DELAY_SECONDS", "-3.0")

    assert get_groq_tool_call_max_retries() == 0
    assert get_groq_tool_call_retry_delay_seconds() == pytest.approx(0.0)


def test_build_model_settings_uses_provider_parallel_tool_policy(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: SimpleNamespace(
            provider="gemini",
            supports_reasoning=False,
            supports_temperature=True,
        ),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: (
            SimpleNamespace(provider_id="gemini", supports_parallel_tool_calls=False)
            if provider_id == "gemini"
            else None
        ),
    )

    settings = build_model_settings(
        model="gemini-3-pro-preview",
        parallel_tool_calls=True,
    )
    assert settings is not None
    assert settings.parallel_tool_calls is False


def test_normalize_reasoning_effort_drops_invalid_values():
    from src.lib.openai_agents.config import normalize_reasoning_effort

    for valid in ("minimal", "low", "medium", "high", "xhigh"):
        assert normalize_reasoning_effort(valid) == valid
    assert normalize_reasoning_effort("HIGH") == "high"
    assert normalize_reasoning_effort("  low  ") == "low"
    for invalid in ("disabled", "none", "off", "", "bogus", None):
        assert normalize_reasoning_effort(invalid) is None


def test_build_model_settings_drops_invalid_reasoning_without_crashing(monkeypatch):
    # Regression (0.7.2): a flow/agent carrying reasoning='disabled' must NOT crash
    # Reasoning(effort=...) construction (the flow terminal-formatter projection path).
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: SimpleNamespace(
            provider="openai",
            supports_reasoning=True,
            supports_temperature=False,
        ),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: (
            SimpleNamespace(provider_id="openai", supports_parallel_tool_calls=True)
            if provider_id == "openai"
            else None
        ),
    )

    settings = build_model_settings(
        model="gpt-5.4-mini",
        reasoning_effort="disabled",  # type: ignore[arg-type]  # deliberately invalid
    )
    assert settings is not None
    assert settings.reasoning is None


def test_build_model_settings_applies_groq_safety_defaults(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: SimpleNamespace(
            provider="groq",
            supports_reasoning=False,
            supports_temperature=True,
        ),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: (
            SimpleNamespace(provider_id="groq", supports_parallel_tool_calls=True)
            if provider_id == "groq"
            else None
        ),
    )
    monkeypatch.delenv("GROQ_PARALLEL_TOOL_CALLS_ENABLED", raising=False)
    monkeypatch.delenv("GROQ_TOOL_TEMPERATURE_MAX", raising=False)

    settings = build_model_settings(
        model="stub-groq-model",
        temperature=0.9,
        parallel_tool_calls=True,
    )
    assert settings is not None
    assert settings.parallel_tool_calls is False
    assert settings.temperature == pytest.approx(0.0)


def test_build_model_settings_allows_groq_parallel_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: SimpleNamespace(
            provider="groq",
            supports_reasoning=False,
            supports_temperature=True,
        ),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: (
            SimpleNamespace(provider_id="groq", supports_parallel_tool_calls=True)
            if provider_id == "groq"
            else None
        ),
    )
    monkeypatch.setenv("GROQ_PARALLEL_TOOL_CALLS_ENABLED", "true")

    settings = build_model_settings(
        model="stub-groq-model",
        temperature=0.2,
        parallel_tool_calls=True,
    )
    assert settings is not None
    assert settings.parallel_tool_calls is True
    assert settings.temperature == pytest.approx(0.0)


def test_build_model_settings_keeps_openai_behavior_unchanged(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: SimpleNamespace(
            provider="openai",
            supports_reasoning=False,
            supports_temperature=True,
        ),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: (
            SimpleNamespace(provider_id="openai", supports_parallel_tool_calls=True)
            if provider_id == "openai"
            else None
        ),
    )
    monkeypatch.setenv("GROQ_PARALLEL_TOOL_CALLS_ENABLED", "false")
    monkeypatch.setenv("GROQ_TOOL_TEMPERATURE_MAX", "0.1")

    settings = build_model_settings(
        model="gpt-4o",
        temperature=0.8,
        parallel_tool_calls=True,
    )
    assert settings is not None
    assert settings.parallel_tool_calls is True
    assert settings.temperature == pytest.approx(0.8)


def _patch_openai_model(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: SimpleNamespace(
            provider="openai",
            supports_reasoning=False,
            supports_temperature=True,
        ),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: (
            SimpleNamespace(provider_id="openai", supports_parallel_tool_calls=True)
            if provider_id == "openai"
            else None
        ),
    )


def test_build_model_settings_enables_model_retry_by_default(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL_MAX_RETRIES", raising=False)
    _patch_openai_model(monkeypatch)

    settings = build_model_settings(model="gpt-5.5")

    assert settings.retry is not None
    assert settings.retry.max_retries == 3
    assert settings.retry.backoff is not None
    assert settings.retry.backoff.jitter is True
    assert settings.retry.policy is not None


def test_build_model_settings_retry_disabled_when_max_retries_zero(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "0")
    _patch_openai_model(monkeypatch)

    settings = build_model_settings(model="gpt-5.5")

    assert settings.retry is None


def _responses_websocket_error(
    *,
    event_type="error",
    error_type="service_unavailable_error",
    code="server_is_overloaded",
):
    from agents.models.openai_responses import ResponsesWebSocketError

    return ResponsesWebSocketError(
        {
            "type": event_type,
            "sequence_number": 2,
            "error": {
                "type": error_type,
                "code": code,
                "message": "redacted fixture message",
            },
        }
    )


def _zero_model_retry_delay(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_RETRY_INITIAL_DELAY", "0")
    monkeypatch.setenv("OPENAI_MODEL_RETRY_MAX_DELAY", "0")


@pytest.mark.asyncio
async def test_responses_websocket_overload_retries_then_succeeds(monkeypatch, caplog):
    from agents.run_internal.model_retry import stream_response_with_retry

    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "2")
    _zero_model_retry_delay(monkeypatch)
    attempts = 0
    rewinds = 0

    def get_stream():
        async def stream():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                yield {"type": "response.created"}
                yield {"type": "response.in_progress"}
                raise _responses_websocket_error()
            yield {"type": "response.completed"}

        return stream()

    async def rewind():
        nonlocal rewinds
        rewinds += 1

    with caplog.at_level("INFO", logger="src.lib.openai_agents.config"):
        events = [
            event
            async for event in stream_response_with_retry(
                get_stream=get_stream,
                rewind=rewind,
                retry_settings=build_default_model_retry(),
                get_retry_advice=lambda _request: None,
                previous_response_id="response_1",
                conversation_id=None,
            )
        ]

    assert events == [
        {"type": "response.created"},
        {"type": "response.in_progress"},
        {"type": "response.completed"},
    ]
    assert attempts == 2
    assert rewinds == 1
    assert "retry 1/2; retry_budget_exhausted=False" in caplog.text
    assert "redacted fixture message" not in caplog.text


@pytest.mark.asyncio
async def test_responses_websocket_overload_exhausts_configured_retries(
    monkeypatch,
    caplog,
):
    from agents.models.openai_responses import ResponsesWebSocketError
    from agents.run_internal.model_retry import stream_response_with_retry

    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "2")
    _zero_model_retry_delay(monkeypatch)
    attempts = 0
    rewinds = 0

    def get_stream():
        async def stream():
            nonlocal attempts
            attempts += 1
            raise _responses_websocket_error()
            yield  # pragma: no cover - keeps this an async generator

        return stream()

    async def rewind():
        nonlocal rewinds
        rewinds += 1

    with caplog.at_level("INFO", logger="src.lib.openai_agents.config"):
        with pytest.raises(ResponsesWebSocketError) as exc_info:
            async for _event in stream_response_with_retry(
                get_stream=get_stream,
                rewind=rewind,
                retry_settings=build_default_model_retry(),
                get_retry_advice=lambda _request: None,
                previous_response_id="response_1",
                conversation_id=None,
            ):
                pass

    assert attempts == 3
    assert rewinds == 2
    assert exc_info.value.error_type == "service_unavailable_error"
    assert exc_info.value.code == "server_is_overloaded"
    assert "retry 2/2; retry_budget_exhausted=True" in caplog.text
    assert "redacted fixture message" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        _responses_websocket_error(
            error_type="authentication_error",
            code="invalid_api_key",
        ),
        _responses_websocket_error(event_type="response.error"),
    ],
)
async def test_non_retryable_responses_websocket_frames_fail_immediately(
    monkeypatch,
    error,
):
    from agents.models.openai_responses import ResponsesWebSocketError
    from agents.run_internal.model_retry import stream_response_with_retry

    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "2")
    _zero_model_retry_delay(monkeypatch)
    attempts = 0
    rewinds = 0

    def get_stream():
        async def stream():
            nonlocal attempts
            attempts += 1
            raise error
            yield  # pragma: no cover - keeps this an async generator

        return stream()

    async def rewind():
        nonlocal rewinds
        rewinds += 1

    with pytest.raises(ResponsesWebSocketError):
        async for _event in stream_response_with_retry(
            get_stream=get_stream,
            rewind=rewind,
            retry_settings=build_default_model_retry(),
            get_retry_advice=lambda _request: None,
            previous_response_id="response_1",
            conversation_id=None,
        ):
            pass

    assert attempts == 1
    assert rewinds == 0


@pytest.mark.asyncio
async def test_responses_websocket_overload_is_not_retried_after_usable_event(monkeypatch):
    from agents.models.openai_responses import ResponsesWebSocketError
    from agents.run_internal.model_retry import stream_response_with_retry

    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "2")
    _zero_model_retry_delay(monkeypatch)
    attempts = 0
    rewinds = 0

    def get_stream():
        async def stream():
            nonlocal attempts
            attempts += 1
            yield {"type": "response.output_text.delta", "delta": "usable"}
            raise _responses_websocket_error()

        return stream()

    async def rewind():
        nonlocal rewinds
        rewinds += 1

    events = stream_response_with_retry(
        get_stream=get_stream,
        rewind=rewind,
        retry_settings=build_default_model_retry(),
        get_retry_advice=lambda _request: None,
        previous_response_id="response_1",
        conversation_id=None,
    )
    assert await anext(events) == {
        "type": "response.output_text.delta",
        "delta": "usable",
    }
    with pytest.raises(ResponsesWebSocketError):
        await anext(events)

    assert attempts == 1
    assert rewinds == 0


def test_openai_responses_websocket_ping_timeout_invalid_env_uses_default(monkeypatch):
    monkeypatch.setenv("OPENAI_RESPONSES_WEBSOCKET_PING_TIMEOUT_SECONDS", "not-a-float")

    assert get_openai_responses_websocket_ping_timeout_seconds() is None


def test_pdf_max_file_size_default_is_500mb(monkeypatch):
    monkeypatch.delenv("PDF_MAX_FILE_SIZE_BYTES", raising=False)

    assert get_pdf_max_file_size_bytes() == 500 * 1024 * 1024


def test_pdf_document_error_message_max_chars_is_configurable(monkeypatch):
    monkeypatch.delenv("PDF_DOCUMENT_ERROR_MESSAGE_MAX_CHARS", raising=False)
    assert get_pdf_document_error_message_max_chars() == 1000

    monkeypatch.setenv("PDF_DOCUMENT_ERROR_MESSAGE_MAX_CHARS", "17")
    assert get_pdf_document_error_message_max_chars() == 17

    monkeypatch.setenv("PDF_DOCUMENT_ERROR_MESSAGE_MAX_CHARS", "0")
    assert get_pdf_document_error_message_max_chars() == 1


def test_pdf_no_job_orphan_repair_limits_and_mode_are_configurable(monkeypatch):
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_THRESHOLD_SECONDS", "600")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_BATCH_SIZE", "7")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_REPAIR_TIMEOUT_SECONDS", "9")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_REPAIR_RETRY_COUNT", "4")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_REPAIR_APPLY", "true")

    assert get_pdf_no_job_orphan_threshold_seconds() == 600
    assert get_pdf_no_job_orphan_batch_size() == 7
    assert get_pdf_no_job_orphan_repair_timeout_seconds() == 9
    assert get_pdf_no_job_orphan_repair_retry_count() == 4
    assert get_pdf_no_job_orphan_repair_apply() is True


def test_pdf_no_job_orphan_repair_limits_preserve_safety_floors(monkeypatch):
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_THRESHOLD_SECONDS", "1")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_BATCH_SIZE", "0")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_REPAIR_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_REPAIR_RETRY_COUNT", "-1")
    monkeypatch.setenv("PDF_NO_JOB_ORPHAN_REPAIR_APPLY", "false")

    assert get_pdf_no_job_orphan_threshold_seconds() == 300
    assert get_pdf_no_job_orphan_batch_size() == 1
    assert get_pdf_no_job_orphan_repair_timeout_seconds() == 1
    assert get_pdf_no_job_orphan_repair_retry_count() == 0
    assert get_pdf_no_job_orphan_repair_apply() is False


def test_pdf_max_file_size_env_override_can_raise_former_ceiling(monkeypatch):
    monkeypatch.setenv("PDF_MAX_FILE_SIZE_BYTES", str(600 * 1024 * 1024))

    assert get_pdf_max_file_size_bytes() == 600 * 1024 * 1024


def test_pdf_max_file_size_accepts_persisted_integer_boundary(monkeypatch):
    monkeypatch.setenv("PDF_MAX_FILE_SIZE_BYTES", "2147483647")

    assert get_pdf_max_file_size_bytes() == 2_147_483_647


def test_pdf_max_file_size_accepts_leading_zero_representation(monkeypatch):
    monkeypatch.setenv("PDF_MAX_FILE_SIZE_BYTES", "0000000005")

    assert get_pdf_max_file_size_bytes() == 5


@pytest.mark.parametrize(
    "configured_value",
    ["", "0", "-1", "500MB", "2147483648", "١٢٣"],
)
def test_pdf_max_file_size_rejects_invalid_or_unpersistable_values(
    monkeypatch,
    configured_value,
):
    monkeypatch.setenv("PDF_MAX_FILE_SIZE_BYTES", configured_value)

    with pytest.raises(ValueError, match="PDF_MAX_FILE_SIZE_BYTES"):
        get_pdf_max_file_size_bytes()


def test_pdf_upload_max_page_count_defaults_to_300(monkeypatch):
    monkeypatch.delenv("PDF_UPLOAD_MAX_PAGE_COUNT", raising=False)

    assert get_pdf_upload_max_page_count() == 300


def test_pdf_upload_max_page_count_env_override(monkeypatch):
    monkeypatch.setenv("PDF_UPLOAD_MAX_PAGE_COUNT", "120")

    assert get_pdf_upload_max_page_count() == 120


@pytest.mark.parametrize("configured_value", ["0", "-5"])
def test_pdf_upload_max_page_count_defends_positive_invariant(
    monkeypatch,
    configured_value,
):
    monkeypatch.setenv("PDF_UPLOAD_MAX_PAGE_COUNT", configured_value)

    assert get_pdf_upload_max_page_count() == 1


def test_inspect_results_display_limits_are_env_configured(monkeypatch):
    monkeypatch.setenv("INSPECT_RESULTS_LIST_PAGE_SIZE", "7")
    monkeypatch.setenv("INSPECT_RESULTS_VALIDATION_PAGE_SIZE", "9")
    monkeypatch.setenv("INSPECT_RESULTS_VALIDATION_DETAIL_LIST_LIMIT", "11")
    monkeypatch.setenv("INSPECT_RESULTS_JSON_DEPTH_LIMIT", "13")
    monkeypatch.setenv("INSPECT_RESULTS_JSON_OBJECT_ITEM_LIMIT", "15")

    assert get_inspect_results_list_page_size() == 7
    assert get_inspect_results_validation_page_size() == 9
    assert get_inspect_results_validation_detail_list_limit() == 11
    assert get_inspect_results_json_depth_limit() == 13
    assert get_inspect_results_json_object_item_limit() == 15


def test_inspect_results_display_limits_clamp_to_positive(monkeypatch):
    monkeypatch.setenv("INSPECT_RESULTS_LIST_PAGE_SIZE", "0")
    monkeypatch.setenv("INSPECT_RESULTS_VALIDATION_PAGE_SIZE", "-3")
    monkeypatch.setenv("INSPECT_RESULTS_VALIDATION_DETAIL_LIST_LIMIT", "0")
    monkeypatch.setenv("INSPECT_RESULTS_JSON_DEPTH_LIMIT", "-1")
    monkeypatch.setenv("INSPECT_RESULTS_JSON_OBJECT_ITEM_LIMIT", "0")

    assert get_inspect_results_list_page_size() == 1
    assert get_inspect_results_validation_page_size() == 1
    assert get_inspect_results_validation_detail_list_limit() == 1
    assert get_inspect_results_json_depth_limit() == 1
    assert get_inspect_results_json_object_item_limit() == 1


def test_domain_runtime_inspection_page_limits_are_env_configured(monkeypatch):
    monkeypatch.setenv("DOMAIN_RUNTIME_INSPECTION_DEFAULT_LIMIT", "5")
    monkeypatch.setenv("DOMAIN_RUNTIME_INSPECTION_MAX_LIMIT", "7")

    assert get_domain_runtime_inspection_default_limit() == 5
    assert get_domain_runtime_inspection_max_limit() == 7


def test_domain_runtime_inspection_page_limits_clamp_to_positive(monkeypatch):
    monkeypatch.setenv("DOMAIN_RUNTIME_INSPECTION_DEFAULT_LIMIT", "0")
    monkeypatch.setenv("DOMAIN_RUNTIME_INSPECTION_MAX_LIMIT", "-1")

    assert get_domain_runtime_inspection_default_limit() == 1
    assert get_domain_runtime_inspection_max_limit() == 1


def test_get_api_key_uses_provider_env_mapping(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_default_runner_provider",
        lambda: SimpleNamespace(provider_id="openai"),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: (
            SimpleNamespace(
                provider_id="openai",
                api_key_env="OPENAI_API_KEY",
                base_url_env="OPENAI_BASE_URL",
                default_base_url="",
            )
            if provider_id == "openai"
            else None
        ),
    )

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        assert get_api_key() == "test-key"


def test_get_base_url_prefers_env_then_default(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: (
            SimpleNamespace(
                provider_id="groq",
                api_key_env="GROQ_API_KEY",
                base_url_env="GROQ_BASE_URL",
                default_base_url="https://fallback.groq.local/v1",
            )
            if provider_id == "groq"
            else None
        ),
    )

    with patch.dict(os.environ, {"GROQ_BASE_URL": "https://env.groq.local/v1"}):
        assert get_base_url("groq") == "https://env.groq.local/v1"

    with patch.dict(os.environ, {}, clear=True):
        assert get_base_url("groq") == "https://fallback.groq.local/v1"


@pytest.mark.parametrize("api_key", [None, "   "])
def test_get_model_for_agent_requires_native_provider_key(monkeypatch, api_key):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: SimpleNamespace(provider="openai"),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: (
            SimpleNamespace(
                provider_id="openai",
                driver="openai_native",
                api_key_env="OPENAI_API_KEY",
                base_url_env="OPENAI_BASE_URL",
                default_base_url=None,
                litellm_prefix=None,
                drop_params=False,
                supports_parallel_tool_calls=True,
            )
            if provider_id == "openai"
            else None
        ),
    )
    if api_key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", api_key)

    with pytest.raises(ValueError, match="OPENAI_API_KEY environment variable not set"):
        get_model_for_agent("gpt-test")


def test_get_model_for_agent_supports_synthetic_litellm_provider(monkeypatch):
    captured = {}

    class FakeLitellmModel:
        def __init__(self, model, base_url=None, api_key=None):
            captured["model"] = model
            captured["base_url"] = base_url
            captured["api_key"] = api_key

    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: SimpleNamespace(provider="org_custom"),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: (
            SimpleNamespace(
                provider_id="org_custom",
                driver="litellm",
                api_key_env="ORG_CUSTOM_API_KEY",
                base_url_env="ORG_CUSTOM_BASE_URL",
                default_base_url="https://org-custom.example/v1",
                litellm_prefix="acme",
                drop_params=True,
                supports_parallel_tool_calls=True,
            )
            if provider_id == "org_custom"
            else None
        ),
    )
    monkeypatch.setattr(
        "agents.extensions.models.litellm_model.LitellmModel",
        FakeLitellmModel,
    )

    with patch.dict(
        os.environ,
        {
            "ORG_CUSTOM_API_KEY": "org-key",
            "ORG_CUSTOM_BASE_URL": "https://runtime-org.example/v1",
        },
    ):
        model = get_model_for_agent("model-x")

    assert model is not None
    assert captured["model"] == "acme/model-x"
    assert captured["base_url"] == "https://runtime-org.example/v1"
    assert captured["api_key"] == "org-key"


def test_get_model_for_agent_keeps_namespaced_model_with_groq_prefix(monkeypatch):
    captured = {}

    class FakeLitellmModel:
        def __init__(self, model, base_url=None, api_key=None):
            captured["model"] = model
            captured["base_url"] = base_url
            captured["api_key"] = api_key

    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: SimpleNamespace(provider="groq"),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: (
            SimpleNamespace(
                provider_id="groq",
                driver="litellm",
                api_key_env="GROQ_API_KEY",
                base_url_env="GROQ_BASE_URL",
                default_base_url="https://api.groq.com/openai/v1",
                litellm_prefix="groq",
                drop_params=True,
                supports_parallel_tool_calls=True,
            )
            if provider_id == "groq"
            else None
        ),
    )
    monkeypatch.setattr(
        "agents.extensions.models.litellm_model.LitellmModel",
        FakeLitellmModel,
    )

    with patch.dict(
        os.environ,
        {
            "GROQ_API_KEY": "groq-key",
            "GROQ_BASE_URL": "https://api.groq.com/openai/v1",
        },
    ):
        get_model_for_agent("stub-groq-model")

    assert captured["model"] == "groq/stub-groq-model"
    assert captured["base_url"] == "https://api.groq.com/openai/v1"
    assert captured["api_key"] == "groq-key"
