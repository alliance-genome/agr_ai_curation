"""Tests for config-defined request policy and provider telemetry capture."""

from types import SimpleNamespace

import pytest
from agents import ModelSettings
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from src.lib.openai_agents.provider_model import ProviderConfiguredChatCompletionsModel
from src.lib.openai_agents.provider_usage import capture_provider_usage


def _model(*, telemetry_adapter=None):
    return ProviderConfiguredChatCompletionsModel(
        model="deepseek/deepseek-v4-pro-0813",
        openai_client=SimpleNamespace(),
        provider_id="openrouter",
        request_extra_body={
            "provider": {"allow_fallbacks": False, "require_parameters": True}
        },
        request_headers={"X-OpenRouter-Metadata": "enabled"},
        forbidden_request_fields=("models", "fallbacks"),
        omit_usage_request=True,
        telemetry_adapter=telemetry_adapter,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_provider_policy_reaches_streaming_and_non_streaming_paths(
    monkeypatch,
    stream,
):
    captured = {}

    async def fake_fetch(self, *args, **kwargs):
        settings = args[2]
        captured["extra_body"] = settings.extra_body
        captured["extra_headers"] = settings.extra_headers
        captured["include_usage"] = settings.include_usage
        return {"ok": True}

    monkeypatch.setattr(OpenAIChatCompletionsModel, "_fetch_response", fake_fetch)
    model = _model()
    settings = ModelSettings(
        extra_body={
            "provider": {
                "sort": "latency",
                "allow_fallbacks": True,
                "require_parameters": False,
            }
        },
        extra_headers={"X-Caller": "safe"},
        include_usage=True,
    )

    await model._fetch_response(None, [], settings, [], None, [], None, None, stream)

    assert captured["extra_body"] == {
        "provider": {
            "sort": "latency",
            "allow_fallbacks": False,
            "require_parameters": True,
        }
    }
    assert "models" not in captured["extra_body"]
    assert "fallbacks" not in captured["extra_body"]
    assert captured["extra_headers"] == {
        "X-Caller": "safe",
        "X-OpenRouter-Metadata": "enabled",
    }
    assert captured["include_usage"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field_name", ["models", "fallbacks"])
async def test_provider_policy_rejects_model_fallback_fields(field_name):
    model = _model()
    settings = ModelSettings(extra_body={field_name: ["other/model"]})

    with pytest.raises(ValueError, match=field_name):
        await model._fetch_response(None, [], settings, [], None, [], None, None, False)


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


@pytest.mark.asyncio
async def test_streaming_telemetry_is_captured_from_terminal_fields(monkeypatch):
    async def fake_fetch(self, *args, **kwargs):
        return (
            object(),
            _FakeStream(
                [
                    {"choices": []},
                    {
                        "usage": {
                            "prompt_tokens": 2,
                            "completion_tokens": 3,
                            "total_tokens": 5,
                            "cost": 0.004,
                        },
                        "openrouter_metadata": {
                            "attempt": 1,
                            "endpoints": {
                                "available": [
                                    {
                                        "provider": "DeepInfra",
                                        "model": "deepseek/deepseek-v4-pro-0813",
                                        "selected": True,
                                    }
                                ]
                            },
                        },
                    },
                ]
            ),
        )

    monkeypatch.setattr(OpenAIChatCompletionsModel, "_fetch_response", fake_fetch)
    model = _model(telemetry_adapter="openrouter")

    with capture_provider_usage() as records:
        _, stream = await model._fetch_response(
            None, [], ModelSettings(), [], None, [], None, None, True
        )
        async for _ in stream:
            pass

    assert len(records) == 1
    assert records[0].actual_provider == "DeepInfra"
    assert records[0].total_tokens == 5
    assert records[0].billed_cost is not None


@pytest.mark.asyncio
async def test_non_streaming_telemetry_is_captured(monkeypatch):
    async def fake_fetch(self, *args, **kwargs):
        return {
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 8,
                "total_tokens": 15,
                "cost": "0.006",
            },
            "openrouter_metadata": {
                "attempt": 1,
                "endpoints": {
                    "available": [
                        {
                            "provider": "Together",
                            "model": "deepseek/deepseek-v4-pro-0813",
                            "selected": True,
                        }
                    ]
                },
            },
        }

    monkeypatch.setattr(OpenAIChatCompletionsModel, "_fetch_response", fake_fetch)
    model = _model(telemetry_adapter="openrouter")

    with capture_provider_usage() as records:
        await model._fetch_response(
            None, [], ModelSettings(), [], None, [], None, None, False
        )

    assert len(records) == 1
    assert records[0].actual_provider == "Together"
    assert records[0].input_tokens == 7
    assert records[0].billed_cost is not None
