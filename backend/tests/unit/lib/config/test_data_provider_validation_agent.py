"""Data provider validation agent bundle tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.lib.config import agent_loader, prompt_loader, schema_discovery
from src.schemas.domain_validator import DomainValidatorResultBase

from ..packages import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__))
REPO_PACKAGES_DIR = REPO_ROOT / "packages"
AGENT_DIR = REPO_PACKAGES_DIR / "alliance" / "agents" / "data_provider"
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


def _result_payload(**overrides):
    payload = {
        "status": "unresolved",
        "request_id": "request-1",
        "validator_binding_id": "data_provider_validation",
        "validator_agent": {
            "package_id": "agr.alliance",
            "agent_id": "data_provider_validation",
        },
        "target": {
            "domain_pack_id": "agr.alliance.gene_expression",
            "object_type": "GeneExpressionAnnotation",
            "field_path": "data_provider.abbreviation",
            "expected_fields": ["abbreviation", "taxon"],
            "input_values": {
                "abbreviation": "WB",
                "taxon": "NCBITaxon:6239",
            },
        },
        "resolved_values": {},
        "resolved_objects": [],
        "missing_expected_fields": ["abbreviation", "taxon"],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "agr_curation_query",
                "method": "get_data_provider",
                "query": {"abbreviation": "WB", "taxon_id": "NCBITaxon:6239"},
                "result_count": 0,
                "outcome": "not_found",
            }
        ],
        "curator_message": "No data provider matched.",
        "explanation": "The package data provider lookup returned no exact match.",
        "data_provider_candidates": [],
        "mismatch_explanations": [],
    }
    payload.update(overrides)
    return payload


def test_data_provider_agent_bundle_loads_with_narrow_tool_grant(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    agent = agents["data_provider_validation"]
    assert agent.folder_name == "data_provider"
    assert agent.category == "Validation"
    assert agent.tools == ["agr_curation_query"]
    assert agent.output_schema == "DataProviderValidationResult"

    schema = schemas["DataProviderValidationResult"]
    assert issubclass(schema, DomainValidatorResultBase)
    assert "data_provider_candidates" in schema.model_fields
    assert "mismatch_explanations" in schema.model_fields


def test_data_provider_prompt_and_tool_grant_agree_on_available_methods():
    prompt_payload = yaml.safe_load((AGENT_DIR / "prompt.yaml").read_text(encoding="utf-8"))
    agent_payload = yaml.safe_load((AGENT_DIR / "agent.yaml").read_text(encoding="utf-8"))
    tools_payload = yaml.safe_load(TOOLS_BINDINGS_PATH.read_text(encoding="utf-8"))

    content = prompt_payload["content"]
    agr_tool = tools_payload["tools"][0]
    methods = agr_tool["metadata"]["agent_methods"]["data_provider_validation"][
        "methods"
    ]

    assert agent_payload["tools"] == ["agr_curation_query"]
    for method in methods:
        assert method in agr_tool["metadata"]["methods"]
        assert f"`{method}`" in content

    for fragment in [
        "`abbreviation`",
        "`provider_name`",
        "`taxon`",
        "provider/taxon mismatch",
        'status: "resolved"',
        'status: "unresolved"',
        "lookup_attempts",
        "mismatch_explanations",
        "Do not return `repair_action`",
    ]:
        assert fragment in content


@pytest.mark.parametrize(
    ("payload", "expected_status", "candidate_count", "mismatch_count"),
    [
        (
            _result_payload(
                status="resolved",
                resolved_values={
                    "abbreviation": "WB",
                    "taxon": "NCBITaxon:6239",
                },
                resolved_objects=[
                    {
                        "abbreviation": "WB",
                        "taxon_id": "NCBITaxon:6239",
                        "display_name": "WormBase",
                    }
                ],
                missing_expected_fields=[],
                lookup_attempts=[
                    {
                        "provider": "agr_curation_query",
                        "method": "get_data_provider",
                        "query": {"abbreviation": "WB", "taxon_id": "NCBITaxon:6239"},
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
                curator_message="Data provider resolved.",
                explanation="The provider abbreviation and taxon matched WormBase.",
                data_provider_candidates=[
                    {
                        "abbreviation": "WB",
                        "taxon_id": "NCBITaxon:6239",
                        "display_name": "WormBase",
                        "match_type": "abbreviation",
                        "matched_value": "WB",
                        "taxon_matches": True,
                    }
                ],
            ),
            "resolved",
            1,
            0,
        ),
        (_result_payload(), "unresolved", 0, 0),
        (
            _result_payload(
                candidates=[
                    {
                        "value": "WB",
                        "label": "WormBase",
                        "object_type": "DataProvider",
                        "matched_fields": {"abbreviation": "WB"},
                        "details": {
                            "taxon_id": "NCBITaxon:6239",
                            "mismatch_explanation": (
                                "Taxon 'NCBITaxon:7227' does not match provider 'WB' taxon 'NCBITaxon:6239'."
                            ),
                        },
                    }
                ],
                lookup_attempts=[
                    {
                        "provider": "agr_curation_query",
                        "method": "get_data_provider",
                        "query": {"abbreviation": "WB", "taxon_id": "NCBITaxon:7227"},
                        "result_count": 1,
                        "outcome": "conflict",
                    }
                ],
                curator_message="Provider WB does not match the supplied taxon.",
                explanation="The provider lookup found WB, but its taxon is NCBITaxon:6239.",
                data_provider_candidates=[
                    {
                        "abbreviation": "WB",
                        "taxon_id": "NCBITaxon:6239",
                        "display_name": "WormBase",
                        "match_type": "abbreviation",
                        "matched_value": "WB",
                        "taxon_matches": False,
                        "mismatch_explanation": (
                            "Taxon 'NCBITaxon:7227' does not match provider 'WB' taxon 'NCBITaxon:6239'."
                        ),
                    }
                ],
                mismatch_explanations=[
                    "Taxon 'NCBITaxon:7227' does not match provider 'WB' taxon 'NCBITaxon:6239'."
                ],
            ),
            "unresolved",
            1,
            1,
        ),
    ],
)
def test_data_provider_schema_accepts_resolution_cases(
    monkeypatch,
    payload,
    expected_status,
    candidate_count,
    mismatch_count,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema_discovery.discover_agent_schemas(force_reload=True)
    schema = schema_discovery.get_schema_for_agent("data_provider")

    result = schema(**payload)

    assert result.status == expected_status
    assert len(result.data_provider_candidates) == candidate_count
    assert len(result.mismatch_explanations) == mismatch_count
