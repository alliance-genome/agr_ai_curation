"""Dedicated Chat model selection does not alter extraction defaults."""
import pytest
from src.lib.agent_studio import openai_runtime
from src.lib.config.models_loader import get_default_model


def test_chat_default_is_astra_medium_without_changing_extraction(monkeypatch):
    monkeypatch.delenv("AGENT_STUDIO_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("AGENT_STUDIO_REASONING_EFFORT", raising=False)
    assert openai_runtime.resolve_agent_studio_model() == ("gpt-6-astra", "medium")
    assert get_default_model().model_id == "gpt-5.6-sol"


def test_chat_model_and_reasoning_can_be_configured(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_OPENAI_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("AGENT_STUDIO_REASONING_EFFORT", "high")
    assert openai_runtime.resolve_agent_studio_model() == ("gpt-5.6-sol", "high")


@pytest.mark.parametrize("model,effort,message", [
    ("missing-model", "medium", "registered Chat model"),
    ("deepseek/deepseek-v4-pro-0813", "medium", "Chat model to use OpenAI"),
    ("gpt-6-astra", "none", "not supported"),
    ("gpt-6-astra", "minimal", "not supported"),
])
def test_chat_model_selection_fails_closed(monkeypatch, model, effort, message):
    monkeypatch.setenv("AGENT_STUDIO_OPENAI_MODEL", model)
    monkeypatch.setenv("AGENT_STUDIO_REASONING_EFFORT", effort)
    with pytest.raises(ValueError, match=message):
        openai_runtime.resolve_agent_studio_model()
