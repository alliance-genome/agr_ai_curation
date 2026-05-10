"""Prompt contract checks for validation-driven domain-envelope repair."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]

EXTRACTOR_PROMPTS = [
    "packages/alliance/agents/allele_extractor/prompt.yaml",
    "packages/alliance/agents/chemical_extractor/prompt.yaml",
    "packages/alliance/agents/disease_extractor/prompt.yaml",
    "packages/alliance/agents/gene_expression/prompt.yaml",
    "packages/alliance/agents/gene_extractor/prompt.yaml",
    "packages/alliance/agents/phenotype_extractor/prompt.yaml",
]

VALIDATOR_PROMPTS = [
    "packages/alliance/agents/allele/prompt.yaml",
    "packages/alliance/agents/chemical/prompt.yaml",
    "packages/alliance/agents/disease/prompt.yaml",
    "packages/alliance/agents/gene/prompt.yaml",
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
            'repair_action: "mark_under_development"',
            "Never patch protected fields",
        ]:
            assert fragment in content, f"{relative_path} missing {fragment}"


def test_validator_prompts_keep_validation_separate_from_patching():
    for relative_path in VALIDATOR_PROMPTS:
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
