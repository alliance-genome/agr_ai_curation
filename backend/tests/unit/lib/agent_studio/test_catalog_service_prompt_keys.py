"""Tests for prompt-key resolution and group-rule normalization."""

import uuid

import pytest

from src.models.sql.prompts import PromptTemplate
from src.lib.agent_studio import catalog_service
from src.lib.agent_studio.models import AgentPrompts, PromptCatalog, PromptInfo
from src.lib.prompts.context import get_prompt_override


def test_get_prompt_key_for_agent_resolves_registry_alias():
    """Config registry alias (agent_id) should resolve to canonical folder key."""
    assert catalog_service.get_prompt_key_for_agent("gene_validation") == "gene"


def test_get_prompt_key_for_agent_accepts_canonical_key():
    """Canonical prompt key (folder name) should resolve to itself."""
    assert catalog_service.get_prompt_key_for_agent("gene") == "gene"


def test_get_prompt_key_for_gene_expression_accepts_flow_alias_and_package_id():
    """Gene-expression prompt lookup accepts the flow alias and package agent ID."""
    assert catalog_service.get_prompt_key_for_agent("gene_expression") == "gene_expression"
    assert (
        catalog_service.get_prompt_key_for_agent("gene_expression_extraction")
        == "gene_expression"
    )


def test_get_prompt_key_for_agent_uses_explicit_system_agent_key():
    """A bundle with an explicit public key resolves to that key."""
    assert (
        catalog_service.get_prompt_key_for_agent("ontology_term_validation")
        == "ontology_term_validation"
    )


def test_get_prompt_key_for_agent_rejects_noncanonical_folder_alias():
    """A folder name is not an alias when the bundle declares another public key."""
    with pytest.raises(ValueError, match="Unknown agent_id"):
        catalog_service.get_prompt_key_for_agent("ontology_term")


def test_prompt_catalog_get_agent_accepts_validator_agent_id_from_validation_plan():
    """Validator-agent IDs from validation plans should inspect the bundled prompt."""
    service = catalog_service.PromptCatalogService()
    gene_prompt = PromptInfo(
        agent_id="gene",
        agent_name="Gene Validation Agent",
        description="Validates genes",
        base_prompt="Gene validator prompt",
        source_file="database",
        tools=["agr_curation_query"],
    )
    service._catalog = PromptCatalog(
        categories=[AgentPrompts(category="Validation", agents=[gene_prompt])],
        total_agents=1,
        available_groups=[],
    )

    assert service.get_agent("gene_validation") == gene_prompt


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


def test_build_catalog_hides_attachment_only_validators_from_flow_palette(monkeypatch):
    """FlowBuilder visibility should reuse supervisor-callable validator policy."""
    prompts = {}
    for agent_id in ("allele_validation", "ontology_term_validation", "chat_output"):
        prompts[f"{agent_id}:system:base"] = PromptTemplate(
            id=uuid.uuid4(),
            agent_name=agent_id,
            prompt_type="system",
            group_id=None,
            content=f"{agent_id} base prompt",
            version=1,
            is_active=True,
        )

    monkeypatch.setattr(catalog_service, "AGENT_REGISTRY", {
        "allele_validation": {
            "name": "Allele Validation Agent",
            "description": "Validate allele identifiers",
            "category": "Validation",
            "tools": [],
            "subcategory": "Data Validation",
            "frontend": {"show_in_palette": True},
            "supervisor": {"enabled": False},
        },
        "ontology_term_validation": {
            "name": "Ontology Term Resolver Agent",
            "description": "Resolve ontology terms",
            "category": "Validation",
            "tools": [],
            "subcategory": "Data Validation",
            "frontend": {"show_in_palette": True},
            "supervisor": {"enabled": True},
        },
        "chat_output": {
            "name": "Chat Output Agent",
            "description": "Summarize flow outputs",
            "category": "Output",
            "tools": [],
            "subcategory": "Output",
            "frontend": {"show_in_palette": True},
            "supervisor": {"enabled": False},
        },
    })
    monkeypatch.setattr(catalog_service, "expand_tools_for_agent", lambda _a, _t: [])
    monkeypatch.setattr(catalog_service, "build_agent_prompt_layers", lambda _agent_id: None)

    from src.lib.prompts import cache as prompt_cache
    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: True)
    monkeypatch.setattr(prompt_cache, "get_all_active_prompts", lambda: prompts)

    catalog = catalog_service._build_catalog()
    by_id = {
        agent.agent_id: agent
        for category in catalog.categories
        for agent in category.agents
    }

    assert by_id["allele_validation"].show_in_palette is False
    assert by_id["ontology_term_validation"].show_in_palette is True
    assert by_id["chat_output"].show_in_palette is True


def test_build_catalog_surfaces_prompt_layer_projection_errors(monkeypatch):
    """Layer assembly failures should be visible in catalog metadata."""
    base_prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="system",
        group_id=None,
        content="base prompt",
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
    monkeypatch.setattr(
        catalog_service,
        "build_agent_prompt_layers",
        lambda _agent_id: (_ for _ in ()).throw(ValueError("broken layer assembly")),
    )

    from src.lib.prompts import cache as prompt_cache
    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: True)
    monkeypatch.setattr(prompt_cache, "get_all_active_prompts", lambda: {
        "gene:system:base": base_prompt,
    })

    catalog = catalog_service._build_catalog()
    agent = catalog.categories[0].agents[0]

    assert agent.prompt_layers == []
    assert agent.effective_prompt_hash is None
    assert agent.prompt_layer_error == "Prompt layer metadata could not be built."


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
