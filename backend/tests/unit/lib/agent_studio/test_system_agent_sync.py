"""Tests for system-agent DB synchronization from layered config sources."""

from types import SimpleNamespace

from src.lib.config.agent_loader import (
    AgentDefinition,
    FrontendConfig,
    ModelConfig,
    SupervisorRouting,
)


class _AgentQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _DBStub:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.commit_calls = 0

    def query(self, _model):
        return _AgentQuery(self.rows)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commit_calls += 1


def _agent_definition(folder_name: str, agent_id: str) -> AgentDefinition:
    return AgentDefinition(
        folder_name=folder_name,
        agent_id=agent_id,
        name=f"{folder_name.title()} Agent",
        description=f"{folder_name} description",
        category="Validation",
        supervisor_routing=SupervisorRouting(
            enabled=True,
            description=f"Ask the {folder_name} agent",
            batchable=False,
            batching_instructions="",
            batching_entity="",
        ),
        tools=["agr_curation_query"],
        output_schema="GeneValidationEnvelope" if folder_name == "gene" else None,
        model_config=ModelConfig(model="gpt-5-mini", temperature=0.2, reasoning="medium"),
        group_rules_enabled=(folder_name == "gene"),
        frontend=FrontendConfig(icon="G", show_in_palette=True),
    )


def test_sync_system_agents_upserts_reactivates_and_deactivates(monkeypatch):
    import src.lib.agent_studio.system_agent_sync as module

    inactive_gene = SimpleNamespace(
        agent_key="gene",
        user_id=None,
        name="Old Gene Agent",
        description="old",
        instructions="old",
        model_id="old-model",
        model_temperature=0.1,
        model_reasoning="low",
        tool_ids=[],
        output_schema_key=None,
        group_rules_enabled=False,
        group_rules_component=None,
        group_prompt_overrides={"x": "y"},
        icon="O",
        category="Old",
        visibility="system",
        project_id=None,
        shared_at=None,
        template_source="old_gene",
        supervisor_enabled=False,
        supervisor_description="old",
        supervisor_batchable=False,
        supervisor_batching_entity=None,
        show_in_palette=False,
        is_active=False,
    )
    stale_agent = SimpleNamespace(
        agent_key="obsolete_agent",
        is_active=True,
        supervisor_enabled=True,
    )
    db = _DBStub([inactive_gene, stale_agent])

    monkeypatch.setattr(
        module,
        "resolve_agent_config_sources",
        lambda _agents_path=None: (
            SimpleNamespace(folder_name="disease"),
            SimpleNamespace(folder_name="gene"),
        ),
    )
    monkeypatch.setattr(
        module,
        "load_agent_definitions",
        lambda _agents_path=None, force_reload=False: {
            "disease_validation": _agent_definition("disease", "disease_validation"),
            "gene_validation": _agent_definition("gene", "gene_validation"),
        },
    )
    monkeypatch.setattr(
        module,
        "_get_active_system_prompt",
        lambda _db, *, folder_name, agent_id: f"prompt:{folder_name}:{agent_id}",
    )

    result = module.sync_system_agents(db, force_reload=True)

    assert result == {
        "inserted": 1,
        "updated": 1,
        "reactivated": 0,
        "deactivated": 1,
        "discovered": 2,
    }
    assert db.commit_calls == 1
    assert len(db.added) == 1

    inserted = db.added[0]
    assert inserted.agent_key == "disease"
    assert inserted.instructions == "prompt:disease:disease_validation"
    assert inserted.tool_ids == ["agr_curation_query"]

    # Inactive agents are NOT re-enabled by sync (they may have been disabled
    # by runtime validation due to missing tool dependencies).
    assert inactive_gene.is_active is False
    assert inactive_gene.name == "Gene Agent"
    assert inactive_gene.group_rules_enabled is True
    assert inactive_gene.group_rules_component == "gene"
    assert inactive_gene.instructions == "prompt:gene:gene_validation"

    assert stale_agent.is_active is False
    assert stale_agent.supervisor_enabled is False


def test_sync_skips_agent_with_missing_prompt(monkeypatch):
    """Agents with no prompt content are skipped with a warning, not a crash."""
    import src.lib.agent_studio.system_agent_sync as module

    db = _DBStub([])

    monkeypatch.setattr(
        module,
        "resolve_agent_config_sources",
        lambda _agents_path=None: (SimpleNamespace(folder_name="gene"),),
    )
    monkeypatch.setattr(
        module,
        "load_agent_definitions",
        lambda _agents_path=None, force_reload=False: {
            "gene_validation": _agent_definition("gene", "gene_validation"),
        },
    )
    # Return None for both DB prompt and file prompt
    monkeypatch.setattr(
        module,
        "_get_active_system_prompt",
        lambda _db, *, folder_name, agent_id: None,
    )
    monkeypatch.setattr(
        module,
        "_load_prompt_content_from_source",
        lambda _source: None,
    )

    result = module.sync_system_agents(db, force_reload=True)

    assert result["inserted"] == 0
    assert result["discovered"] == 0
    assert len(db.added) == 0


def test_sync_does_not_reenable_disabled_agent(monkeypatch):
    """Sync should not re-enable an agent that was disabled (e.g. by runtime validation)."""
    import src.lib.agent_studio.system_agent_sync as module

    disabled_gene = SimpleNamespace(
        agent_key="gene",
        user_id=None,
        name="Gene Agent",
        description="gene description",
        instructions="prompt:gene",
        model_id="gpt-5-mini",
        model_temperature=0.2,
        model_reasoning="medium",
        tool_ids=["agr_curation_query"],
        output_schema_key="GeneValidationEnvelope",
        group_rules_enabled=True,
        group_rules_component="gene",
        group_prompt_overrides={},
        icon="G",
        category="Validation",
        visibility="system",
        project_id=None,
        shared_at=None,
        template_source="gene_validation",
        supervisor_enabled=False,
        supervisor_description="Ask the gene agent",
        supervisor_batchable=False,
        supervisor_batching_entity=None,
        show_in_palette=True,
        is_active=False,  # Disabled by runtime validation
    )
    db = _DBStub([disabled_gene])

    monkeypatch.setattr(
        module,
        "resolve_agent_config_sources",
        lambda _agents_path=None: (SimpleNamespace(folder_name="gene"),),
    )
    monkeypatch.setattr(
        module,
        "load_agent_definitions",
        lambda _agents_path=None, force_reload=False: {
            "gene_validation": _agent_definition("gene", "gene_validation"),
        },
    )
    monkeypatch.setattr(
        module,
        "_get_active_system_prompt",
        lambda _db, *, folder_name, agent_id: "prompt:gene",
    )

    module.sync_system_agents(db, force_reload=True)

    # is_active should remain False — sync must not re-enable disabled agents
    assert disabled_gene.is_active is False
