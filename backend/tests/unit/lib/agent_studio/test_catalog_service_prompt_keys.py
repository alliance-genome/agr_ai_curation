"""Tests for prompt-key resolution and group-rule normalization."""

import uuid

import pytest

from src.models.sql.prompts import PromptTemplate
from src.lib.agent_studio import catalog_service
from src.lib.agent_studio.custom_agent_service import CustomAgentRuntimeInfo
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
    assert sorted(catalog.available_mods) == ["FB", "WB"]
    assert len(catalog.categories) == 1
    assert len(catalog.categories[0].agents) == 1

    agent = catalog.categories[0].agents[0]
    assert agent.has_mod_rules is True
    assert sorted(agent.mod_rules.keys()) == ["FB", "WB"]
    assert agent.mod_rules["WB"].content == "wb rules"
    assert agent.mod_rules["FB"].content == "fb rules"


def test_get_agent_by_id_resolves_custom_agent_with_mod_rules_disabled(monkeypatch):
    """Custom agent should resolve via parent factory and force active_groups=[] when disabled."""
    custom_id = "ca_11111111-2222-3333-4444-555555555555"

    def _parent_factory(active_groups=None):
        return {"active_groups": active_groups}

    monkeypatch.setattr(catalog_service, "AGENT_REGISTRY", {
        "gene": {
            "name": "Gene Specialist",
            "description": "Curate genes",
            "category": "Validation",
            "factory": _parent_factory,
            "required_params": [],
        }
    })

    from src.lib.agent_studio import custom_agent_service
    monkeypatch.setattr(custom_agent_service, "get_custom_agent_runtime_info", lambda _agent_id: CustomAgentRuntimeInfo(
        custom_agent_uuid=uuid.UUID("11111111-2222-3333-4444-555555555555"),
        custom_agent_id=custom_id,
        parent_agent_key="gene",
        display_name="Doug's Gene Agent",
        custom_prompt="Custom prompt",
        include_mod_rules=False,
        requires_document=False,
        parent_exists=True,
    ))

    result = catalog_service.get_agent_by_id(custom_id, active_groups=["WB"])
    assert result["active_groups"] == []
    assert get_prompt_override() is None
