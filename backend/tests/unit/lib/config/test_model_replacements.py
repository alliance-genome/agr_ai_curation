"""A model version move is one catalog edit (ALL-1708): replaced ids resolve to their entry.

The first tests read the shipped catalog; the others load made-up catalogs through the
real loader.
"""

from pathlib import Path

import pytest

import src.lib.config.models_loader as loader


@pytest.fixture(autouse=True)
def fresh_catalog():
    loader.reset_cache()
    yield
    loader.reset_cache()


def _load(tmp_path: Path, text: str):
    path = tmp_path / "models.yaml"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return loader.load_models(models_path=path, packages_dir=tmp_path / "no-packages", force_reload=True)


# The shipped catalog -------------------------------------------------------

def test_every_replaced_id_resolves_within_its_own_family():
    index = loader.model_index()
    for model in loader.list_models():
        for replaced in model.replaces:
            assert index[replaced] is model
            assert loader.resolve_model_id(replaced) == model.model_id
            assert loader.get_model(replaced) is model
            assert loader.is_replaced_model_id(replaced)
            assert replaced not in {entry.model_id for entry in loader.list_models()}


def test_both_shipped_catalog_files_are_identical():
    root = Path(__file__).resolve().parents[5]
    for runtime_root in (root, Path("/app"), Path("/workspace")):
        config, package = runtime_root / "config/models.yaml", runtime_root / "packages/core/config/models.yaml"
        if config.exists() and package.exists():
            assert config.read_text() == package.read_text()
            return
    pytest.skip("catalog files not mounted")


def test_an_unknown_id_is_unchanged_and_still_unknown():
    assert loader.resolve_model_id("not-a-model") == "not-a-model"
    assert loader.get_model("not-a-model") is None
    assert not loader.is_replaced_model_id("not-a-model")


# Made-up catalogs -------------------------------------------------------------

SOL_AND_ASTRA = """
models:
  - model_id: sol-2
    name: Sol 2
    replaces: [sol-1, sol-0]
    reasoning_options: [low, medium, high]
  - model_id: astra-2
    name: Astra 2
    replaces: [astra-1]
    reasoning_options: [low, medium, high, xhigh]
"""


def test_replaced_ids_resolve_to_their_own_entry(tmp_path):
    _load(tmp_path, SOL_AND_ASTRA)
    assert loader.resolve_model_id("sol-1") == loader.resolve_model_id("sol-0") == "sol-2"
    assert loader.resolve_model_id("astra-1") == "astra-2"
    assert [model.model_id for model in loader.list_models()] == ["sol-2", "astra-2"]
    assert set(loader.model_index()) == {"sol-2", "sol-1", "sol-0", "astra-2", "astra-1"}


