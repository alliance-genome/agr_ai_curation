"""Tests for custom-agent service helpers."""

import uuid
from types import SimpleNamespace

from src.lib.agent_studio.custom_agent_service import (
    CUSTOM_AGENT_PREFIX,
    compute_prompt_hash,
    get_custom_agent_mod_prompt,
    make_custom_agent_id,
    normalize_mod_prompt_overrides,
    parse_custom_agent_id,
)


def test_make_and_parse_custom_agent_id_round_trip():
    custom_uuid = uuid.uuid4()
    agent_id = make_custom_agent_id(custom_uuid)
    assert agent_id.startswith(CUSTOM_AGENT_PREFIX)
    assert parse_custom_agent_id(agent_id) == custom_uuid


def test_parse_custom_agent_id_rejects_invalid_values():
    assert parse_custom_agent_id("gene") is None
    assert parse_custom_agent_id("ca_not-a-uuid") is None
    assert parse_custom_agent_id("") is None


def test_compute_prompt_hash_is_stable():
    prompt = "You are a specialist."
    assert compute_prompt_hash(prompt) == compute_prompt_hash(prompt)
    assert compute_prompt_hash(prompt) != compute_prompt_hash(prompt + " changed")


def test_normalize_mod_prompt_overrides_cleans_keys_and_empty_values():
    normalized = normalize_mod_prompt_overrides({
        " wb ": "WormBase custom rules",
        "FB": "",
        "": "ignored",
        "mgi": "Mouse rules",
    })

    assert normalized == {
        "WB": "WormBase custom rules",
        "MGI": "Mouse rules",
    }


def test_get_custom_agent_mod_prompt_prefers_override():
    override_content = get_custom_agent_mod_prompt(
        parent_agent_key="gene",
        mod_id="WB",
        mod_prompt_overrides={"WB": "custom wb rules"},
    )
    assert override_content == "custom wb rules"


def test_get_custom_agent_mod_prompt_falls_back_to_cached_rules(monkeypatch):
    fake_cache_module = SimpleNamespace(
        get_prompt_optional=lambda agent_name, prompt_type, mod_id: (
            type("Prompt", (), {"content": "cached wb rules"})()
            if agent_name == "gene" and prompt_type == "group_rules" and mod_id == "WB"
            else None
        )
    )

    monkeypatch.setitem(__import__("sys").modules, "src.lib.prompts.cache", fake_cache_module)

    content = get_custom_agent_mod_prompt(
        parent_agent_key="gene",
        mod_id="WB",
        mod_prompt_overrides={},
    )
    assert content == "cached wb rules"
