"""Tests for extraction-builder tool schema exposure."""

from __future__ import annotations

from src.lib.openai_agents.tools.extraction_builder import (
    finalize_allele_extraction,
    finalize_chemical_extraction,
    finalize_disease_extraction,
    finalize_gene_extraction,
    finalize_phenotype_extraction,
    stage_allele_paper_evidence,
    stage_chemical_condition_evidence,
    stage_disease_assertion_evidence,
    stage_gene_mention_evidence,
    stage_phenotype_assertion_evidence,
)


def test_builder_tools_compile_strict_function_schemas():
    assert stage_allele_paper_evidence.name == "stage_allele_paper_evidence"
    assert finalize_allele_extraction.name == "finalize_allele_extraction"
    assert stage_gene_mention_evidence.name == "stage_gene_mention_evidence"
    assert finalize_gene_extraction.name == "finalize_gene_extraction"
    assert stage_disease_assertion_evidence.name == "stage_disease_assertion_evidence"
    assert finalize_disease_extraction.name == "finalize_disease_extraction"
    assert stage_chemical_condition_evidence.name == (
        "stage_chemical_condition_evidence"
    )
    assert finalize_chemical_extraction.name == "finalize_chemical_extraction"
    assert stage_phenotype_assertion_evidence.name == (
        "stage_phenotype_assertion_evidence"
    )
    assert finalize_phenotype_extraction.name == "finalize_phenotype_extraction"

    stage_schema = stage_allele_paper_evidence.params_json_schema
    finalize_schema = finalize_allele_extraction.params_json_schema
    gene_stage_schema = stage_gene_mention_evidence.params_json_schema
    disease_stage_schema = stage_disease_assertion_evidence.params_json_schema
    chemical_stage_schema = stage_chemical_condition_evidence.params_json_schema
    phenotype_stage_schema = stage_phenotype_assertion_evidence.params_json_schema

    assert stage_schema["additionalProperties"] is False
    assert finalize_schema["additionalProperties"] is False
    assert gene_stage_schema["additionalProperties"] is False
    assert disease_stage_schema["additionalProperties"] is False
    assert chemical_stage_schema["additionalProperties"] is False
    assert phenotype_stage_schema["additionalProperties"] is False
    assert set(stage_schema["required"]) >= {"mention_text", "evidence_record_ids"}
    assert set(gene_stage_schema["required"]) >= {
        "mention",
        "evidence_record_ids",
        "identity_resolution_notes",
        "confidence",
    }
    assert set(disease_stage_schema["required"]) >= {
        "mention",
        "disease_name",
        "disease_relation_name",
        "data_provider_abbreviation",
        "evidence_record_ids",
        "role",
        "confidence",
    }
    assert set(chemical_stage_schema["required"]) >= {
        "source_chemical_mention",
        "condition_chemical_name",
        "evidence_record_ids",
        "role",
        "confidence",
    }
    assert set(phenotype_stage_schema["required"]) >= {
        "phenotype_statement",
        "phenotype_term_label",
        "subject_label",
        "data_provider_hint",
        "taxon_hint",
        "evidence_record_ids",
    }
    assert set(finalize_schema["required"]) >= {
        "summary",
        "candidate_count",
        "kept_count",
        "excluded_count",
        "ambiguous_count",
    }