@pytest.mark.parametrize("text,message", [
    ("""
models:
  - model_id: sol-2
    replaces: [sol-1]
  - model_id: sol-3
    replaces: [sol-1]
""", "'sol-1' is replaced by both 'sol-2' and 'sol-3'"),
    ("""
models:
  - model_id: sol-1
  - model_id: sol-2
    replaces: [sol-1]
""", "'sol-1' is both a catalog entry and replaced by 'sol-2'"),
    # A chain (sol-3 replaces sol-2, which replaces sol-1) is refused: keep it flat.
    ("""
models:
  - model_id: sol-2
    replaces: [sol-1]
  - model_id: sol-3
    replaces: [sol-2]
""", "'sol-2' is both a catalog entry and replaced by 'sol-3'"),
    ("""
models:
  - model_id: sol-2
    replaces: [sol-2]
""", "replaces itself"),
    ("""
models:
  - model_id: sol-2
    reasoning_options: [low, medium, high]
    reasoning_map: {high: medium}
""", "must map a level this model does not offer"),
    ("""
models:
  - model_id: sol-2
    reasoning_options: [low, medium, high]
    reasoning_map: {xhigh: max}
""", "must map a level this model does not offer"),
])
def test_an_inconsistent_catalog_stops_loading_with_a_named_error(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        _load(tmp_path, text)


def test_rollback_makes_the_old_entry_current_again(tmp_path):
    """Rows saved on the new id after the move resolve back; a plain revert would not."""
    _load(tmp_path, """
models:
  - model_id: sol-1
    replaces: [sol-2]
""")
    assert loader.resolve_model_id("sol-2") == "sol-1"
    _load(tmp_path, """
models:
  - model_id: sol-1
""")
    assert loader.get_model("sol-2") is None  # a plain revert leaves sol-2 rows unknown


# Reasoning ---------------------------------------------------------------------

def test_saved_level_is_kept_mapped_or_refused_by_name(tmp_path):
    from src.lib.openai_agents.config import (
        UnsupportedReasoningEffortError,
        requested_reasoning_effort,
        unsupported_reasoning_effort,
    )

    _load(tmp_path, """
models:
  - model_id: sol-2
    replaces: [sol-1]
    reasoning_options: [low, medium, high]
    reasoning_map: {xhigh: high}
  - model_id: luna-2
    replaces: [luna-1]
    reasoning_options: [low, medium]
""")
    sol, luna = loader.get_model("sol-1"), loader.get_model("luna-1")
    assert requested_reasoning_effort(sol, "medium") == "medium"
    assert requested_reasoning_effort(sol, "XHigh") == "high"
    assert requested_reasoning_effort(sol, None) is None
    assert unsupported_reasoning_effort(sol, "xhigh") is None
    assert unsupported_reasoning_effort(luna, "high") == "high"
    with pytest.raises(UnsupportedReasoningEffortError,
                       match="'high' is not offered by model 'luna-2' and the model catalog has no reviewed mapping"):
        requested_reasoning_effort(luna, "high")


def test_model_settings_send_the_resolved_model_and_mapped_level(tmp_path, monkeypatch):
    from src.lib.openai_agents import config

    _load(tmp_path, """
models:
  - model_id: sol-2
    provider: openai
    replaces: [sol-1]
    supports_temperature: false
    reasoning_options: [low, medium, high]
    reasoning_map: {xhigh: high}
  - model_id: luna-2
    provider: openai
    replaces: [luna-1]
    supports_temperature: false
    reasoning_options: [low]
""")
    identity = config.PromptCacheIdentity(agent_key="gene", static_prompt="Extract.")
    settings = config.build_model_settings("sol-1", reasoning_effort="xhigh", prompt_cache=identity)
    assert settings.reasoning.effort == "high"
    expected_key = config.build_prompt_cache_key(identity, model="sol-2")
    assert expected_key in str(settings.extra_args)
    with pytest.raises(config.UnsupportedReasoningEffortError):
        config.build_model_settings("luna-1", reasoning_effort="high", prompt_cache=identity)


# Entry points ------------------------------------------------------------------

def test_agents_built_from_a_replaced_id_request_the_current_entry(monkeypatch):
    from src.lib.openai_agents.config import get_model_for_agent

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    for model in loader.list_models():
        if model.provider == "openai":
            for replaced in model.replaces:
                assert get_model_for_agent(replaced) == model.model_id


def test_an_environment_naming_replaced_ids_passes_the_startup_gate():
    from src.lib.config.model_env_validation import model_env_errors

    env = {}
    for model in loader.list_models():
        for replaced in model.replaces:
            env["DEFAULT_AGENT_MODEL" if "DEFAULT_AGENT_MODEL" not in env else
                f"AGENT_X{len(env)}_MODEL"] = replaced
    env["AGENT_STUDIO_OPENAI_MODEL"] = next(
        replaced for model in loader.list_models() if "xhigh" in model.reasoning_options
        for replaced in model.replaces)
    env["AGENT_STUDIO_REASONING_EFFORT"] = "medium"
    assert env and model_env_errors(env) == []
    assert model_env_errors({"DEFAULT_AGENT_MODEL": "not-a-model"}) == [
        "DEFAULT_AGENT_MODEL='not-a-model' is not a model in the model catalog"]


def test_studio_chat_configured_on_a_replaced_id_runs_on_its_entry(monkeypatch):
    from src.lib.agent_studio import openai_runtime

    astra = next(model for model in loader.list_models() if model.replaces and "xhigh" in model.reasoning_options)
    monkeypatch.setenv("AGENT_STUDIO_OPENAI_MODEL", astra.replaces[0])
    monkeypatch.setenv("AGENT_STUDIO_REASONING_EFFORT", "medium")
    assert openai_runtime.resolve_agent_studio_model() == (astra.model_id, "medium")


def test_environment_model_reads_record_the_model_actually_called(monkeypatch):
    from src.lib.openai_agents.config import get_validation_review_model

    sol = next(model for model in loader.list_models() if model.default)
    monkeypatch.setenv("VALIDATION_REVIEW_MODEL", sol.replaces[0])
    assert get_validation_review_model() == sol.model_id
    monkeypatch.setenv("VALIDATION_REVIEW_MODEL", "")
    assert get_validation_review_model() == ""  # empty keeps the supervisor's own model
