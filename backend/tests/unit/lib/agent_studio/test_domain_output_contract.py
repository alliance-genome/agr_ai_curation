"""Packaged extraction identity is not a model-response schema."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.schemas.agent_execution_revision import AgentOutputContract, DomainExtractionRef


BUILDER = {
    "package_id": "agr.alliance",
    "agent_id": "gene_extractor",
    "domain_pack_id": "agr.alliance.gene",
}


def test_builder_domain_contract_round_trips_without_model_schema():
    contract = AgentOutputContract(
        output_state="structured_extraction",
        output_mode="domain",
        domain_extraction_ref=DomainExtractionRef(**BUILDER),
    )
    assert contract.output_schema_key is None
    assert contract.model_dump(mode="json")["domain_extraction_ref"] == BUILDER
    assert AgentOutputContract.model_validate_json(contract.model_dump_json()) == contract


@pytest.mark.parametrize("payload", [
    {"output_state": "none", "output_mode": None, "output_schema_key": None, "generic_profile_ref": None},
    {"output_state": "structured_extraction", "output_mode": "domain", "output_schema_key": "GeneValidationResult", "generic_profile_ref": None},
    {"output_state": "structured_extraction", "output_mode": "unprofiled_generic", "output_schema_key": None, "generic_profile_ref": None},
])
def test_existing_contract_serialization_is_unchanged(payload):
    contract = AgentOutputContract.model_validate(payload)
    assert contract.model_dump(mode="json") == payload
    assert json.loads(contract.model_dump_json()) == payload


@pytest.mark.parametrize("changes", [
    {"output_state": "none", "output_mode": None},
    {"output_mode": "unprofiled_generic"},
    {"output_mode": "profile_bound_generic"},
    {"output_schema_key": "GeneExtractionResultEnvelope"},
    {"output_schema_key": ""},
    {"domain_extraction_ref": {**BUILDER, "package_id": " "}},
    {"domain_extraction_ref": {**BUILDER, "unexpected": "value"}},
])
def test_builder_identity_cannot_leak_between_output_modes(changes):
    payload = {
        "output_state": "structured_extraction", "output_mode": "domain",
        "domain_extraction_ref": BUILDER, **changes,
    }
    with pytest.raises(ValidationError):
        AgentOutputContract.model_validate(payload)


def test_domain_without_schema_or_builder_remains_invalid():
    with pytest.raises(ValidationError):
        AgentOutputContract(output_state="structured_extraction", output_mode="domain")


@pytest.fixture
def installed_builder(monkeypatch):
    from src.lib.agent_studio import domain_output_contract
    from src.lib.config.agent_loader import AgentAccessConfig, AgentDefinition, CurationConfig
    from src.lib.flows import validation_attachments
    from src.lib.openai_agents import streaming_tools

    definition = AgentDefinition(
        folder_name="gene_extractor", agent_id="gene_extractor", name="Gene extractor",
        package_id="agr.alliance", tools=["finalize_gene_extraction"],
        curation=CurationConfig(adapter_key="gene", domain_pack_id="agr.alliance.gene", launchable=True),
        access=AgentAccessConfig(allowed_group_ids=["FB"]),
        structured_finalization={"kind": "selected-package-contract"},
    )
    monkeypatch.setattr(
        domain_output_contract, "get_agent_definition_for_package",
        lambda package_id, agent_id: definition
        if (package_id, agent_id) == ("agr.alliance", "gene_extractor") else None,
    )
    monkeypatch.setattr(streaming_tools, "builder_finalization_tool_names", lambda: frozenset(["finalize_gene_extraction", "finalize_allele_extraction"]))
    metadata = SimpleNamespace(
        pack_id="agr.alliance.gene", version="1.0.0", metadata={},
        status=SimpleNamespace(value="under_development"), object_definitions=[],
    )
    registry = SimpleNamespace(domain_pack=SimpleNamespace(metadata=metadata), bindings=[])
    monkeypatch.setattr(validation_attachments, "domain_pack_validation_registries", lambda: {"agr.alliance.gene": registry})
    return definition


def test_capture_uses_selected_builder_not_inherited_curation(monkeypatch, installed_builder):
    from src.lib.agent_studio import catalog_service, custom_agent_service
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.schemas.agent_execution_revision import AgentExecutionSnapshot
    from tests.unit.lib.agent_studio.test_execution_snapshot import agent

    monkeypatch.setattr(custom_agent_service, "_system_managed_tool_ids", lambda *_: ["finalize_gene_extraction"])
    monkeypatch.setattr(catalog_service, "_inherited_curation_definition_for_db_agent", lambda _: None)
    head = agent()
    head.tool_ids = ["finalize_gene_extraction"]
    head.inherited_allowed_group_ids = []
    output = AgentOutputContract(output_state="structured_extraction", output_mode="domain", domain_extraction_ref=DomainExtractionRef(**BUILDER))
    saved = capture_execution_snapshot(None, head, output, active_group_ids=["FB"])
    assert saved.output_contract.output_schema_key is None
    assert saved.curation == {"adapter_key": "gene", "domain_pack_id": "agr.alliance.gene", "launchable": True}
    assert saved.inherited_allowed_group_ids == ["FB"]
    assert saved.structured_finalization == {"kind": "selected-package-contract"}
    assert saved.tool_ids == head.tool_ids  # selection never silently grants tools
    original = saved.model_dump(mode="json")
    fingerprint = saved.fingerprint()
    installed_builder.curation.adapter_key = "changed"
    installed_builder.structured_finalization["kind"] = "changed"
    assert saved.model_dump(mode="json") == original
    assert AgentExecutionSnapshot.model_validate(original).fingerprint() == fingerprint
    original["curation"]["domain_pack_id"] = "agr.alliance.allele"
    with pytest.raises(ValidationError, match="Saved curation must match"):
        AgentExecutionSnapshot.model_validate(original)


@pytest.mark.parametrize("change", [
    {"package_id": "wrong-package"},
    {"agent_id": "wrong-agent"},
    {"domain_pack_id": "agr.alliance.allele"},
])
def test_selection_does_not_resolve_a_different_installed_identity(installed_builder, change):
    from src.lib.agent_studio.domain_output_contract import resolve_domain_extraction_definition
    from src.schemas.agent_execution_revision import DomainExtractionRef

    with pytest.raises(ValueError):
        resolve_domain_extraction_definition(DomainExtractionRef(**{**BUILDER, **change}))


@pytest.mark.parametrize("changes", [
    {"tool_ids": []},
    {"tool_ids": ["finalize_allele_extraction"]},
    {"tool_ids": ["finalize_gene_extraction", "finalize_allele_extraction"]},
    {"allowed_group_ids": []},
    {"allowed_group_ids": ["WB"]},
    {"active_group_ids": ["WB"]},
    {"active_group_ids": []},
])
def test_selection_cannot_grant_tools_or_broaden_access(installed_builder, changes):
    from src.lib.agent_studio.domain_output_contract import validate_domain_extraction_selection

    with pytest.raises(ValueError):
        validate_domain_extraction_selection(installed_builder, **{
            "tool_ids": ["finalize_gene_extraction"], "allowed_group_ids": ["FB"],
            "active_group_ids": ["FB"], "group_tool_policy": {}, **changes,
        })


def test_selection_preserves_package_tool_policy(installed_builder):
    from src.lib.agent_studio.domain_output_contract import validate_domain_extraction_selection
    from src.lib.group_tool_policy import GroupToolPolicy, GroupToolRule

    policy = GroupToolPolicy(rules=[GroupToolRule("lookup", ["FB"], ["field"])])
    installed_builder.group_tool_policy = policy
    args = {"tool_ids": ["finalize_gene_extraction"], "allowed_group_ids": ["FB"], "active_group_ids": ["FB"]}
    with pytest.raises(ValueError, match="package-owned group tool policy"):
        validate_domain_extraction_selection(installed_builder, group_tool_policy={}, **args)
    validate_domain_extraction_selection(installed_builder, group_tool_policy=policy.to_dict(), **args)


def test_human_and_ai_discovery_use_current_package_access(monkeypatch, installed_builder):
    from src.lib.agent_studio.domain_output_contract import domain_extraction_ref_for_agent
    from src.lib.config import agent_loader

    monkeypatch.setattr(agent_loader, "get_agent_definition", lambda key: installed_builder if key == "public_builder" else None)
    selected = domain_extraction_ref_for_agent("public_builder", active_group_ids=["FB"])
    assert selected is not None and selected.model_dump() == BUILDER
    assert domain_extraction_ref_for_agent("public_builder", active_group_ids=["WB"]) is None
    assert domain_extraction_ref_for_agent("public_builder", active_group_ids=[]) is None
    assert domain_extraction_ref_for_agent("missing", active_group_ids=["FB"]) is None
    installed_builder.output_schema = "ValidationResult"
    assert domain_extraction_ref_for_agent("public_builder", active_group_ids=["FB"]) is None


def test_initial_baseline_uses_declared_builder_not_schema_null_alone(monkeypatch, installed_builder):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.domain_output_contract import initial_agent_output_contract

    head = SimpleNamespace(output_schema_key=None)
    monkeypatch.setattr(catalog_service, "_inherited_curation_definition_for_db_agent", lambda _: installed_builder)
    selected = initial_agent_output_contract(head)
    assert selected.domain_extraction_ref is not None
    assert selected.domain_extraction_ref.model_dump() == BUILDER
    assert selected.output_schema_key is None
    installed_builder.curation.domain_pack_id = "generic"
    assert initial_agent_output_contract(head).output_mode == "unprofiled_generic"
    monkeypatch.setattr(catalog_service, "_inherited_curation_definition_for_db_agent", lambda _: None)
    assert initial_agent_output_contract(head).output_state == "none"
    head.output_schema_key = "ValidationResult"
    assert initial_agent_output_contract(head).output_schema_key == "ValidationResult"


def test_none_with_builder_tools_rejects_save_and_historical_execution(installed_builder, monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from tests.unit.lib.agent_studio.test_execution_snapshot import agent

    head = agent()
    none = AgentOutputContract(output_state="none")
    monkeypatch.setattr(catalog_service, "_inherited_curation_definition_for_db_agent", lambda _: None)
    saved = capture_execution_snapshot(None, head, none)
    head.tool_ids = ["finalize_gene_extraction"]
    with pytest.raises(ValueError, match="No structured output is incompatible"):
        capture_execution_snapshot(None, head, none)
    # An old contradictory snapshot remains inspectable, but cannot execute.
    saved.tool_ids = ["finalize_gene_extraction"]
    original = saved.model_dump(mode="json")
    with pytest.raises(ValueError, match="save a new revision"):
        catalog_service._create_db_agent(head, execution_snapshot=saved)
    assert saved.model_dump(mode="json") == original
