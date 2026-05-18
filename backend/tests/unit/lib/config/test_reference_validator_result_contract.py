"""Reference validator shared result contract checks."""

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

REFERENCE_SPECIFIC_FIELDS = {
    "reference_id",
    "curie",
    "title",
    "short_citation",
    "cross_references",
    "source",
    "match_type",
    "confidence",
    "ambiguity",
    "no_match",
    "candidate_references",
    "failure_classification",
}


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


def _prompt_content() -> str:
    data = _load_yaml("packages/alliance/agents/reference/prompt.yaml")
    content = data.get("content")
    assert isinstance(content, str)
    return content


def _base_payload(*, status: str = "resolved") -> dict:
    return {
        "status": status,
        "request_id": "request:reference-contract",
        "validator_binding_id": "source_reference_validation",
        "validator_agent": {
            "package_id": "agr.alliance",
            "agent_id": "reference_validation",
        },
        "target": {
            "domain_pack_id": "agr.alliance.allele",
            "object_type": "Reference",
            "object_id": "reference-1",
            "object_role": "validated_reference",
            "field_path": "reference_id",
            "expected_fields": ["reference_id", "curie", "title"],
            "input_values": {"pmid": "PMID:27528223"},
        },
        "resolved_values": {
            "reference_id": 101000000924191,
            "curie": "AGRKB:101000000924191",
            "title": "Suppressed Helicobacter pylori study",
        },
        "resolved_objects": [
            {
                "reference_id": 101000000924191,
                "curie": "AGRKB:101000000924191",
                "title": "Suppressed Helicobacter pylori study",
                "short_citation": "Hahm KB et al., 1997",
                "cross_references": ["PMID:27528223"],
                "source": "literature_es",
            }
        ],
        "missing_expected_fields": [],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "agr_literature_reference_lookup",
                "method": "get_literature_reference",
                "query": {
                    "value": "PMID:27528223",
                    "exact_match": True,
                    "limit": 20,
                },
                "result_count": 1,
                "outcome": "success",
                "message": "Resolved one literature reference.",
            }
        ],
        "curator_message": "Resolved one literature reference.",
        "explanation": "The PMID lookup returned one API-backed literature_es match.",
        "reference_id": 101000000924191,
        "curie": "AGRKB:101000000924191",
        "title": "Suppressed Helicobacter pylori study",
        "short_citation": "Hahm KB et al., 1997",
        "cross_references": ["PMID:27528223"],
        "source": "literature_es",
        "match_type": "exact_identifier",
        "confidence": 1.0,
        "candidate_references": [
            {
                "reference_id": 101000000924191,
                "curie": "AGRKB:101000000924191",
                "title": "Suppressed Helicobacter pylori study",
                "short_citation": "Hahm KB et al., 1997",
                "cross_references": ["PMID:27528223"],
                "source": "literature_es",
            }
        ],
    }


def _ambiguous_payload() -> dict:
    payload = _base_payload(status="unresolved")
    candidates = [
        {
            "reference_id": None,
            "curie": "AGRKB:1",
            "title": "Reference title one",
            "short_citation": "Author A et al.",
            "cross_references": ["PMID:1"],
            "source": "literature_es",
        },
        {
            "reference_id": None,
            "curie": "AGRKB:2",
            "title": "Reference title two",
            "short_citation": "Author B et al.",
            "cross_references": ["PMID:2"],
            "source": "literature_es",
        },
    ]
    payload.update(
        {
            "resolved_values": {},
            "resolved_objects": [],
            "missing_expected_fields": ["reference_id", "curie", "title"],
            "candidates": [
                {
                    "value": candidate["curie"],
                    "label": candidate["title"],
                    "object_type": "Reference",
                    "matched_fields": {"title": candidate["title"]},
                    "details": candidate,
                }
                for candidate in candidates
            ],
            "lookup_attempts": [
                {
                    "provider": "agr_literature_reference_lookup",
                    "method": "search_literature_references",
                    "query": {
                        "value": "Reference title",
                        "exact_match": False,
                        "limit": 20,
                    },
                    "result_count": 2,
                    "outcome": "ambiguous",
                    "message": "Found two candidate literature references.",
                }
            ],
            "curator_message": "Reference lookup is ambiguous.",
            "explanation": "The fuzzy search returned two API-backed candidates.",
            "reference_id": None,
            "curie": None,
            "title": None,
            "short_citation": None,
            "cross_references": [],
            "source": "literature_es",
            "match_type": "ambiguous",
            "confidence": None,
            "ambiguity": {
                "query": "Reference title",
                "candidate_count": 2,
                "source": "literature_es",
            },
            "candidate_references": candidates,
        }
    )
    return payload


def _no_match_payload() -> dict:
    payload = _base_payload(status="unresolved")
    payload.update(
        {
            "resolved_values": {},
            "resolved_objects": [],
            "missing_expected_fields": ["reference_id", "curie"],
            "candidates": [],
            "lookup_attempts": [
                {
                    "provider": "agr_literature_reference_lookup",
                    "method": "get_literature_reference",
                    "query": {
                        "value": "MGI:6254583",
                        "exact_match": True,
                        "limit": 20,
                    },
                    "result_count": 0,
                    "outcome": "not_found",
                    "message": "No literature reference matched.",
                }
            ],
            "curator_message": "No reference matched MGI:6254583.",
            "explanation": "The exact lookup returned no API-backed reference.",
            "reference_id": None,
            "curie": None,
            "title": None,
            "short_citation": None,
            "cross_references": [],
            "source": "literature_es",
            "match_type": "no_match",
            "confidence": None,
            "no_match": {
                "query": "MGI:6254583",
                "source": "literature_es",
            },
            "candidate_references": [],
        }
    )
    return payload


