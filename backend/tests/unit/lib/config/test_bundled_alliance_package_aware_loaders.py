"""Bundled Alliance-default loader coverage for shipped repo packages."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from src.lib.config import agent_loader, agent_sources, prompt_loader, schema_discovery
from src.lib.config.tool_policy_defaults_loader import load_tool_policy_defaults
from src.schemas.domain_validator import (
    DomainValidatorResultBase,
    is_domain_validator_result_schema,
)
from src.schemas.models import DomainEnvelopeExtractionResult

from ..packages import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__))
REPO_PACKAGES_DIR = REPO_ROOT / "packages"
REPO_LEGACY_AGENTS_DIR = REPO_ROOT / "alliance_agents"


def _iter_dict_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dict_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dict_nodes(child)


@pytest.fixture(autouse=True)
def _reset_loader_caches():
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()


def test_bundled_alliance_load_agent_definitions_defaults_to_runtime_packages(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)

    assert "gene_validation" in agents
    assert agents["gene_validation"].folder_name == "gene"
    assert agents["gene_validation"].output_schema == "GeneResultEnvelope"


def test_bundled_alliance_gene_extractor_declares_record_evidence(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    gene_extractor = agents["gene_extractor"]

    assert gene_extractor.tools == [
        "search_document",
        "read_section",
        "read_subsection",
        "record_evidence",
        "agr_curation_query",
    ]


def test_bundled_alliance_gene_expression_declares_record_evidence(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    gene_expression = agents["gene_expression_extraction"]

    assert gene_expression.tools == [
        "search_document",
        "read_section",
        "read_subsection",
        "record_evidence",
        "agr_curation_query",
    ]


def test_bundled_alliance_allele_extractor_declares_record_evidence(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    allele_extractor = agents["allele_extractor"]

    assert allele_extractor.tools == [
        "search_document",
        "read_section",
        "read_subsection",
        "record_evidence",
        "agr_curation_query",
    ]


def test_bundled_alliance_owns_agr_curation_tool_policy(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    policies = load_tool_policy_defaults(packages_dir=REPO_PACKAGES_DIR)

    policy = policies["agr_curation_query"]
    assert policy.display_name == "AGR Curation Query"
    assert policy.source_label is not None
    assert "package default 'agr.alliance'" in policy.source_label


def test_bundled_alliance_gene_extractor_prompt_teaches_verified_evidence_flow(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    source = next(
        item
        for item in agent_sources.resolve_agent_config_sources(REPO_PACKAGES_DIR)
        if item.folder_name == "gene_extractor"
    )

    prompt_payload = yaml.safe_load(source.prompt_yaml.read_text(encoding="utf-8"))
    prompt_content = str(prompt_payload["content"])

    assert "<few_shot_examples>" in prompt_content
    assert prompt_content.count("record_evidence(") >= 3
    assert '"status": "verified"' in prompt_content
    assert "`verified_quote`" in prompt_content
    assert "`chunk_id`" in prompt_content
    assert "Do not call `record_evidence` for every gene mentioned anywhere in the paper." in prompt_content
    assert "Do not place free-text evidence summaries inside these fields." in prompt_content


def test_bundled_alliance_load_prompts_tracks_package_paths(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    db = MagicMock()
    captured_calls = []

    monkeypatch.setattr(prompt_loader, "_acquire_advisory_lock", lambda _db: (True, True))
    monkeypatch.setattr(prompt_loader, "_release_advisory_lock", lambda _db: None)

    def _capture_upsert(**kwargs):
        captured_calls.append(kwargs)
        return (True, 1)

    monkeypatch.setattr(prompt_loader, "_upsert_prompt", _capture_upsert)

    result = prompt_loader.load_prompts(db=db, force_reload=True)

    assert result["base_prompts"] >= 1
    assert result["group_rules"] >= 1
    assert any(
        call["source_file"] == "packages/agr.alliance/agents/gene/prompt.yaml"
        and call["prompt_type"] == "system"
        for call in captured_calls
    )
    assert any(
        call["source_file"] == "packages/agr.alliance/agents/gene/group_rules/fb.yaml"
        and call["prompt_type"] == "group_rules"
        and call["group_id"] == "FB"
        for call in captured_calls
    )
    assert any(
        call["source_file"] == "config/agents/supervisor/prompt.yaml"
        and call["prompt_type"] == "system"
        for call in captured_calls
    )


def test_bundled_alliance_discover_agent_schemas_defaults_to_runtime_packages(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    assert "GeneResultEnvelope" in schemas
    assert schema_discovery.get_schema_for_agent("gene").__name__ == "GeneResultEnvelope"


def test_bundled_alliance_validation_agent_schemas_are_binding_ready(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)
    validation_agents = [
        agent
        for agent in agents.values()
        if agent.category == "Validation" and agent.output_schema
    ]

    readiness = {
        agent.folder_name: is_domain_validator_result_schema(
            schema_discovery.resolve_output_schema(agent.output_schema or "")
        )
        for agent in validation_agents
    }

    assert validation_agents
    assert all(readiness.values()), readiness
    for agent in validation_agents:
        schema = schemas[agent.output_schema]
        assert issubclass(schema, DomainValidatorResultBase)
        status_schema = schema.model_json_schema()["properties"]["status"]
        assert "under_development" not in status_schema.get("enum", [])


def test_bundled_alliance_ontology_context_schemas_use_shared_validator_root(
    monkeypatch,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agent_loader.load_agent_definitions(force_reload=True)
    schema_discovery.discover_agent_schemas(force_reload=True)

    expected_schemas = {
        "gene_ontology": "GOTermResultEnvelope",
        "go_annotations": "GOAnnotationsResult",
        "ontology_mapping": "OntologyMappingEnvelope",
        "orthologs": "OrthologsResult",
    }
    shared_fields = set(DomainValidatorResultBase.model_fields)

    for agent_name, schema_name in expected_schemas.items():
        schema = schema_discovery.get_schema_for_agent(agent_name)

        assert schema is not None
        assert schema.__name__ == schema_name
        assert issubclass(schema, DomainValidatorResultBase)
        assert shared_fields.issubset(schema.model_fields)
        assert "result" not in schema.model_fields
        assert "validation_result" not in schema.model_fields

    ontology_schema = schema_discovery.get_schema_for_agent("ontology_mapping")
    assert ontology_schema is not None
    assert "unmapped_labels" in ontology_schema.model_fields
    assert "unmapped_terms" not in ontology_schema.model_fields


def test_legacy_ontology_mapping_schema_matches_package_validator_shape(
    monkeypatch,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    package_schema_path = (
        REPO_PACKAGES_DIR / "alliance" / "agents" / "ontology_mapping" / "schema.py"
    )
    legacy_schema_path = REPO_LEGACY_AGENTS_DIR / "ontology_mapping" / "schema.py"
    package_source = package_schema_path.read_text(encoding="utf-8")
    legacy_source = legacy_schema_path.read_text(encoding="utf-8")

    assert legacy_source == package_source
    for obsolete_fragment in (
        "StructuredMessageEnvelope",
        "ConfigDict",
        "from typing import List",
        "actor:",
        "findings:",
        "unmapped_terms",
    ):
        assert obsolete_fragment not in legacy_source

    schema_discovery.discover_agent_schemas(REPO_PACKAGES_DIR, force_reload=True)
    package_schema = schema_discovery.get_schema_for_agent("ontology_mapping")
    assert package_schema is not None
    package_contract = package_schema.model_json_schema()

    schema_discovery.reset_cache()
    legacy_schemas = schema_discovery._load_schema_module(
        legacy_schema_path,
        "ontology_mapping",
        configured_schema="OntologyMappingEnvelope",
    )
    legacy_schema = legacy_schemas.get("OntologyMappingEnvelope")

    assert legacy_schema is not None
    assert legacy_schema.__name__ == package_schema.__name__
    assert issubclass(legacy_schema, DomainValidatorResultBase)
    assert legacy_schema.model_json_schema() == package_contract
    for obsolete_field in (
        "actor",
        "findings",
        "unmapped_terms",
        "result",
        "validation_result",
    ):
        assert obsolete_field not in legacy_schema.model_fields
    assert "unmapped_labels" in legacy_schema.model_fields


def test_bundled_alliance_ontology_context_agents_are_not_active_readiness_gates():
    context_agent_ids = {
        "gene_ontology_lookup",
        "go_annotations_lookup",
        "ontology_mapping_lookup",
        "orthologs_lookup",
    }
    active_agent_ids = set()

    for domain_pack_path in (REPO_ROOT / "packages/alliance/domain_packs").glob(
        "*/domain_pack.yaml"
    ):
        payload = yaml.safe_load(domain_pack_path.read_text(encoding="utf-8"))
        for node in _iter_dict_nodes(payload):
            bindings = node.get("validator_bindings")
            if not isinstance(bindings, dict):
                continue
            active_bindings = bindings.get("active", [])
            if not isinstance(active_bindings, list):
                continue
            for binding in active_bindings:
                if not isinstance(binding, dict):
                    continue
                validator_agent = binding.get("validator_agent")
                if isinstance(validator_agent, dict):
                    agent_id = validator_agent.get("agent_id")
                    if isinstance(agent_id, str):
                        active_agent_ids.add(agent_id)

    assert active_agent_ids
    assert context_agent_ids.isdisjoint(active_agent_ids)


def test_bundled_alliance_output_resolution_prefers_package_schema(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    schema_discovery.discover_agent_schemas(force_reload=True)

    resolved = schema_discovery.resolve_output_schema("GeneResultEnvelope")

    assert resolved is schema_discovery.get_agent_schema("GeneResultEnvelope")
    assert resolved.__module__.startswith("agent_schemas.agr_alliance.gene")


def test_bundled_alliance_extractors_use_repair_response_schema(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    expected = {
        "gene_expression": "GeneExpressionExtractorRepairResponse",
        "gene_extractor": "GeneExtractorRepairResponse",
        "allele_extractor": "AlleleExtractorRepairResponse",
        "disease_extractor": "DiseaseExtractorRepairResponse",
        "chemical_extractor": "ChemicalExtractorRepairResponse",
        "phenotype_extractor": "PhenotypeExtractorRepairResponse",
    }
    for agent_name, schema_name in expected.items():
        discovered_schema = schema_discovery.get_schema_for_agent(agent_name)

        assert schema_name in schemas
        assert discovered_schema is not None
        assert discovered_schema.__name__ == schema_name
        assert getattr(
            discovered_schema,
            "__domain_envelope_extractor_repair_response__",
        )


def test_bundled_alliance_first_pass_extractors_still_register_domain_envelope_schema(
    monkeypatch,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    expected = (
        "GeneExpressionEnvelope",
        "GeneExtractionResultEnvelope",
        "AlleleExtractionResultEnvelope",
        "DiseaseExtractionResultEnvelope",
        "ChemicalExtractionResultEnvelope",
        "PhenotypeResultEnvelope",
    )
    for schema_name in expected:
        discovered_schema = schemas[schema_name]

        assert issubclass(discovered_schema, DomainEnvelopeExtractionResult)
        assert "curatable_objects" in discovered_schema.model_fields
        assert "metadata" in discovered_schema.model_fields
