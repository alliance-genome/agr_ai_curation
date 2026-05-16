"""Experimental condition validation agent bundle tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.lib.config import agent_loader, prompt_loader, schema_discovery
from src.schemas.domain_validator import DomainValidatorResultBase

from ..packages import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__))
REPO_PACKAGES_DIR = REPO_ROOT / "packages"
AGENT_DIR = REPO_PACKAGES_DIR / "alliance" / "agents" / "experimental_condition"
TOOLS_BINDINGS_PATH = REPO_PACKAGES_DIR / "alliance" / "tools" / "bindings.yaml"


@pytest.fixture(autouse=True)
def _reset_loader_caches():
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()


def _base_payload(**overrides):
    payload = {
        "status": "unresolved",
        "request_id": "request-1",
        "validator_binding_id": "experimental_condition_validation",
        "validator_agent": {
            "package_id": "agr.alliance",
            "agent_id": "experimental_condition_validation",
        },
        "target": {
            "domain_pack_id": "agr.alliance.chemical_condition",
            "object_type": "ChemicalCondition",
            "object_id": "chemical-condition-1",
            "field_path": "condition_chemical.curie",
            "expected_fields": ["condition_id", "normalized_components"],
            "input_values": {
                "condition_class_curie": "ZECO:0000111",
                "condition_chemical_curie": "CHEBI:9168",
            },
        },
        "resolved_values": {},
        "resolved_objects": [],
        "missing_expected_fields": ["condition_id", "normalized_components"],
        "candidates": [],
        "lookup_attempts": [],
        "curator_message": "Experimental condition has unresolved components.",
        "explanation": "One or more component lookups did not resolve.",
        "condition_status": "unresolved",
        "condition_id": None,
        "normalized_components": [],
        "component_validations": [],
        "unresolved_components": ["condition_chemical"],
    }
    payload.update(overrides)
    return payload


def test_experimental_condition_agent_bundle_loads_with_component_tool_grants(
    monkeypatch,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    agent = agents["experimental_condition_validation"]
    assert agent.folder_name == "experimental_condition"
    assert agent.category == "Validation"
    assert agent.tools == ["agr_curation_query", "chebi_api_call"]
    assert agent.output_schema == "ExperimentalConditionValidationResult"

    schema = schemas["ExperimentalConditionValidationResult"]
    assert issubclass(schema, DomainValidatorResultBase)
    assert {
        "condition_status",
        "condition_id",
        "normalized_components",
        "component_validations",
        "unresolved_components",
    } <= set(schema.model_fields)


def test_experimental_condition_prompt_and_tool_grant_name_lower_level_methods():
    prompt_payload = yaml.safe_load((AGENT_DIR / "prompt.yaml").read_text(encoding="utf-8"))
    agent_payload = yaml.safe_load((AGENT_DIR / "agent.yaml").read_text(encoding="utf-8"))
    tools_payload = yaml.safe_load(TOOLS_BINDINGS_PATH.read_text(encoding="utf-8"))

    content = prompt_payload["content"]
    agr_tool = tools_payload["tools"][0]
    methods = agr_tool["metadata"]["agent_methods"][
        "experimental_condition_validation"
    ]["methods"]

    assert agent_payload["tools"] == ["agr_curation_query", "chebi_api_call"]
    for method in methods:
        assert method in agr_tool["metadata"]["methods"]
        assert f"`{method}`" in content

    for fragment in [
        "`ontology_term_validation`",
        "`controlled_vocabulary_validation`",
        "`data_provider_validation`",
        "`chemical_validation`",
        "`chebi_api_call`",
        'status: "resolved"',
        'status: "unresolved"',
        "component_validations",
        "unresolved_components",
        "Do not return `repair_action`",
    ]:
        assert fragment in content


def test_experimental_condition_schema_accepts_resolved_chemical_condition(
    monkeypatch,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema_discovery.discover_agent_schemas(force_reload=True)
    schema = schema_discovery.get_schema_for_agent("experimental_condition")

    payload = _base_payload(
        status="resolved",
        resolved_values={
            "condition_id": "ZECO:0000111|CHEBI:9168|3 pM|chemical:sirolimus",
            "normalized_components": [
                {
                    "component_type": "condition_chemical",
                    "field_path": "condition_chemical.curie",
                    "resolved_values": {"chebi_id": "CHEBI:9168", "name": "sirolimus"},
                }
            ],
        },
        missing_expected_fields=[],
        lookup_attempts=[
            {
                "provider": "ebi_chebi",
                "method": "compound",
                "query": {"url": "https://www.ebi.ac.uk/chebi/backend/api/public/compound/9168/"},
                "result_count": 1,
                "outcome": "success",
            }
        ],
        curator_message="Experimental condition components resolved.",
        explanation="The condition class and chemical components resolved unambiguously.",
        condition_status="resolved",
        condition_id="ZECO:0000111|CHEBI:9168|3 pM|chemical:sirolimus",
        normalized_components=[
            {
                "component_type": "condition_chemical",
                "field_path": "condition_chemical.curie",
                "resolved_values": {"chebi_id": "CHEBI:9168", "name": "sirolimus"},
                "resolved_objects": [{"curie": "CHEBI:9168", "name": "sirolimus"}],
                "source_inputs": {"condition_chemical_curie": "CHEBI:9168"},
                "validator_agent": {
                    "package_id": "agr.alliance",
                    "agent_id": "chemical_validation",
                },
            }
        ],
        component_validations=[
            {
                "component_type": "condition_chemical",
                "field_path": "condition_chemical.curie",
                "required": True,
                "validator_agent": {
                    "package_id": "agr.alliance",
                    "agent_id": "chemical_validation",
                },
                "status": "resolved",
                "selected_inputs": {"condition_chemical_curie": "CHEBI:9168"},
                "resolved_values": {"chebi_id": "CHEBI:9168", "name": "sirolimus"},
                "missing_expected_fields": [],
                "candidates": [],
                "lookup_attempts": [
                    {
                        "provider": "ebi_chebi",
                        "method": "compound",
                        "query": {
                            "url": "https://www.ebi.ac.uk/chebi/backend/api/public/compound/9168/"
                        },
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
                "curator_message": "Chemical component resolved.",
                "explanation": "ChEBI returned one matching compound.",
            }
        ],
        unresolved_components=[],
    )

    result = schema.model_validate(payload)

    assert result.status == "resolved"
    assert result.condition_status == "resolved"
    assert result.normalized_components[0].validator_agent.agent_id == "chemical_validation"


def test_experimental_condition_schema_preserves_ambiguous_disease_condition(
    monkeypatch,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema_discovery.discover_agent_schemas(force_reload=True)
    schema = schema_discovery.get_schema_for_agent("experimental_condition")

    payload = _base_payload(
        target={
            "domain_pack_id": "agr.alliance.disease",
            "object_type": "DiseaseAnnotation",
            "object_id": "disease-assertion-1",
            "field_path": "condition_relations[0].conditions",
            "expected_fields": ["condition_id", "normalized_components"],
            "input_values": {
                "condition_class_curie": "ZECO:0000111",
                "condition_taxon_curie": "NCBITaxon:10090",
            },
        },
        candidates=[
            {
                "value": "NCBITaxon:10090",
                "label": "Mus musculus",
                "object_type": "OntologyTerm",
                "matched_fields": {"curie": "NCBITaxon:10090"},
            }
        ],
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "get_ontology_term",
                "query": {"term": "NCBITaxon:10090"},
                "result_count": 2,
                "outcome": "ambiguous",
            }
        ],
        component_validations=[
            {
                "component_type": "condition_taxon",
                "field_path": "condition_relations[0].conditions[0].condition_taxon.curie",
                "required": True,
                "validator_agent": {
                    "package_id": "agr.alliance",
                    "agent_id": "ontology_term_validation",
                },
                "status": "unresolved",
                "selected_inputs": {"condition_taxon_curie": "NCBITaxon:10090"},
                "resolved_values": {},
                "missing_expected_fields": ["taxon"],
                "candidates": [
                    {
                        "value": "NCBITaxon:10090",
                        "label": "Mus musculus",
                        "object_type": "OntologyTerm",
                        "matched_fields": {"curie": "NCBITaxon:10090"},
                    }
                ],
                "lookup_attempts": [
                    {
                        "provider": "agr_curation_query",
                        "method": "get_ontology_term",
                        "query": {"term": "NCBITaxon:10090"},
                        "result_count": 2,
                        "outcome": "ambiguous",
                    }
                ],
                "curator_message": "Taxon component is ambiguous.",
                "explanation": "The ontology lookup returned multiple plausible taxon records.",
            }
        ],
        unresolved_components=["condition_taxon"],
    )

    result = schema.model_validate(payload)

    assert result.status == "unresolved"
    assert result.condition_status == "unresolved"
    assert result.component_validations[0].candidates[0].value == "NCBITaxon:10090"