def _upstream_failure_payload() -> dict:
    payload = _base_payload(status="unresolved")
    payload.update(
        {
            "resolved_values": {},
            "resolved_objects": [],
            "missing_expected_fields": ["reference_id", "curie"],
            "candidates": [],
            "lookup_attempts": [
                {
                    "provider": "agr_literature_reference_lookup",
                    "method": "get_literature_reference",
                    "query": {
                        "value": "PMID:27528223",
                        "exact_match": True,
                        "limit": 20,
                    },
                    "result_count": 0,
                    "outcome": "error",
                    "message": "Literature reference search could not reach the upstream index.",
                }
            ],
            "curator_message": "Reference lookup is temporarily unavailable.",
            "explanation": "The package tool returned an upstream failure.",
            "reference_id": None,
            "curie": None,
            "title": None,
            "short_citation": None,
            "cross_references": [],
            "source": "literature_es",
            "match_type": "upstream_failure",
            "confidence": None,
            "candidate_references": [],
            "failure_classification": "transient",
        }
    )
    return payload


def test_reference_agent_uses_shared_result_schema_and_package_tool(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    agent = agents["reference_validation"]
    schema = schemas["ReferenceValidationResult"]

    assert agent.folder_name == "reference"
    assert agent.output_schema == "ReferenceValidationResult"
    assert agent.tools == ["get_agent_contract", "agr_literature_reference_lookup"]
    assert issubclass(schema, DomainValidatorResultBase)
    assert REQUIRED_SHARED_FIELDS <= set(schema.model_fields)
    assert REFERENCE_SPECIFIC_FIELDS <= set(schema.model_fields)

    status_schema = schema.model_json_schema()["properties"]["status"]
    assert status_schema.get("enum") == ["resolved", "unresolved"]

    validated = schema.model_validate(_base_payload())
    assert validated.status == "resolved"
    assert validated.source == "literature_es"

    with pytest.raises(ValueError):
        schema.model_validate(_base_payload(status="under_development"))


@pytest.mark.parametrize(
    ("payload_factory", "expected_status", "expected_match_type"),
    [
        (_base_payload, "resolved", "exact_identifier"),
        (_ambiguous_payload, "unresolved", "ambiguous"),
        (_no_match_payload, "unresolved", "no_match"),
        (_upstream_failure_payload, "unresolved", "upstream_failure"),
    ],
)
def test_reference_schema_accepts_lookup_outcome_shapes(
    monkeypatch,
    payload_factory,
    expected_status,
    expected_match_type,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(
        force_reload=True
    )["ReferenceValidationResult"]

    result = schema.model_validate(payload_factory())

    assert result.status == expected_status
    assert result.match_type == expected_match_type
    assert result.source == "literature_es"
    assert result.lookup_attempts


def test_reference_schema_accepts_fuzzy_resolved_shape(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(
        force_reload=True
    )["ReferenceValidationResult"]
    payload = _base_payload()
    payload["match_type"] = "fuzzy_search"
    payload["confidence"] = 0.86
    payload["lookup_attempts"][0]["method"] = "search_literature_references"
    payload["lookup_attempts"][0]["query"] = {
        "value": "Hahm KB Suppressed Helicobacter pylori",
        "exact_match": False,
        "limit": 20,
    }
    payload["explanation"] = "The fuzzy search returned one API-backed literature_es match."

    result = schema.model_validate(payload)

    assert result.status == "resolved"
    assert result.match_type == "fuzzy_search"
    assert result.confidence == 0.86


def test_reference_prompt_contract_uses_tool_before_deciding():
    content = _prompt_content()

    assert "ReferenceValidationResult" in content
    assert "agr_literature_reference_lookup" in content
    for field_name in [*REQUIRED_SHARED_FIELDS, *REFERENCE_SPECIFIC_FIELDS]:
        assert f"`{field_name}`" in content, f"reference prompt missing {field_name}"

    for fragment in [
        "Exact identifier lookup first",
        "Exact title lookup next",
        "Fuzzy title, short-citation, or abstract/citation-fragment search last",
        "Call `agr_literature_reference_lookup` before returning",
        'method: "get_literature_reference"',
        'method: "search_literature_references"',
        'status: "resolved"',
        'status: "unresolved"',
        "lookup_attempts[].outcome",
        "missing_expected_fields",
        "candidates",
        "ambiguous",
        "No literature reference matched",
        "upstream error",
        "Do not wrap",
        "Never call Elasticsearch",
    ]:
        assert fragment in content, f"reference prompt missing {fragment}"

    assert "repair_action" not in content
    assert "no_repair_output" not in content
    assert 'status: "under_development"' not in content
    assert "results: List" not in content
    assert "query_summary:" not in content
    assert "not_found:" not in content
