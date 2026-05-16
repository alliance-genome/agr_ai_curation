"""Controlled vocabulary validation agent bundle tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.lib.config import agent_loader, prompt_loader, schema_discovery
from src.schemas.domain_validator import DomainValidatorResultBase

from ..packages import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__))
REPO_PACKAGES_DIR = REPO_ROOT / "packages"
AGENT_DIR = REPO_PACKAGES_DIR / "alliance" / "agents" / "controlled_vocabulary"


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
        "validator_binding_id": "relation_vocabulary_validation",
        "validator_agent": {
            "package_id": "agr.alliance",
            "agent_id": "controlled_vocabulary_validation",
        },
        "target": {
            "domain_pack_id": "agr.alliance.gene_expression",
            "object_type": "GeneExpressionAnnotation",
            "field_path": "relation.name",
            "expected_fields": ["term_name", "vocabulary", "internal_id"],
            "input_values": {
                "vocabulary": "relation",
                "term_name": "expressed in",
            },
        },
        "resolved_values": {},
        "resolved_objects": [],
        "missing_expected_fields": ["term_name", "vocabulary", "internal_id"],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "agr_curation_query",
                "method": "get_vocabulary_term",
                "query": {"vocabulary": "relation", "term_name": "expressed in"},
                "result_count": 0,
                "outcome": "not_found",
            }
        ],
        "curator_message": "No controlled vocabulary term matched.",
        "explanation": "The package vocabulary lookup returned no candidates.",
        "controlled_vocabulary_candidates": [],
    }
    payload.update(overrides)
    return payload


def test_controlled_vocabulary_agent_bundle_loads_with_narrow_tool_grant(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    agent = agents["controlled_vocabulary_validation"]
    assert agent.folder_name == "controlled_vocabulary"
    assert agent.category == "Validation"
    assert agent.tools == ["agr_curation_query"]
    assert agent.output_schema == "ControlledVocabularyValidationResult"

    schema = schemas["ControlledVocabularyValidationResult"]
    assert issubclass(schema, DomainValidatorResultBase)
    assert "controlled_vocabulary_candidates" in schema.model_fields


def test_controlled_vocabulary_prompt_declares_lookup_policy():
    prompt_payload = yaml.safe_load((AGENT_DIR / "prompt.yaml").read_text(encoding="utf-8"))
    prompt = prompt_payload["content"]

    assert "get_vocabulary_term" in prompt
    assert "search_vocabulary_terms" in prompt
    assert "obsolete" in prompt
    assert "ambiguous" in prompt
    assert "Do not convert vocabulary rows into ontology CURIEs" in prompt


@pytest.mark.parametrize(
    ("payload", "expected_status", "candidate_count"),
    [
        (
            _result_payload(
                status="resolved",
                resolved_values={
                    "term_name": "is_implicated_in",
                    "vocabulary": "Disease Relation",
                    "internal_id": 101,
                },
                resolved_objects=[
                    {
                        "internal_id": 101,
                        "vocabulary": "Disease Relation",
                        "term_name": "is_implicated_in",
                        "obsolete": False,
                    }
                ],
                missing_expected_fields=[],
                lookup_attempts=[
                    {
                        "provider": "agr_curation_query",
                        "method": "get_vocabulary_term",
                        "query": {
                            "vocabulary": "Disease Relation",
                            "term_name": "is_implicated_in",
                        },
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
                curator_message="Disease relation resolved.",
                explanation="The controlled vocabulary lookup returned one non-obsolete exact match.",
                controlled_vocabulary_candidates=[
                    {
                        "internal_id": 101,
                        "vocabulary": "Disease Relation",
                        "term_name": "is_implicated_in",
                        "abbreviation": "implicated",
                        "obsolete": False,
                        "synonyms": ["implicated in"],
                    }
                ],
            ),
            "resolved",
            1,
        ),
        (_result_payload(), "unresolved", 0),
        (
            _result_payload(
                candidates=[
                    {
                        "value": "202",
                        "label": "legacy_relation",
                        "object_type": "VocabularyTerm",
                        "matched_fields": {"synonym": "old relation"},
                        "details": {"obsolete": True},
                    }
                ],
                lookup_attempts=[
                    {
                        "provider": "agr_curation_query",
                        "method": "get_vocabulary_term",
                        "query": {
                            "vocabulary": "Disease Relation",
                            "synonym": "old relation",
                            "include_obsolete": True,
                        },
                        "result_count": 1,
                        "outcome": "conflict",
                    }
                ],
                curator_message="The matching vocabulary term is obsolete.",
                explanation="The exact synonym matched an obsolete term, so it was not resolved.",
                controlled_vocabulary_candidates=[
                    {
                        "internal_id": 202,
                        "vocabulary": "Disease Relation",
                        "term_name": "legacy_relation",
                        "obsolete": True,
                        "synonyms": ["old relation"],
                        "matched_value": "old relation",
                    }
                ],
            ),
            "unresolved",
            1,
        ),
        (
            _result_payload(
                candidates=[
                    {
                        "value": "301",
                        "label": "expressed in",
                        "object_type": "VocabularyTerm",
                        "matched_fields": {"term_name": "expressed in"},
                    },
                    {
                        "value": "302",
                        "label": "expressed in",
                        "object_type": "VocabularyTerm",
                        "matched_fields": {"term_name": "expressed in"},
                    },
                ],
                lookup_attempts=[
                    {
                        "provider": "agr_curation_query",
                        "method": "get_vocabulary_term",
                        "query": {"vocabulary": "relation", "term_name": "expressed in"},
                        "result_count": 2,
                        "outcome": "ambiguous",
                    }
                ],
                curator_message="Multiple vocabulary terms matched.",
                explanation="The exact lookup returned two plausible controlled vocabulary rows.",
                controlled_vocabulary_candidates=[
                    {
                        "internal_id": 301,
                        "vocabulary": "Relation",
                        "term_name": "expressed in",
                    },
                    {
                        "internal_id": 302,
                        "vocabulary": "Expression Relation",
                        "term_name": "expressed in",
                    },
                ],
            ),
            "unresolved",
            2,
        ),
    ],
)
def test_controlled_vocabulary_schema_accepts_resolution_cases(
    monkeypatch,
    payload,
    expected_status,
    candidate_count,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema_discovery.discover_agent_schemas(force_reload=True)
    schema = schema_discovery.get_schema_for_agent("controlled_vocabulary")

    result = schema(**payload)

    assert result.status == expected_status
    assert len(result.controlled_vocabulary_candidates) == candidate_count
