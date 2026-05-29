from types import SimpleNamespace

from src.lib.openai_agents.config import (
    build_model_settings,
    reasoning_summary_request_settings,
)


def _provider(provider_id: str, *, driver: str = "openai_native"):
    return SimpleNamespace(
        provider_id=provider_id,
        driver=driver,
        supports_parallel_tool_calls=True,
        drop_params=False,
        api_key_env="OPENAI_API_KEY",
        base_url_env=None,
        default_base_url=None,
        litellm_prefix=None,
    )


def _model(provider: str, *, supports_reasoning: bool):
    return SimpleNamespace(
        provider=provider,
        supports_reasoning=supports_reasoning,
        supports_temperature=not supports_reasoning,
    )


def test_reasoning_summary_settings_request_detailed_for_openai_reasoning_model(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: _model("openai", supports_reasoning=True),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: _provider(provider_id, driver="openai_native"),
    )

    settings = reasoning_summary_request_settings(
        model="gpt-5.4-mini",
        reasoning_effort="medium",
    )

    assert settings["availability"] == "present"
    assert settings["requested_summary"] == "detailed"
    assert settings["reasoning_effort"] == "medium"

    model_settings = build_model_settings(model="gpt-5.4-mini", reasoning_effort="medium")
    assert model_settings.reasoning.effort == "medium"
    assert model_settings.reasoning.summary == "detailed"


def test_reasoning_summary_settings_report_not_supported_for_non_reasoning_model(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: _model("openai", supports_reasoning=False),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: _provider(provider_id, driver="openai_native"),
    )

    settings = reasoning_summary_request_settings(
        model="gpt-4o",
        reasoning_effort="medium",
    )

    assert settings["availability"] == "not_supported"
    assert settings["requested_summary"] is None


def test_reasoning_summary_settings_report_not_supported_for_litellm_provider(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: _model("gemini", supports_reasoning=True),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: _provider(provider_id, driver="litellm"),
    )

    settings = reasoning_summary_request_settings(
        model="gemini-3-pro-preview",
        reasoning_effort="medium",
    )

    assert settings["availability"] == "not_supported"
    assert settings["requested_summary"] is None


def test_reasoning_summary_settings_report_not_requested_without_reasoning_effort(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: _model("openai", supports_reasoning=True),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: _provider(provider_id, driver="openai_native"),
    )

    settings = reasoning_summary_request_settings(
        model="gpt-5.4-mini",
        reasoning_effort=None,
    )

    assert settings["availability"] == "not_requested"
    assert settings["requested_summary"] is None
