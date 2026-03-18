"""Tests for prompt-key resolution and group-rule normalization."""

import uuid

import pytest

from src.models.sql.prompts import PromptTemplate
from src.lib.agent_studio import catalog_service
from src.lib.prompts.context import get_prompt_override


def test_get_prompt_key_for_agent_resolves_registry_alias():
    """Config registry alias (agent_id) should resolve to canonical folder key."""
    assert catalog_service.get_prompt_key_for_agent("gene_validation") == "gene"


def test_get_prompt_key_for_agent_accepts_canonical_key():
    """Canonical prompt key (folder name) should resolve to itself."""
    assert catalog_service.get_prompt_key_for_agent("gene") == "gene"


def test_get_prompt_key_for_agent_rejects_unknown_key():
    """Unknown key should raise clear ValueError."""
    with pytest.raises(ValueError, match="Unknown agent_id"):
        catalog_service.get_prompt_key_for_agent("unknown_agent_key")


def test_build_catalog_accepts_group_rules_and_legacy_mod_rules(monkeypatch):
    """Catalog build should read both group_rules and legacy mod_rules prompt types."""
    base_prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="system",
        group_id=None,
        content="base prompt",
        version=1,
        is_active=True,
    )
    wb_prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="group_rules",
        group_id="WB",
        content="wb rules",
        version=1,
        is_active=True,
    )
    fb_prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="mod_rules",
        group_id="FB",
        content="fb rules",
        version=1,
        is_active=True,
    )

    monkeypatch.setattr(catalog_service, "AGENT_REGISTRY", {
        "gene": {
            "name": "Gene Specialist",
            "description": "Curate genes",
            "category": "Validation",
            "tools": [],
            "factory": lambda: None,
            "subcategory": "Data Validation",
        }
    })
    monkeypatch.setattr(catalog_service, "expand_tools_for_agent", lambda _a, _t: [])

    from src.lib.prompts import cache as prompt_cache
    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: True)
    monkeypatch.setattr(prompt_cache, "get_all_active_prompts", lambda: {
        "gene:system:base": base_prompt,
        "gene:group_rules:WB": wb_prompt,
        "gene:mod_rules:FB": fb_prompt,
    })

    catalog = catalog_service._build_catalog()
    assert catalog.total_agents == 1
    assert sorted(catalog.available_groups) == ["FB", "WB"]
    assert len(catalog.categories) == 1
    assert len(catalog.categories[0].agents) == 1

    agent = catalog.categories[0].agents[0]
    assert agent.has_group_rules is True
    assert sorted(agent.group_rules.keys()) == ["FB", "WB"]
    assert agent.group_rules["WB"].content == "wb rules"
    assert agent.group_rules["FB"].content == "fb rules"


def test_build_catalog_core_only_registry_hides_missing_alliance_agents(monkeypatch):
    """Catalog build should stay valid when only task_input and supervisor exist."""
    supervisor_prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="supervisor",
        prompt_type="system",
        group_id=None,
        content="supervisor prompt",
        version=1,
        is_active=True,
    )

    monkeypatch.setattr(
        catalog_service,
        "AGENT_REGISTRY",
        {
            "task_input": {
                "name": "Initial Instructions",
                "description": "Start the flow",
                "category": "Input",
                "tools": [],
                "frontend": {"show_in_palette": False},
            },
            "supervisor": {
                "name": "Supervisor",
                "description": "Route curator requests",
                "category": "Routing",
                "tools": [],
                "frontend": {"show_in_palette": False},
            },
        },
    )
    monkeypatch.setattr(catalog_service, "expand_tools_for_agent", lambda _a, _t: [])

    from src.lib.prompts import cache as prompt_cache
    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: True)
    monkeypatch.setattr(
        prompt_cache,
        "get_all_active_prompts",
        lambda: {"supervisor:system:base": supervisor_prompt},
    )

    catalog = catalog_service._build_catalog()
    agent_ids = {
        agent.agent_id
        for category in catalog.categories
        for agent in category.agents
    }

    assert agent_ids == {"task_input", "supervisor"}
    assert catalog.total_agents == 2
    assert "gene" not in agent_ids
    assert "pdf_extraction" not in agent_ids


def test_get_agent_by_id_requires_unified_agents_table(monkeypatch):
    """Runtime creation should fail fast when no unified agent record exists."""
    monkeypatch.setattr(
        catalog_service,
        "_get_db_agent_row",
        lambda _agent_id, _kwargs: None,
    )

    with pytest.raises(ValueError, match="unified agents table"):
        catalog_service.get_agent_by_id("gene")
    assert get_prompt_override() is None


def test_get_agent_by_id_builds_from_unified_agent_record(monkeypatch):
    """Runtime creation should use DB-backed unified agent rows only."""
    fake_row = type("FakeAgentRow", (), {"agent_key": "gene"})()
    built_agent = object()

    monkeypatch.setattr(
        catalog_service,
        "_get_db_agent_row",
        lambda _agent_id, _kwargs: fake_row,
    )
    monkeypatch.setattr(
        catalog_service,
        "_create_db_agent",
        lambda _row, **_kwargs: built_agent,
    )

    result = catalog_service.get_agent_by_id("gene", active_groups=["WB"])
    assert result is built_agent
    assert get_prompt_override() is None
