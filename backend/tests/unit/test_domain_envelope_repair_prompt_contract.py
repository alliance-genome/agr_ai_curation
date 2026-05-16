"""Prompt contract checks for validation-driven domain-envelope repair."""

import re
from pathlib import Path

import yaml

from src.lib.domain_packs.repair_patches import (
    DomainEnvelopeExtractorFinalClassification,
    DomainEnvelopeRepairPatch,
)
from src.lib.openai_agents import models as agent_models


REPO_ROOT = Path(__file__).resolve().parents[3]

EXTRACTOR_PROMPTS = [
    "packages/alliance/agents/allele_extractor/prompt.yaml",
    "packages/alliance/agents/chemical_extractor/prompt.yaml",
    "packages/alliance/agents/disease_extractor/prompt.yaml",
    "packages/alliance/agents/gene_expression/prompt.yaml",
    "packages/alliance/agents/gene_extractor/prompt.yaml",
    "packages/alliance/agents/phenotype_extractor/prompt.yaml",
]

ACTIVE_BINDING_EXTRACTOR_PROMPTS = [
    "packages/alliance/agents/allele_extractor/prompt.yaml",
    "packages/alliance/agents/chemical_extractor/prompt.yaml",
    "packages/alliance/agents/disease_extractor/prompt.yaml",
    "packages/alliance/agents/gene_expression/prompt.yaml",
    "packages/alliance/agents/gene_extractor/prompt.yaml",
    "packages/alliance/agents/phenotype_extractor/prompt.yaml",
]

EXTRACTOR_OUTPUT_SCHEMAS = {
    "packages/alliance/agents/allele_extractor/agent.yaml": "AlleleExtractorRepairResponse",
    "packages/alliance/agents/chemical_extractor/agent.yaml": "ChemicalExtractorRepairResponse",
    "packages/alliance/agents/disease_extractor/agent.yaml": "DiseaseExtractorRepairResponse",
    "packages/alliance/agents/gene_expression/agent.yaml": "GeneExpressionExtractorRepairResponse",
    "packages/alliance/agents/gene_extractor/agent.yaml": "GeneExtractorRepairResponse",
    "packages/alliance/agents/phenotype_extractor/agent.yaml": "PhenotypeExtractorRepairResponse",
}

REPAIR_AWARE_VALIDATOR_PROMPTS = [
    "packages/alliance/agents/chemical/prompt.yaml",
    "packages/alliance/agents/disease/prompt.yaml",
]

ONTOLOGY_CONTEXT_VALIDATOR_PROMPTS = [
    "alliance_agents/gene_ontology/prompt.yaml",
    "alliance_agents/go_annotations/prompt.yaml",
    "alliance_agents/ontology_mapping/prompt.yaml",
    "alliance_agents/orthologs/prompt.yaml",
    "packages/alliance/agents/gene_ontology/prompt.yaml",
    "packages/alliance/agents/go_annotations/prompt.yaml",
    "packages/alliance/agents/ontology_mapping/prompt.yaml",
    "packages/alliance/agents/orthologs/prompt.yaml",
]


def _content(relative_path: str) -> str:
    path = REPO_ROOT / relative_path
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    content = data.get("content")
    assert isinstance(content, str)
    return content


