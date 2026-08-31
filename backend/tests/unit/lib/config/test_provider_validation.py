"""Tests for config.provider_validation diagnostics and contract checks."""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_startup_report():
    from src.lib.config.provider_validation import reset_startup_provider_validation_report

    reset_startup_provider_validation_report()
    yield
    reset_startup_provider_validation_report()


def test_build_provider_runtime_report_detects_unknown_model_provider(monkeypatch):
    import src.lib.config.provider_validation as module

    monkeypatch.setattr(module, "load_providers", lambda: {"openai": object()})
    monkeypatch.setattr(module, "list_providers", lambda: [
        SimpleNamespace(
            provider_id="openai",
            driver="openai_native",
            api_mode="responses",
            api_key_env="OPENAI_API_KEY",
            base_url_env="OPENAI_BASE_URL",
            default_base_url="",
            default_for_runner=True,
            supports_parallel_tool_calls=True,
        )
    ])
    monkeypatch.setattr(module, "load_models", lambda: {"bad-model": object()})
    monkeypatch.setattr(module, "list_models", lambda: [
        SimpleNamespace(model_id="bad-model", provider="not-real", curator_visible=True)
    ])

    report = module.build_provider_runtime_report(strict_mode=True)
    assert report["status"] == "unhealthy"
    assert any("unknown provider 'not-real'" in msg for msg in report["errors"])


def test_build_provider_runtime_report_flags_missing_api_key_in_strict_mode(monkeypatch):
    import src.lib.config.provider_validation as module

    monkeypatch.setattr(module, "load_providers", lambda: {"openai": object()})
    monkeypatch.setattr(module, "list_providers", lambda: [
        SimpleNamespace(
            provider_id="openai",
            driver="openai_native",
            api_mode="responses",
            api_key_env="OPENAI_API_KEY",
            base_url_env="OPENAI_BASE_URL",
            default_base_url="",
            default_for_runner=True,
            supports_parallel_tool_calls=True,
        )
    ])
    monkeypatch.setattr(module, "load_models", lambda: {"gpt-5.4-mini": object()})
    monkeypatch.setattr(module, "list_models", lambda: [
        SimpleNamespace(model_id="gpt-5.4-mini", provider="openai", curator_visible=True)
    ])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    report = module.build_provider_runtime_report(strict_mode=True)
    assert report["status"] == "unhealthy"
    assert any("OPENAI_API_KEY" in msg for msg in report["errors"])


def test_build_provider_runtime_report_downgrades_missing_key_in_non_strict_mode(monkeypatch):
    import src.lib.config.provider_validation as module

    monkeypatch.setattr(module, "load_providers", lambda: {"openai": object()})
    monkeypatch.setattr(module, "list_providers", lambda: [
        SimpleNamespace(
            provider_id="openai",
            driver="openai_native",
            api_mode="responses",
            api_key_env="OPENAI_API_KEY",
            base_url_env="OPENAI_BASE_URL",
            default_base_url="",
            default_for_runner=True,
            supports_parallel_tool_calls=True,
        )
    ])
    monkeypatch.setattr(module, "load_models", lambda: {"gpt-5.4-mini": object()})
    monkeypatch.setattr(module, "list_models", lambda: [
        SimpleNamespace(model_id="gpt-5.4-mini", provider="openai", curator_visible=True)
    ])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    report = module.build_provider_runtime_report(strict_mode=False)
    assert report["status"] == "degraded"
    assert report["errors"] == []
    assert any("OPENAI_API_KEY" in msg for msg in report["warnings"])


