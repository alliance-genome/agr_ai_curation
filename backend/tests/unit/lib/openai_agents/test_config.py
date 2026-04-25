"""Tests for strict model/provider config behavior."""

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.lib.openai_agents.config import (
    AgentConfig,
    build_model_settings,
    get_agent_config,
    get_api_key,
    get_base_url,
    get_groq_tool_call_max_retries,
    get_groq_tool_call_retry_delay_seconds,
    get_model_for_agent,
    is_retryable_groq_tool_call_error,
    resolve_model_provider,
    supports_reasoning,
    supports_temperature,
)


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
        {"gene_extractor": {"config_defaults": {"model": "gpt-4o"}}},
        raising=False,
    )
    monkeypatch.setattr("src.lib.openai_agents.config.get_default_model", lambda: "gpt-5.5")

    with patch.dict(os.environ, {}, clear=True):
        config = get_agent_config("gene_extractor")

    assert config.model == "gpt-4o"


def test_get_agent_config_env_override_beats_registry_model(monkeypatch):
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.AGENT_REGISTRY",
        {"gene_extractor": {"config_defaults": {"model": "gpt-4o"}}},
        raising=False,
    )
    monkeypatch.setattr("src.lib.openai_agents.config.get_default_model", lambda: "gpt-5.5")

    with patch.dict(os.environ, {"AGENT_GENE_EXTRACTOR_MODEL": "gpt-5.4-nano"}, clear=True):
        config = get_agent_config("gene_extractor")

    assert config.model == "gpt-5.4-nano"


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

    assert resolve_model_provider("openai/gpt-oss-120b") == "groq"


def test_resolve_model_provider_requires_known_model(monkeypatch):
    monkeypatch.setattr("src.lib.config.models_loader.get_model", lambda _model_id: None)
    with pytest.raises(ValueError, match="Unknown model_id"):
        resolve_model_provider("unknown-model")


def test_resolve_model_provider_rejects_unknown_override(monkeypatch):
    monkeypatch.setattr("src.lib.config.providers_loader.get_provider", lambda _provider_id: None)
    with pytest.raises(ValueError, match="Unknown provider_id"):
        resolve_model_provider("gpt-5.4-nano", provider_override="not-real")


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
        model="openai/gpt-oss-120b",
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
        model="openai/gpt-oss-120b",
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
        get_model_for_agent("openai/gpt-oss-120b")

    assert captured["model"] == "groq/openai/gpt-oss-120b"
    assert captured["base_url"] == "https://api.groq.com/openai/v1"
    assert captured["api_key"] == "groq-key"
