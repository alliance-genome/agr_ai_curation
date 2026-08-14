"""Unit tests for Agent Studio Anthropic model resolution."""

from types import SimpleNamespace

import pytest


def test_list_anthropic_catalog_models_filters_and_sorts(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.setattr(
        api_module,
        "list_model_definitions",
        lambda: [
            SimpleNamespace(model_id="gpt-5.5", name="GPT-5.5", provider="openai", default=True),
            SimpleNamespace(model_id="claude-z", name="Claude Z", provider="anthropic", default=False),
            SimpleNamespace(model_id="claude-a", name="Claude A", provider="anthropic", default=True),
        ],
    )

    models = api_module._list_anthropic_catalog_models()
    assert [model.model_id for model in models] == ["claude-a", "claude-z"]


def test_resolve_prompt_explorer_model_uses_trimmed_canonical_env(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.setenv("PROMPT_EXPLORER_MODEL_ID", "  claude-opus-5  ")
    monkeypatch.setattr(
        api_module,
        "_list_anthropic_catalog_models",
        lambda: [
            SimpleNamespace(model_id="claude-opus-5", name="Claude Opus 5"),
        ],
    )

    model_id, model_name = api_module._resolve_prompt_explorer_model()
    assert model_id == "claude-opus-5"
    assert model_name == "Claude Opus 5"


def test_resolve_prompt_explorer_model_falls_back_to_catalog(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.setenv("PROMPT_EXPLORER_MODEL_ID", "   ")
    monkeypatch.setattr(
        api_module,
        "_list_anthropic_catalog_models",
        lambda: [SimpleNamespace(model_id="claude-catalog", name="Claude Catalog")],
    )

    model_id, model_name = api_module._resolve_prompt_explorer_model()
    assert model_id == "claude-catalog"
    assert model_name == "Claude Catalog"


def test_resolve_prompt_explorer_model_uses_raw_env_id_when_not_in_catalog(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.setenv("PROMPT_EXPLORER_MODEL_ID", "claude-unlisted")
    monkeypatch.setattr(
        api_module,
        "_list_anthropic_catalog_models",
        lambda: [SimpleNamespace(model_id="claude-catalog", name="Claude Catalog")],
    )

    model_id, model_name = api_module._resolve_prompt_explorer_model()
    assert model_id == "claude-unlisted"
    assert model_name == "claude-unlisted"


def test_resolve_prompt_explorer_model_raises_when_unconfigured(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.delenv("PROMPT_EXPLORER_MODEL_ID", raising=False)
    monkeypatch.setattr(api_module, "_list_anthropic_catalog_models", lambda: [])

    with pytest.raises(ValueError) as exc_info:
        api_module._resolve_prompt_explorer_model()

    assert str(exc_info.value) == (
        "No Agent Studio Anthropic model configured. Set PROMPT_EXPLORER_MODEL_ID "
        "or add an Anthropic model to config/models.yaml."
    )
