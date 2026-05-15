"""Disease and chemical validator shared result contract checks."""

from pathlib import Path

import pytest
import yaml

from src.lib.config import agent_loader, prompt_loader, schema_discovery
from src.schemas.domain_validator import DomainValidatorResultBase

from ..packages import find_repo_root


REPO_ROOT = find_repo_root(Path(__file__))
REPO_PACKAGES_DIR = REPO_ROOT / "packages"

REQUIRED_SHARED_FIELDS = {
    "status",
    "request_id",
    "validator_binding_id",
    "validator_agent",
    "target",
    "resolved_values",
    "resolved_objects",
    "missing_expected_fields",
    "candidates",
    "lookup_attempts",
    "curator_message",
    "explanation",
}

VALIDATOR_CASES = {
    "disease": {
        "agent_id": "disease_validation",
        "schema_name": "DiseaseValidationResult",
        "tool": "curation_db_sql",
        "provider": "alliance_curation_db",
        "domain_pack": "packages/alliance/domain_packs/disease/domain_pack.yaml",
    },
    "chemical": {
        "agent_id": "chemical_validation",
        "schema_name": "ChemicalValidationResult",
        "tool": "chebi_api_call",
        "provider": "ebi_chebi",
        "domain_pack": "packages/alliance/domain_packs/chemical_condition/domain_pack.yaml",
    },
}

LEGACY_TOP_LEVEL_FIELDS = {"results", "query_summary", "not_found"}


@pytest.fixture(autouse=True)
def _reset_loader_caches():
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()


def _load_yaml(relative_path: str) -> dict:
    data = yaml.safe_load((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _prompt_content(folder: str) -> str:
    data = _load_yaml(f"packages/alliance/agents/{folder}/prompt.yaml")
    content = data.get("content")
    assert isinstance(content, str)
    return content


def _base_payload(case: dict, *, status: str = "resolved") -> dict:
    return {
        "status": status,
        "request_id": "request:disease-chemical-contract",
        "validator_binding_id": "binding:example",
        "validator_agent": {
            "package_id": "agr.alliance",
            "agent_id": case["agent_id"],
        },
        "target": {
            "domain_pack_id": "agr.alliance.example",
            "object_type": "ExampleObject",
            "object_id": "object-1",
            "object_role": "validated_reference",
            "field_path": "payload.curie",
            "expected_fields": ["curie"],
            "input_values": {"curie": "EXAMPLE:1"},
        },
        "resolved_values": {"curie": "EXAMPLE:1"},
        "resolved_objects": [{"curie": "EXAMPLE:1", "name": "Example"}],
        "missing_expected_fields": [],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": case["provider"],
                "method": "contract_test",
                "query": {"curie": "EXAMPLE:1"},
                "result_count": 1,
                "outcome": "success",
            }
        ],
        "curator_message": "Resolved by contract test.",
        "explanation": "The validator found an unambiguous provider-backed match.",
    }


def test_disease_and_chemical_agents_use_shared_result_schemas(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    for folder, case in VALIDATOR_CASES.items():
        agent = agents[case["agent_id"]]
        schema = schemas[case["schema_name"]]

        assert agent.folder_name == folder
        assert agent.output_schema == case["schema_name"]
        assert agent.tools == [case["tool"]]
        assert issubclass(schema, DomainValidatorResultBase)
        assert REQUIRED_SHARED_FIELDS <= set(schema.model_fields)
        assert not (LEGACY_TOP_LEVEL_FIELDS & set(schema.model_fields))

        status_schema = schema.model_json_schema()["properties"]["status"]
        assert status_schema.get("enum") == ["resolved", "unresolved"]

        validated = schema.model_validate(_base_payload(case))
        assert validated.status == "resolved"

        with pytest.raises(ValueError):
            schema.model_validate(_base_payload(case, status="under_development"))


def test_disease_and_chemical_prompt_contracts_use_shared_fields():
    for folder, case in VALIDATOR_CASES.items():
        content = _prompt_content(folder)

        assert case["schema_name"] in content
        assert case["tool"] in content
        for field_name in REQUIRED_SHARED_FIELDS:
            assert f"`{field_name}`" in content, f"{folder} prompt missing {field_name}"

        for fragment in [
            'status: "resolved"',
            'status: "unresolved"',
            "lookup_attempts[].outcome",
            "missing_expected_fields",
            "candidates",
            "ambiguous",
            "Do not wrap",
            "Do not return `repair_action`",
            'Only an extractor may return `repair_action: "extractor_patch"`',
        ]:
            assert fragment in content, f"{folder} prompt missing {fragment}"

        assert 'status: "under_development"' not in content
        assert "results: List" not in content
        assert "query_summary:" not in content
        assert "not_found:" not in content


def test_active_disease_and_chemical_bindings_resolve_to_migrated_agents(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    for case in VALIDATOR_CASES.values():
        domain_pack = _load_yaml(case["domain_pack"])
        active_bindings = domain_pack["metadata"]["validator_bindings"]["active"]
        matching_bindings = [
            binding
            for binding in active_bindings
            if binding.get("validator_agent", {}).get("agent_id") == case["agent_id"]
        ]

        assert matching_bindings
        agent = agents[case["agent_id"]]
        schema = schemas[agent.output_schema]
        assert issubclass(schema, DomainValidatorResultBase)
        for binding in matching_bindings:
            assert binding["validator_agent"]["package_id"] == "agr.alliance"
            assert isinstance(binding.get("expected_result_fields"), dict)