def test_build_provider_runtime_report_rejects_missing_compatible_base_url(monkeypatch):
    import src.lib.config.provider_validation as module

    monkeypatch.setattr(module, "load_providers", lambda: {"compatible": object()})
    monkeypatch.setattr(module, "list_providers", lambda: [
        SimpleNamespace(
            provider_id="compatible",
            driver="openai_compatible",
            api_mode="chat_completions",
            api_key_env="COMPATIBLE_API_KEY",
            base_url_env="COMPATIBLE_BASE_URL",
            default_base_url=None,
            default_for_runner=True,
            supports_parallel_tool_calls=True,
        )
    ])
    monkeypatch.setattr(module, "load_models", lambda: {"compatible-model": object()})
    monkeypatch.setattr(module, "list_models", lambda: [
        SimpleNamespace(
            model_id="compatible-model",
            provider="compatible",
            curator_visible=True,
        )
    ])
    monkeypatch.setenv("COMPATIBLE_API_KEY", "test-key")
    monkeypatch.delenv("COMPATIBLE_BASE_URL", raising=False)

    report = module.build_provider_runtime_report(strict_mode=True)

    assert report["status"] == "unhealthy"
    assert report["providers"][0]["readiness"] == "missing_base_url"
    assert any("base URL is not configured" in msg for msg in report["errors"])


def test_optional_provider_missing_key_degrades_without_startup_error(monkeypatch):
    import src.lib.config.provider_validation as module

    providers = [
        SimpleNamespace(
            provider_id="openai",
            driver="openai_native",
            api_mode="responses",
            api_key_env="OPENAI_API_KEY",
            base_url_env=None,
            default_base_url=None,
            default_for_runner=True,
            optional_for_runtime=False,
            supports_parallel_tool_calls=True,
        ),
        SimpleNamespace(
            provider_id="openrouter",
            driver="openai_compatible",
            api_mode="chat_completions",
            api_key_env="OPENROUTER_API_KEY",
            base_url_env=None,
            default_base_url="https://openrouter.ai/api/v1",
            default_for_runner=False,
            optional_for_runtime=True,
            supports_parallel_tool_calls=True,
        ),
    ]
    models = [
        SimpleNamespace(model_id="gpt-test", provider="openai", curator_visible=True),
        SimpleNamespace(
            model_id="deepseek/deepseek-v4-pro-0813",
            provider="openrouter",
            curator_visible=True,
        ),
    ]
    monkeypatch.setattr(module, "load_providers", lambda: None)
    monkeypatch.setattr(module, "list_providers", lambda: providers)
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: models)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = module.build_provider_runtime_report(strict_mode=True)

    assert report["status"] == "degraded"
    assert report["errors"] == []
    item = next(p for p in report["providers"] if p["provider_id"] == "openrouter")
    assert item["readiness"] == "missing_api_key"
    assert item["route_configured"] is True
    assert item["route_available"] is False
    assert item["required_for_runtime"] is False

    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-openrouter-key")
    ready_report = module.build_provider_runtime_report(strict_mode=True)
    ready_item = next(
        p for p in ready_report["providers"] if p["provider_id"] == "openrouter"
    )
    assert ready_report["status"] == "healthy"
    assert ready_item["readiness"] == "ready"
    assert ready_item["route_available"] is True


def test_validate_and_cache_provider_runtime_contracts_caches_startup_report(monkeypatch):
    import src.lib.config.provider_validation as module

    monkeypatch.setattr(
        module,
        "validate_provider_runtime_contracts",
        lambda strict_mode=None: (
            True,
            {
                "status": "healthy",
                "strict_mode": True,
                "validated_at": "2026-02-23T00:00:00+00:00",
                "errors": [],
                "warnings": [],
                "providers": [],
                "models": [],
                "summary": {},
            },
        ),
    )

    report = module.validate_and_cache_provider_runtime_contracts(strict_mode=True)
    cached = module.get_startup_provider_validation_report()
    assert report["status"] == "healthy"
    assert cached is not None
    assert cached["status"] == "healthy"


def test_validate_and_cache_provider_runtime_contracts_raises_on_error(monkeypatch):
    import src.lib.config.provider_validation as module

    monkeypatch.setattr(
        module,
        "validate_provider_runtime_contracts",
        lambda strict_mode=None: (
            False,
            {
                "status": "unhealthy",
                "strict_mode": True,
                "validated_at": "2026-02-23T00:00:00+00:00",
                "errors": ["Provider 'openai' missing OPENAI_API_KEY"],
                "warnings": [],
                "providers": [],
                "models": [],
                "summary": {},
            },
        ),
    )

    with pytest.raises(RuntimeError, match="LLM provider validation failed"):
        module.validate_and_cache_provider_runtime_contracts(strict_mode=True)
