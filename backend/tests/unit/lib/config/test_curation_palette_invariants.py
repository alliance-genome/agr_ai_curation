"""Guardrails for curation terminal visibility in the Flow Builder palette."""

from __future__ import annotations

from src.lib.config.agent_loader import load_agent_definitions, reset_cache


def _load_agents():
    reset_cache()
    try:
        return load_agent_definitions()
    finally:
        reset_cache()


def test_curation_prep_is_hidden_from_flow_builder_palette():
    agents = _load_agents()

    curation_prep = agents["curation_prep"]

    assert curation_prep.frontend.show_in_palette is False


def test_exactly_one_auto_push_curation_terminal_is_palette_visible():
    agents = _load_agents()

    # Curation Handoff is the shipped auto-push curation terminal: category and
    # subcategory are the config-owned contract that distinguish it from the
    # internal curation_prep engine without relying only on a single agent ID.
    auto_push_curation_terminals = [
        agent
        for agent in agents.values()
        if agent.category == "Curation" and agent.subcategory == "Handoff"
    ]
    visible_terminal_ids = sorted(
        agent.agent_id
        for agent in auto_push_curation_terminals
        if agent.frontend.show_in_palette
    )

    assert visible_terminal_ids == ["curation_handoff"]
