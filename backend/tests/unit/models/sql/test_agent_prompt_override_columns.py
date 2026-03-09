"""Tests for renamed prompt override columns on agent SQL models."""

from src.models.sql.agent import Agent
from src.models.sql.custom_agent import CustomAgentVersion


def test_agent_uses_group_prompt_overrides_as_canonical_column():
    agent = Agent(
        agent_key="test_agent",
        name="Test Agent",
        instructions="Follow the instructions.",
        model_id="gpt-5",
        group_prompt_overrides={"WB": "WormBase rules"},
    )

    assert "group_prompt_overrides" in Agent.__table__.c
    assert "mod_prompt_overrides" not in Agent.__table__.c
    assert agent.group_prompt_overrides == {"WB": "WormBase rules"}
    assert agent.mod_prompt_overrides == {"WB": "WormBase rules"}


def test_agent_still_accepts_legacy_mod_prompt_overrides_alias():
    agent = Agent(
        agent_key="legacy_agent",
        name="Legacy Agent",
        instructions="Follow the instructions.",
        model_id="gpt-5",
        mod_prompt_overrides={"MGI": "MGI rules"},
    )

    assert agent.group_prompt_overrides == {"MGI": "MGI rules"}
    assert agent.mod_prompt_overrides == {"MGI": "MGI rules"}


def test_custom_agent_version_uses_group_prompt_overrides_as_canonical_column():
    version = CustomAgentVersion(
        version=1,
        custom_prompt="Prompt",
        group_prompt_overrides={"WB": "WormBase rules"},
    )

    assert "group_prompt_overrides" in CustomAgentVersion.__table__.c
    assert "mod_prompt_overrides" not in CustomAgentVersion.__table__.c
    assert version.group_prompt_overrides == {"WB": "WormBase rules"}
    assert version.mod_prompt_overrides == {"WB": "WormBase rules"}


def test_custom_agent_version_still_accepts_legacy_mod_prompt_overrides_alias():
    version = CustomAgentVersion(
        version=2,
        custom_prompt="Prompt",
        mod_prompt_overrides={"RGD": "RGD rules"},
    )

    assert version.group_prompt_overrides == {"RGD": "RGD rules"}
    assert version.mod_prompt_overrides == {"RGD": "RGD rules"}
