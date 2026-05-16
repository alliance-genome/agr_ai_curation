"""Prompt contract checks for repair-free target extractor schemas."""

import re
from pathlib import Path

import yaml

from src.lib.openai_agents import models as agent_models
from src.schemas.models import DomainEnvelopeExtractionResult


REPO_ROOT = Path(__file__).resolve().parents[3]

EXTRACTOR_PROMPTS = [
    "packages/alliance/agents/allele_extractor/prompt.yaml",
    "packages/alliance/agents/chemical_extractor/prompt.yaml",
    "packages/alliance/agents/disease_extractor/prompt.yaml",
    "packages/alliance/agents/gene_expression/prompt.yaml",
    "packages/alliance/agents/gene_extractor/prompt.yaml",
    "packages/alliance/agents/phenotype_extractor/prompt.yaml",
]

EXTRACTOR_OUTPUT_SCHEMAS = {
    "packages/alliance/agents/allele_extractor/agent.yaml": "AlleleExtractionResultEnvelope",
    "packages/alliance/agents/chemical_extractor/agent.yaml": "ChemicalExtractionResultEnvelope",
    "packages/alliance/agents/disease_extractor/agent.yaml": "DiseaseExtractionResultEnvelope",
    "packages/alliance/agents/gene_expression/agent.yaml": "GeneExpressionEnvelope",
    "packages/alliance/agents/gene_extractor/agent.yaml": "GeneExtractionResultEnvelope",
    "packages/alliance/agents/phenotype_extractor/agent.yaml": "PhenotypeResultEnvelope",
}

FORBIDDEN_TARGET_REPAIR_FRAGMENTS = [
    "repair_hints",
    "repair_notes",
    "repair_mode",
    "repair_patch",
    "repair_result",
    "repair_request",
    "repair_history",
    "repair_requested",
    "repair_patch_accepted",
    "repair_patch_rejected",
    "repair_final_classified",
    "ExtractorRepairResponse",
    "extractor_patch",
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


def test_extractor_prompts_do_not_expose_repair_surfaces():
    for relative_path in EXTRACTOR_PROMPTS:
        content = _content(relative_path)
        normalized_content = re.sub(r"\s+", " ", content)

        for fragment in FORBIDDEN_TARGET_REPAIR_FRAGMENTS:
            assert fragment not in normalized_content, f"{relative_path} contains {fragment}"


def test_extractor_prompts_delegate_unresolved_state_to_validators():
    required_fragments = [
        "Active validator bindings own",
        "validator result fields",
        "envelope validation findings",
    ]

    for relative_path in EXTRACTOR_PROMPTS:
        content = _content(relative_path)
        normalized_content = re.sub(r"\s+", " ", content).lower()
        for fragment in required_fragments:
            assert fragment.lower() in normalized_content, f"{relative_path} missing {fragment}"


def test_extractor_agents_use_plain_extraction_result_schemas():
    for relative_path, schema_name in EXTRACTOR_OUTPUT_SCHEMAS.items():
        agent_payload = _yaml(relative_path)
        assert agent_payload["output_schema"] == schema_name

        schema_cls = getattr(agent_models, schema_name)
        assert issubclass(schema_cls, DomainEnvelopeExtractionResult)
        assert not getattr(
            schema_cls,
            "__domain_envelope_extractor_repair_response__",
            False,
        )
