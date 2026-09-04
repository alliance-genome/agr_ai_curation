"""Unit tests for canonical Agent Studio model resolution."""

from types import SimpleNamespace

import pytest

from src.lib.agent_studio import openai_runtime


def test_agent_studio_defaults_resolve_from_canonical_model_catalog():
    model = openai_runtime.get_default_catalog_model()

    assert model is not None
    assert model.model_id == "gpt-5.6-sol"
    assert model.provider == "openai"
    assert model.default_reasoning == "medium"
    assert openai_runtime.resolve_agent_studio_model() == ("gpt-5.6-sol", "medium")
    assert openai_runtime.AGENT_STUDIO_OPENAI_MODEL == model.model_id
    assert openai_runtime.AGENT_STUDIO_REASONING_EFFORT == model.default_reasoning


@pytest.mark.parametrize(
    ("model", "expected_message"),
    [
        (None, "requires a default model"),
        (
            SimpleNamespace(
                model_id="other-model",
                provider="other-provider",
                default_reasoning="medium",
            ),
            "requires the default model catalog entry to use OpenAI",
        ),
        (
            SimpleNamespace(
                model_id="gpt-test",
                provider="openai",
                default_reasoning=None,
            ),
            "requires a valid default_reasoning",
        ),
    ],
)
def test_agent_studio_model_resolution_fails_closed(monkeypatch, model, expected_message):
    monkeypatch.setattr(openai_runtime, "get_default_catalog_model", lambda: model)

    with pytest.raises(ValueError, match=expected_message):
        openai_runtime.resolve_agent_studio_model()