def _yaml(relative_path: str) -> dict:
    data = yaml.safe_load((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_supervisor_prompt_declares_repair_loop_outcomes():
    content = _content("config/agents/supervisor/prompt.yaml")

    for fragment in [
        '"repair_action": "repair_request"',
        '"repair_action": "extractor_patch"',
        '"expected_before"',
        "`validator_rerun`",
        "`not_found`",
        "`transient_service_failure`",
        "`blocked_validator`",
        "`retry_exhausted`",
        "`mark_under_development`",
        "`no_repair_possible`",
    ]:
        assert fragment in content


def test_extractor_prompts_declare_bounded_patch_contracts():
    for relative_path in EXTRACTOR_PROMPTS:
        content = _content(relative_path)
        for fragment in [
            'repair_action: "repair_request"',
            'repair_action: "extractor_patch"',
            "`expected_before`",
            "`validation_finding_id`",
            "`repair_attempt_id`",
            'repair_action: "no_repair_possible"',
            '`status: "no_repair_possible"`',
            'repair_action: "mark_under_development"',
            '`status: "under_development"`',
            "Never patch protected fields",
        ]:
            assert fragment in content, f"{relative_path} missing {fragment}"


def test_extractor_repair_prompt_examples_use_payload_relative_patch_paths():
    forbidden_patterns = [
        r"curatable_objects\[\d+\]\.payload\.",
        r"metadata\.evidence_records\[\d+\]\.chunk_id",
    ]
    forbidden_fragments = [
        "payload field paths or metadata paths",
        "payload fields or metadata paths",
        "object refs, or metadata paths",
    ]

    for relative_path in EXTRACTOR_PROMPTS:
        content = _content(relative_path)
        for pattern in forbidden_patterns:
            assert re.search(pattern, content) is None, f"{relative_path} has {pattern}"
        for fragment in forbidden_fragments:
            assert fragment not in content, f"{relative_path} has {fragment}"


def test_extractor_prompts_delegate_active_bound_fields_to_validators():
    required_fragments = [
        "active validator bindings",
        "authority",
        "evidence-backed unresolved objects",
        "selector inputs",
        "unresolved validator outcomes remain envelope validation findings",
        "bounded repair request",
    ]
    forbidden_fragments = [
        "Normalizes retained",
        "Normalize retained",
        "Normalize the retained",
        "normalized with `agr_curation_query`",
        "normalize retained",
        "returned by AGR lookup",
    ]

    for relative_path in ACTIVE_BINDING_EXTRACTOR_PROMPTS:
        content = _content(relative_path)
        normalized_content = re.sub(r"\s+", " ", content).lower()

        for fragment in required_fragments:
            assert fragment in normalized_content, f"{relative_path} missing {fragment}"
        for fragment in forbidden_fragments:
            assert fragment not in content, f"{relative_path} contains {fragment}"


def test_validator_prompts_keep_validation_separate_from_patching():
    for relative_path in REPAIR_AWARE_VALIDATOR_PROMPTS:
        content = _content(relative_path)
        for fragment in [
            "Keep validator responsibility separate from extraction",
            "`not_found`",
            "`transient_service_failure`",
            "`blocked_validator`",
            "`mark_under_development`",
            'Only an extractor may return `repair_action: "extractor_patch"`',
        ]:
            assert fragment in content, f"{relative_path} missing {fragment}"


def test_ontology_context_validator_prompts_use_shared_result_semantics():
    required_shared_fields = [
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
    ]
    forbidden_runtime_fragments = [
        "under_development",
        "under-development validator",
        "blocked_validator",
        "mark_under_development",
        "repair_action",
        "repair-focused",
    ]

    for relative_path in ONTOLOGY_CONTEXT_VALIDATOR_PROMPTS:
        content = _content(relative_path)
        normalized_content = re.sub(r"\s+", " ", content)

        assert "bounded context enrichment" in normalized_content, relative_path
        assert (
            "not claim that the surrounding domain envelope is ready"
            in normalized_content
        )
        assert 'status: "resolved"' in content, relative_path
        assert 'status: "unresolved"' in content, relative_path
        assert "result`, `validation_result`" in content, relative_path
        assert "Do not continue beyond the bounded investigation" in normalized_content

        for field_name in required_shared_fields:
            assert field_name in content, f"{relative_path} missing {field_name}"
        for fragment in forbidden_runtime_fragments:
            assert fragment not in content, f"{relative_path} contains {fragment}"


def test_extractor_agents_use_repair_capable_output_schemas():
    for relative_path, schema_name in EXTRACTOR_OUTPUT_SCHEMAS.items():
        agent_payload = _yaml(relative_path)
        assert agent_payload["output_schema"] == schema_name

        schema_cls = getattr(agent_models, schema_name)
        assert getattr(schema_cls, "__domain_envelope_extractor_repair_response__")

        patch_response = schema_cls.model_validate(
            {
                "repair_action": "extractor_patch",
                "patch_id": "repair-patch:test",
                "envelope_id": "env-1",
                "expected_revision": 1,
                "source_finding_ids": ["validation:1"],
                "operations": [
                    {
                        "op": "replace",
                        "object_ref": {
                            "pending_ref_id": "object-1",
                            "object_type": "Gene",
                        },
                        "field_path": "primary_external_id",
                        "expected_before": "OLD:1",
                        "after": "NEW:1",
                        "reason": "Validator supplied the grounded identifier.",
                    }
                ],
                "rationale": "Bounded repair against the requested field path.",
            }
        )
        assert isinstance(patch_response.root, DomainEnvelopeRepairPatch)

        final_response = schema_cls.model_validate(
            {
                "repair_action": "mark_under_development",
                "envelope_id": "env-1",
                "expected_revision": 1,
                "status": "under_development",
                "reason": "The domain-pack field is not fully defined.",
                "finding_ids": ["validation:1"],
                "object_ref": {
                    "pending_ref_id": "object-1",
                    "object_type": "Gene",
                },
                "field_path": "primary_external_id",
            }
        )
        assert isinstance(final_response.root, DomainEnvelopeExtractorFinalClassification)
