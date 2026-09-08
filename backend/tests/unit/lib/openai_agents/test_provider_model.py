"""Tests for config-defined request policy and provider telemetry capture."""

from types import SimpleNamespace

import pytest
import httpx
from openai import AsyncOpenAI
from agents import function_tool
from agents.models.interface import ModelTracing
from agents import ModelSettings
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.retry import ModelRetrySettings

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
        disable_model_retries=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled, requested, expected",
    [(True, True, None), (True, False, False), (True, None, None), (False, True, True)],
)
async def test_parallel_request_policy_preserves_explicit_restrictions(
    enabled, requested, expected
):
    import json

    captured = {}

    async def respond(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "synthetic-completion",
                "object": "chat.completion",
                "created": 1,
                "model": "google/gemini-3.7-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    @function_tool
    def synthetic_ping() -> str:
        """Synthetic request-shape tool; never invoked by this test."""
        raise AssertionError("No tool execution expected")

    async with AsyncOpenAI(
        api_key="synthetic-test-key",
        base_url="https://provider.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        max_retries=0,
    ) as client:
        model = ProviderConfiguredChatCompletionsModel(
            model="google/gemini-3.7-flash",
            openai_client=client,
            provider_id="synthetic",
            request_extra_body={
                "provider": {"allow_fallbacks": False, "require_parameters": True}
            },
            request_headers={},
            forbidden_request_fields=(),
            omit_usage_request=True,
            omit_parallel_tool_calls_when_enabled=enabled,
            telemetry_adapter=None,
            disable_model_retries=True,
        )
        settings = ModelSettings(parallel_tool_calls=requested, tool_choice="auto")
        await model._fetch_response(
            None,
            "Synthetic input",
            settings,
            [synthetic_ping],
            None,
            [],
            None,
            ModelTracing.DISABLED,
            False,
        )
    assert settings.parallel_tool_calls is requested
    if expected is None:
        assert "parallel_tool_calls" not in captured
    else:
        assert captured["parallel_tool_calls"] is expected
    assert captured["tools"][0]["function"]["name"] == "synthetic_ping"
    assert captured["provider"] == {
        "allow_fallbacks": False,
        "require_parameters": True,
    }


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
        captured["retry"] = settings.retry
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
        retry=ModelRetrySettings(max_retries=3),
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
    assert captured["retry"] is None


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


@pytest.mark.asyncio
async def test_provider_failure_is_recorded_with_route_sequence_and_bounded_detail(
    monkeypatch,
):
    async def fake_fetch(self, *args, **kwargs):
        raise RuntimeError("bearer top-secret " + "x" * 100)

    monkeypatch.setattr(OpenAIChatCompletionsModel, "_fetch_response", fake_fetch)
    model = _model(telemetry_adapter="openrouter")
    model._benchmark_route_slot = "agent:extractor"
    model._benchmark_requested_provider = "openrouter"
    model._benchmark_requested_model = "extractor-model"
    model._benchmark_reasoning_effort = "high"

    with capture_provider_usage(max_records=2, max_failure_detail_chars=40) as records:
        with pytest.raises(RuntimeError, match="top-secret"):
            await model._fetch_response(
                None, [], ModelSettings(), [], None, [], None, None, False
            )

    assert len(records) == 1
    assert records[0].route_slot == "agent:extractor"
    assert records[0].sequence == 1
    assert records[0].status == "failed"
    assert records[0].reasoning_effort == "high"
    assert records[0].failure_detail is not None
    assert "top-secret" not in records[0].failure_detail
    assert len(records[0].failure_detail) <= 40


@pytest.mark.asyncio
async def test_invocation_limit_fails_before_next_provider_call(monkeypatch):
    calls = 0

    async def fake_fetch(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return {}

    monkeypatch.setattr(OpenAIChatCompletionsModel, "_fetch_response", fake_fetch)
    model = _model(telemetry_adapter="openrouter")
    with capture_provider_usage(max_records=1, max_failure_detail_chars=20):
        await model._fetch_response(
            None, [], ModelSettings(), [], None, [], None, None, False
        )
        with pytest.raises(RuntimeError, match="exceeded 1"):
            await model._fetch_response(
                None, [], ModelSettings(), [], None, [], None, None, False
            )

    assert calls == 1
