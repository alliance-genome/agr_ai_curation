"""Disabled deployment policy wins over credentials and stale model instances."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.lib.openai_agents import config


@pytest.mark.parametrize("key", ["", "dummy-key-not-a-secret"])
def test_openrouter_is_disabled_before_client_construction(monkeypatch, key):
    monkeypatch.delenv("LLM_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    with pytest.raises(ValueError, match="disabled by policy"):
        config.get_model_for_agent("deepseek/deepseek-v4-pro-0813")


def test_openai_stays_enabled(monkeypatch):
    monkeypatch.delenv("LLM_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-not-a-secret")
    assert isinstance(config.get_model_for_agent("gpt-6-sol"), str)


def test_policy_filters_catalog_and_rejects_save(monkeypatch):
    from src.lib.config.models_loader import get_model, is_model_selectable
    from src.lib.agent_studio.custom_agent_service import _validate_model_id

    monkeypatch.delenv("LLM_DISABLED_PROVIDERS", raising=False)
    model = get_model("deepseek/deepseek-v4-pro-0813")
    assert model is not None
    assert not is_model_selectable(model)
    with pytest.raises(config.ProviderDisabledError):
        _validate_model_id(model.model_id)
    monkeypatch.setenv("LLM_DISABLED_PROVIDERS", "")
    assert is_model_selectable(model)
    assert _validate_model_id(model.model_id) == model.model_id


@pytest.mark.asyncio
async def test_cached_adapter_cannot_send_when_disabled(monkeypatch):
    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
    from src.lib.openai_agents.provider_model import (
        ProviderConfiguredChatCompletionsModel,
    )

    monkeypatch.delenv("LLM_DISABLED_PROVIDERS", raising=False)
    fetch = AsyncMock()
    monkeypatch.setattr(OpenAIChatCompletionsModel, "_fetch_response", fetch)
    model = ProviderConfiguredChatCompletionsModel(
        model="deepseek/deepseek-v4-pro-0813",
        openai_client=SimpleNamespace(),
        provider_id="openrouter",
        request_extra_body={},
        request_headers={},
        forbidden_request_fields=(),
        omit_usage_request=False,
        telemetry_adapter=None,
        disable_model_retries=False,
    )
    with pytest.raises(config.ProviderDisabledError):
        await model._fetch_response()
    fetch.assert_not_awaited()


def test_disabled_diagnostics_do_not_request_credentials(monkeypatch):
    from src.lib.config.provider_validation import build_provider_runtime_report

    monkeypatch.delenv("LLM_DISABLED_PROVIDERS", raising=False)
    report = build_provider_runtime_report(strict_mode=False)
    provider = next(
        item for item in report["providers"] if item["provider_id"] == "openrouter"
    )
    assert provider["readiness"] == "disabled_by_policy"
    assert not provider["route_available"]
    assert not provider["mapped_curator_visible_model_ids"]
    assert not any("OPENROUTER_API_KEY" in message for message in report["warnings"])
