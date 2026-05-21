"""Tests for extraction-builder tool schema exposure."""

from __future__ import annotations

from src.lib.openai_agents.tools.extraction_builder import (
    finalize_allele_extraction,
    stage_allele_paper_evidence,
)


def test_builder_tools_compile_strict_function_schemas():
    assert stage_allele_paper_evidence.name == "stage_allele_paper_evidence"
    assert finalize_allele_extraction.name == "finalize_allele_extraction"

    stage_schema = stage_allele_paper_evidence.params_json_schema
    finalize_schema = finalize_allele_extraction.params_json_schema

    assert stage_schema["additionalProperties"] is False
    assert finalize_schema["additionalProperties"] is False
    assert set(stage_schema["required"]) >= {"mention_text", "evidence_record_ids"}
    assert set(finalize_schema["required"]) >= {
        "summary",
        "candidate_count",
        "kept_count",
        "excluded_count",
        "ambiguous_count",
    }
