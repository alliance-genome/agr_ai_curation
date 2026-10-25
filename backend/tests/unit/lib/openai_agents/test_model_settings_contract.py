"""Retry-bearing contracts must be stable without losing execution policy."""
import json
from copy import copy
from dataclasses import asdict, replace

import pytest
from agents import Agent, ModelSettings
from agents.retry import ModelRetrySettings, ModelRetryBackoffSettings
from pydantic_core import to_jsonable_python

from src.lib.openai_agents.config import build_default_model_retry
from src.lib.openai_agents.model_settings_contract import (
    ModelSettingsContractError, model_settings_contract, register_retry_policy,
)
from src.lib.validation_history.store import HistoryConflict
from src.lib.validation_review.extractor_phase import contract


def settings():
    return ModelSettings(temperature=0.2, retry=build_default_model_retry())


def test_real_default_policy_is_stable_and_retains_live_settings(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    first = settings()
    recorded = model_settings_contract(first)
    assert recorded == model_settings_contract(settings())
    assert recorded == model_settings_contract(copy(first))
    assert json.loads(json.dumps(recorded)) == recorded
    assert recorded["temperature"] == 0.2
    assert recorded["retry"]["max_retries"] == 3
    assert recorded["retry"]["backoff"] == first.retry.backoff.to_json_dict()
    policy = recorded["retry"]["policy"]
    assert policy["id"] == "agr.default_model_retry"
    assert policy["revision"] == 1
    assert policy["sdk_version"]
    assert policy["any"][-1] == {"http_status": [408, 409, 429, 500, 502, 503, 504]}
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "7")
    assert recorded == model_settings_contract(first)
    assert recorded != model_settings_contract(settings())
    # Returning JSON must not expose mutable registry state.
    policy["any"].clear()
    assert model_settings_contract(first)["retry"]["policy"]["any"]


def test_no_retry_preserves_previous_contract_and_default_policy_is_distinct():
    disabled = ModelSettings(temperature=0.3)
    assert model_settings_contract(disabled) == to_jsonable_python(asdict(disabled))
    default = ModelSettings(retry=ModelRetrySettings(max_retries=2))
    assert model_settings_contract(default)["retry"]["policy"]["id"] == "openai-agents.default"
    assert model_settings_contract(default) != model_settings_contract(disabled)
    assert model_settings_contract(None) is None


@pytest.mark.parametrize("field,value", [
    ("initial_delay", 0.25), ("max_delay", 19), ("multiplier", 3), ("jitter", False),
])
def test_resolve_keeps_policy_identity_and_tracks_live_backoff(field, value, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    original = settings()
    merged = original.resolve(ModelSettings(retry=ModelRetrySettings(
        max_retries=6, backoff=ModelRetryBackoffSettings(**{field: value}),
    )))
    before, after = model_settings_contract(original), model_settings_contract(merged)
    assert after["retry"]["policy"] == before["retry"]["policy"]
    assert after["retry"]["max_retries"] == 6
    assert after["retry"]["backoff"][field] == value
    assert after != before


def test_policy_replacement_cannot_reuse_identity(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    original = settings()
    replacement = replace(original, retry=replace(original.retry, policy=lambda ctx: False))
    with pytest.raises(ModelSettingsContractError, match="no registered"):
        model_settings_contract(replacement)
    with pytest.raises(ModelSettingsContractError, match="cannot be changed"):
        register_retry_policy(original.retry.policy, {"id": "different"})


def test_extractor_contract_pins_policy_and_rejects_unknown_callback(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    agent = Agent(name="Extractor", instructions="Extract", model="test", model_settings=settings())
    pinned = contract(agent, max_turns=10)
    agent.model_settings = settings()
    assert contract(agent, max_turns=10) == pinned
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "4")
    agent.model_settings = settings()
    assert contract(agent, max_turns=10) != pinned
    agent.model_settings.retry.policy = lambda ctx: False
    with pytest.raises(HistoryConflict, match="unidentified retry policy"):
        contract(agent, max_turns=10)
