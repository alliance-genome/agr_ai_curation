"""Unit tests for curation prep schemas."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from src.schemas.curation_prep import (
    CurationPrepAgentOutput,
    CurationPrepCandidate,
    CurationPrepScopeConfirmation,
    CurationPrepTokenUsage,
)
from src.schemas.curation_workspace import (
    CurationEvidenceSource,
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
    EvidenceSupportsDecision,
)


def make_anchor_payload() -> dict[str, object]:
    return {
        "anchor_kind": EvidenceAnchorKind.SNIPPET,
        "locator_quality": EvidenceLocatorQuality.EXACT_QUOTE,
        "supports_decision": EvidenceSupportsDecision.SUPPORTS,
        "snippet_text": "APOE was associated with the reported phenotype.",
        "sentence_text": "APOE was associated with the reported phenotype.",
        "viewer_search_text": "APOE was associated with the reported phenotype.",
        "page_number": 3,
        "section_title": "Results",
        "subsection_title": "Disease association",
        "figure_reference": "Fig. 2",
        "chunk_ids": ["chunk-1"],
    }


def make_candidate_payload() -> dict[str, object]:
    return {
        "adapter_key": "disease",
        "payload": {
            "gene_symbol": "APOE",
            "phenotype": {"label": "late onset phenotype"},
            "supporting_papers": [{"title": "APOE evidence paper"}],
        },
        "evidence_records": [
            {
                "evidence_record_id": "evidence-1",
                "source": CurationEvidenceSource.EXTRACTED,
                "extraction_result_id": "extract-1",
                "field_paths": ["gene_symbol", "phenotype.label"],
                "anchor": make_anchor_payload(),
                "notes": ["Exact quote from Results section."],
            }
        ],
        "conversation_context_summary": (
            "The user asked for curation prep focused on the APOE disease association."
        ),
    }


def test_curation_prep_candidate_accepts_payload_and_direct_evidence_records():
    candidate = CurationPrepCandidate(**make_candidate_payload())

    assert candidate.adapter_key == "disease"
    assert candidate.payload["gene_symbol"] == "APOE"
    assert candidate.evidence_records[0].field_paths == ["gene_symbol", "phenotype.label"]
    assert candidate.conversation_context_summary.startswith("The user asked")


def test_curation_prep_candidate_accepts_numeric_field_paths_inside_payload_lists():
    payload = make_candidate_payload()
    payload["evidence_records"] = [
        {
            "evidence_record_id": "evidence-1",
            "source": CurationEvidenceSource.EXTRACTED,
            "extraction_result_id": "extract-1",
            "field_paths": ["supporting_papers.0.title"],
            "anchor": make_anchor_payload(),
            "notes": [],
        }
    ]

    candidate = CurationPrepCandidate(**payload)

    assert candidate.evidence_records[0].field_paths == ["supporting_papers.0.title"]


def test_curation_prep_candidate_rejects_missing_evidence_records():
    payload = make_candidate_payload()
    payload["evidence_records"] = []

    with pytest.raises(ValidationError, match="evidence_records must contain at least one record"):
        CurationPrepCandidate(**payload)


def test_curation_prep_candidate_rejects_field_paths_missing_from_payload():
    payload = make_candidate_payload()
    payload["evidence_records"] = [
        {
            "evidence_record_id": "evidence-1",
            "source": CurationEvidenceSource.EXTRACTED,
            "field_paths": ["phenotype.normalized_id"],
            "anchor": make_anchor_payload(),
            "notes": [],
        }
    ]

    with pytest.raises(
        ValidationError,
        match="evidence_records.field_paths must resolve to payload field values",
    ):
        CurationPrepCandidate(**payload)


def test_curation_prep_candidate_rejects_non_json_payload_values():
    payload = make_candidate_payload()
    payload["payload"] = {"gene_symbol": math.nan}

    with pytest.raises(ValidationError, match="payload must contain only JSON-compatible values"):
        CurationPrepCandidate(**payload)


def test_curation_prep_scope_confirmation_requires_scope_when_confirmed():
    with pytest.raises(ValidationError, match="Confirmed scope must include at least one adapter"):
        CurationPrepScopeConfirmation(confirmed=True)


def test_curation_prep_output_schema_exposes_payload_and_evidence_records():
    candidate_schema = CurationPrepCandidate.model_json_schema()

    assert candidate_schema["properties"]["payload"]["type"] == "object"
    assert candidate_schema["properties"]["evidence_records"]["type"] == "array"
    assert "confidence" not in candidate_schema["properties"]
    assert "unresolved_ambiguities" not in candidate_schema["properties"]


def test_curation_prep_output_accepts_simplified_candidate_shape():
    output = CurationPrepAgentOutput.model_validate(
        {
            "candidates": [make_candidate_payload()],
            "run_metadata": {
                "model_name": "deterministic-mapper",
                "token_usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                "processing_notes": ["Prep mapper completed."],
                "warnings": [],
            },
        }
    )

    assert output.candidates[0].payload["phenotype"]["label"] == "late onset phenotype"


def test_curation_prep_token_usage_requires_total_not_less_than_parts():
    with pytest.raises(ValidationError, match="total_tokens must be greater than or equal"):
        CurationPrepTokenUsage(input_tokens=5, output_tokens=7, total_tokens=11)
