"""Unit tests for first-class agent service helpers."""

from types import SimpleNamespace
from uuid import uuid4

from src.lib.agent_studio.agent_service import (
    agent_to_execution_spec,
    is_agent_editable_by_user,
    is_agent_visible_to_user,
)


def test_is_agent_visible_to_user_allows_system_agents():
    agent = SimpleNamespace(visibility="system", user_id=None, project_id=None)
    assert is_agent_visible_to_user(agent, user_id=123, project_ids=None)


def test_is_agent_visible_to_user_restricts_private_agents_to_owner():
    owner_agent = SimpleNamespace(visibility="private", user_id=7, project_id=None)
    non_owner_agent = SimpleNamespace(visibility="private", user_id=9, project_id=None)

    assert is_agent_visible_to_user(owner_agent, user_id=7)
    assert not is_agent_visible_to_user(non_owner_agent, user_id=7)


def test_is_agent_visible_to_user_requires_project_membership():
    project_id = uuid4()
    agent = SimpleNamespace(visibility="project", user_id=8, project_id=project_id)

    assert not is_agent_visible_to_user(agent, user_id=8, project_ids=None)
    assert is_agent_visible_to_user(agent, user_id=8, project_ids={project_id})


def test_is_agent_editable_by_user_owner_only_for_non_system():
    system_agent = SimpleNamespace(visibility="system", user_id=None)
    custom_agent = SimpleNamespace(visibility="private", user_id=3)

    assert not is_agent_editable_by_user(system_agent, user_id=3)
    assert is_agent_editable_by_user(custom_agent, user_id=3)
    assert not is_agent_editable_by_user(custom_agent, user_id=4)


def test_agent_to_execution_spec_maps_and_normalizes_json_fields():
    agent = SimpleNamespace(
        agent_key="gene_validation",
        name="Gene Validator",
        instructions="You are a specialist.",
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning="medium",
        tool_ids=["agr_query"],
        output_schema_key="GeneResultEnvelope",
        group_rules_enabled=True,
        group_rules_component="gene",
        mod_prompt_overrides={"WB": "WormBase rules"},
        supervisor_enabled=True,
        show_in_palette=True,
    )

    spec = agent_to_execution_spec(agent)
    assert spec.agent_key == "gene_validation"
    assert spec.model_id == "gpt-4o"
    assert spec.tool_ids == ["agr_query"]
    assert spec.mod_prompt_overrides == {"WB": "WormBase rules"}
