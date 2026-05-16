"""Subject entity and AGM validator contract coverage."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.lib.config import agent_loader, schema_discovery
from src.schemas.domain_validator import DomainValidatorResultBase


REPO_ROOT = Path(__file__).resolve().parents[3]
ALLIANCE_AGENTS_PATH = REPO_ROOT / "packages" / "alliance" / "agents"
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


@pytest.fixture(autouse=True)
def _reset_loader_caches():
    agent_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    schema_discovery.reset_cache()


def _base_payload(*, agent_id: str, status: str = "resolved") -> dict[str, object]:
    return {
        "status": status,
        "request_id": "domain-validation:subject-1",
        "validator_binding_id": "annotation_subject_validation",
        "validator_agent": {
            "package_id": "agr.alliance",
            "agent_id": agent_id,
        },
        "target": {
            "domain_pack_id": "agr.alliance.phenotype",
            "object_type": "PhenotypeSubject",
            "object_id": "subject-1",
            "field_path": "subject_identifier",
            "expected_fields": ["subject_identifier", "subject_type", "taxon"],
            "input_values": {
                "subject_type": "agm",
                "subject_identifier": "MGI:8308849",
                "taxon": "NCBITaxon:10090",
            },
        },
        "resolved_values": {
            "subject_identifier": "MGI:8308849",
            "subject_type": "agm",
            "taxon": "NCBITaxon:10090",
        },
        "resolved_objects": [
            {
                "subject_identifier": "MGI:8308849",
                "subject_type": "agm",
                "taxon": "NCBITaxon:10090",
            }
        ],
        "missing_expected_fields": [],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "agr_curation_query",
                "method": "map_entity_curies_to_info",
                "query": {
                    "entity_type": "agm",
                    "entity_curies": ["MGI:8308849"],
                },
                "result_count": 1,
                "outcome": "success",
            }
        ],
        "curator_message": "Subject resolved.",
        "explanation": "The selected route returned one matching subject.",
    }


def test_subject_entity_and_agm_agents_load_with_shared_result_schemas(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_ROOT / "packages"))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    expected = {
        "subject_entity_validation": {
            "folder": "subject_entity",
            "schema": "SubjectEntityValidationResult",
            "extra_fields": {
                "normalized_subject_identifier",
                "normalized_subject_type",
                "normalized_subject_label",
                "taxon",
                "selected_validator",
                "subject_candidates",
                "unresolved_explanations",
            },
        },
        "agm_validation": {
            "folder": "agm",
            "schema": "AgmValidationResult",
            "extra_fields": {"agm_candidates", "unresolved_explanations"},
        },
    }

    for agent_id, case in expected.items():
        agent = agents[agent_id]
        schema = schemas[case["schema"]]

        assert agent.folder_name == case["folder"]
        assert agent.output_schema == case["schema"]
        assert agent.tools == ["agr_curation_query"]
        assert issubclass(schema, DomainValidatorResultBase)
        assert REQUIRED_SHARED_FIELDS <= set(schema.model_fields)
        assert case["extra_fields"] <= set(schema.model_fields)


def test_subject_entity_schema_preserves_selected_validator_and_candidates():
    schema = schema_discovery.discover_agent_schemas(
        ALLIANCE_AGENTS_PATH, force_reload=True
    )["SubjectEntityValidationResult"]
    payload = _base_payload(agent_id="subject_entity_validation")
    selected_validator = {
        "subject_type": "agm",
        "validator_agent": {
            "package_id": "agr.alliance",
            "agent_id": "agm_validation",
        },
        "tool_methods": ["map_entity_curies_to_info"],
        "route_reason": "subject_type explicitly selected the AGM route.",
    }
    payload.update(
        {
            "normalized_subject_identifier": "MGI:8308849",
            "normalized_subject_type": "agm",
            "normalized_subject_label": "Pax6<tm1>",
            "taxon": "NCBITaxon:10090",
            "selected_validator": selected_validator,
            "subject_candidates": [
                {
                    "subject_identifier": "MGI:8308849",
                    "subject_type": "agm",
                    "subject_label": "Pax6<tm1>",
                    "taxon": "NCBITaxon:10090",
                    "match_type": "curie",
                    "selected_validator": selected_validator,
                    "matched_fields": {"subject_identifier": "MGI:8308849"},
                }
            ],
            "unresolved_explanations": [],
        }
    )

    result = schema.model_validate(payload)

    assert result.status == "resolved"
    assert result.selected_validator is not None
    assert result.selected_validator.validator_agent.agent_id == "agm_validation"
    assert result.subject_candidates[0].subject_type == "agm"
    assert result.normalized_subject_identifier == "MGI:8308849"


def test_agm_schema_accepts_unresolved_candidates_and_rejects_metadata_status():
    schema = schema_discovery.discover_agent_schemas(
        ALLIANCE_AGENTS_PATH, force_reload=True
    )["AgmValidationResult"]
    payload = _base_payload(agent_id="agm_validation", status="unresolved")
    payload.update(
        {
            "resolved_values": {},
            "resolved_objects": [],
            "missing_expected_fields": ["subject_identifier", "taxon"],
            "curator_message": "AGM lookup requires taxon context for label lookup.",
            "explanation": "The request did not include taxon context.",
            "agm_candidates": [
                {
                    "agm_id": "MGI:8308849",
                    "label": "Pax6<tm1>",
                    "taxon": "NCBITaxon:10090",
                    "match_type": "curie",
                    "matched_fields": {"subject_label": "Pax6<tm1>"},
                }
            ],
            "unresolved_explanations": ["missing_taxon_for_label_lookup"],
        }
    )

    result = schema.model_validate(payload)

    assert result.status == "unresolved"
    assert result.agm_candidates[0].agm_id == "MGI:8308849"
    assert result.unresolved_explanations == ["missing_taxon_for_label_lookup"]

    payload["status"] = "under_development"
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_subject_entity_and_agm_prompts_pin_routing_and_output_policy():
    prompt_cases = {
        "subject_entity": [
            "Route only from the selected `subject_type`",
            "gene_validation",
            "allele_validation",
            "agm_validation",
            "`selected_validator`",
            "`unresolved_explanations`",
        ],
        "agm": [
            "map_entity_curies_to_info",
            "map_entity_names_to_curies",
            "`agm_candidates`",
            "missing taxon",
        ],
    }

    for folder, fragments in prompt_cases.items():
        prompt = yaml.safe_load(
            (ALLIANCE_AGENTS_PATH / folder / "prompt.yaml").read_text(
                encoding="utf-8"
            )
        )["content"]
        for fragment in fragments:
            assert fragment in prompt, f"{folder} prompt missing {fragment}"
        assert "repair_action" not in prompt, f"{folder} prompt still mentions repair_action"

    subject_entity_prompt = yaml.safe_load(
        (ALLIANCE_AGENTS_PATH / "subject_entity" / "prompt.yaml").read_text(
            encoding="utf-8"
        )
    )["content"]
    assert "repair_action" not in subject_entity_prompt
