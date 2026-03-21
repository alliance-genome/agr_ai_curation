"""Unit tests for the curation prep agent builder."""

from types import SimpleNamespace

import pytest

from src.lib.config.agent_loader import load_agent_definitions, reset_cache as reset_agent_cache
from src.lib.openai_agents.agents import curation_prep_agent as module
from src.schemas.curation_prep import CurationPrepAgentOutput


@pytest.fixture(autouse=True)
def _reset_agent_loader_cache():
    reset_agent_cache()
    yield
    reset_agent_cache()


def test_curation_prep_agent_bundle_is_discoverable():
    """The repo-local config bundle should load through the shared agent loader."""

    agent_defs = load_agent_definitions(force_reload=True)

    assert "curation_prep" in agent_defs
    agent_def = agent_defs["curation_prep"]
    assert agent_def.folder_name == "curation_prep"
    assert agent_def.output_schema == "CurationPrepAgentOutput"
    assert agent_def.supervisor_routing.enabled is False


def test_create_curation_prep_agent_builds_structured_output_agent(monkeypatch):
    """The runtime builder should enable usage tracking and structured output."""

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        module,
        "get_curation_prep_agent_definition",
        lambda: SimpleNamespace(
            name="Curation Prep Agent",
            model_config=SimpleNamespace(
                model="gpt-5-mini",
                temperature=0.1,
                reasoning="medium",
            ),
        ),
    )
    monkeypatch.setattr(module, "_load_curation_prep_prompt", lambda: "Base prompt.")
    monkeypatch.setattr(module, "resolve_model_provider", lambda _model: "openai")
    monkeypatch.setattr(
        module,
        "get_model_for_agent",
        lambda model, provider_override=None: f"model::{model}::{provider_override}",
    )

    def _fake_build_model_settings(**kwargs):
        captured["model_settings_kwargs"] = kwargs
        return "settings"

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

    monkeypatch.setattr(module, "build_model_settings", _fake_build_model_settings)
    monkeypatch.setattr(module, "Agent", _FakeAgent)

    module.create_curation_prep_agent()

    assert captured["model_settings_kwargs"] == {
        "model": "gpt-5-mini",
        "temperature": 0.1,
        "reasoning_effort": "medium",
        "parallel_tool_calls": False,
        "verbosity": "low",
        "include_usage": True,
        "provider_override": "openai",
    }

    agent_kwargs = captured["agent_kwargs"]
    assert agent_kwargs["name"] == "Curation Prep Agent"
    assert agent_kwargs["model"] == "model::gpt-5-mini::openai"
    assert agent_kwargs["model_settings"] == "settings"
    assert agent_kwargs["tools"] == []
    assert agent_kwargs["output_type"] is CurationPrepAgentOutput
    assert "ALWAYS PRODUCE STRUCTURED OUTPUT" in agent_kwargs["instructions"]


def test_load_curation_prep_prompt_falls_back_to_prompt_yaml(monkeypatch, tmp_path):
    """Prompt loading should fall back to the bundle prompt when cache is unavailable."""

    prompt_path = tmp_path / "prompt.yaml"
    prompt_path.write_text(
        "agent_id: curation_prep\ncontent: |\n  Prompt from YAML fallback.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        module,
        "get_prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache not initialized")),
    )
    monkeypatch.setattr(
        module,
        "resolve_agent_config_sources",
        lambda: (
            SimpleNamespace(folder_name="gene", prompt_yaml=None),
            SimpleNamespace(folder_name="curation_prep", prompt_yaml=prompt_path),
        ),
    )

    prompt = module._load_curation_prep_prompt()

    assert prompt == "Prompt from YAML fallback."
