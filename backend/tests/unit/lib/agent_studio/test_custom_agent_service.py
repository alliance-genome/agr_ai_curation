"""Tests for custom-agent service helpers."""

import uuid

from src.lib.agent_studio.custom_agent_service import (
    CUSTOM_AGENT_PREFIX,
    compute_prompt_hash,
    make_custom_agent_id,
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
