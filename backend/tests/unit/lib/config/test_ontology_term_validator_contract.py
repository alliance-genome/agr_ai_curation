"""Generic ontology term validator bundle contract checks."""

from pathlib import Path

import yaml

from src.lib.agent_studio.system_agent_sync import canonical_system_agent_key
from src.lib.config import agent_loader, prompt_loader, schema_discovery
from src.schemas.domain_validator import DomainValidatorResultBase

from ..packages import find_repo_root


REPO_ROOT = find_repo_root(Path(__file__))
REPO_PACKAGES_DIR = REPO_ROOT / "packages"
AGENT_DIR = REPO_PACKAGES_DIR / "alliance" / "agents" / "ontology_term"
TOOLS_BINDINGS_PATH = REPO_PACKAGES_DIR / "alliance" / "tools" / "bindings.yaml"


def _base_payload(*, status: str = "unresolved") -> dict:
    return {
        "status": status,
        "request_id": "domain-validation:ontology-term-contract",
        "validator_binding_id": "phenotype_term_ontology_validator",
        "validator_agent": {
            "package_id": "agr.alliance",
            "agent_id": "ontology_term_validation",
        },
        "target": {
            "domain_pack_id": "agr.alliance.phenotype",
            "object_type": "PhenotypeTerm",
            "object_id": "phenotype-term-1",
            "field_path": "curie",
            "expected_fields": ["curie", "label"],
            "input_values": {
                "label": "abnormal locomotor behavior",
                "ontology_family": "phenotype",
                "accepted_prefixes": ["MP", "WBPhenotype", "ZP"],
                "exact_match": True,
            },
        },
        "resolved_values": {},
        "resolved_objects": [],
        "missing_expected_fields": ["curie", "label"],
        "candidates": [
            {
                "value": "MP:0001392",
                "label": "abnormal locomotor behavior",
                "object_type": "OntologyTerm",
                "matched_fields": {"label": "abnormal locomotor behavior"},
                "details": {"ontology_type": "MPTerm"},
            }
        ],
        "lookup_attempts": [
            {
                "provider": "agr_curation_query",
                "method": "search_ontology_terms",
                "query": {
                    "term": "abnormal locomotor behavior",
                    "ontology_term_type": "MPTerm",
                    "exact_match": True,
                },
                "result_count": 2,
                "outcome": "ambiguous",
                "message": "Multiple phenotype terms matched the label.",
            }
        ],
        "curator_message": (
            "Multiple ontology terms matched; curator selection is required."
        ),
        "explanation": "The validator preserved candidates instead of guessing.",
        "ontology_term_candidates": [
            {
                "curie": "MP:0001392",
                "label": "abnormal locomotor behavior",
                "ontology_type": "MPTerm",
                "ontology_family": "phenotype",
                "accepted_prefix": "MP",
                "match_type": "exact_label",
                "context": {"exact_match": True},
            }
        ],
    }


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_ontology_term_validator_bundle_uses_shared_result_contract(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()

    agents = agent_loader.load_agent_definitions(force_reload=True)
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)
    agent = agents["ontology_term_validation"]
    schema = schemas["OntologyTermValidationResult"]

    assert agent.folder_name == "ontology_term"
    assert agent.system_agent_key == "ontology_term_validation"
    assert canonical_system_agent_key(agent) == "ontology_term_validation"
    assert agent.name == "Ontology Term Resolver Agent"
    assert agent.output_schema == "OntologyTermValidationResult"
    assert agent.tools == ["get_agent_contract", "agr_curation_query"]
    assert issubclass(schema, DomainValidatorResultBase)
    assert "ontology_term_candidates" in schema.model_fields

    unresolved = schema.model_validate(_base_payload(status="unresolved"))
    assert unresolved.status == "unresolved"
    assert unresolved.lookup_attempts[0].outcome == "ambiguous"
    assert unresolved.ontology_term_candidates[0].curie == "MP:0001392"

    resolved_payload = _base_payload(status="resolved")
    resolved_payload["resolved_values"] = {
        "curie": "MP:0001392",
        "label": "abnormal locomotor behavior",
    }
    resolved_payload["resolved_objects"] = [
        {"curie": "MP:0001392", "name": "abnormal locomotor behavior"}
    ]
    resolved_payload["missing_expected_fields"] = []
    resolved_payload["lookup_attempts"][0]["result_count"] = 1
    resolved_payload["lookup_attempts"][0]["outcome"] = "success"

    assert schema.model_validate(resolved_payload).status == "resolved"


def test_ontology_term_prompt_and_tool_grant_agree_on_available_methods():
    prompt_payload = _load_yaml(AGENT_DIR / "prompt.yaml")
    agent_payload = _load_yaml(AGENT_DIR / "agent.yaml")
    tools_payload = _load_yaml(TOOLS_BINDINGS_PATH)

    content = prompt_payload["content"]
    agr_tool = next(
        tool for tool in tools_payload["tools"] if tool["tool_id"] == "agr_curation_query"
    )
    methods = agr_tool["metadata"]["agent_methods"]["ontology_term_validation"][
        "methods"
    ]

    assert agent_payload["tools"] == ["get_agent_contract", "agr_curation_query"]
    assert agr_tool["tool_id"] == "agr_curation_query"
    for method in methods:
        assert method in agr_tool["metadata"]["methods"]
        assert f"`{method}`" in content

    for fragment in [
        "`curie`",
        "`label`",
        "`ontology_family`",
        "`accepted_prefixes`",
        "`exact_match`",
        "Do not guess",
        'status: "resolved"',
        'status: "unresolved"',
        "multiple plausible candidates",
        "lookup_attempts",
        "missing_expected_fields",
        "curator_message",
    ]:
        assert fragment in content

    assert "repair_action" not in content
    assert "under_development" not in content
