"""Tests for shared extractor contract schema fields and reason-code taxonomy."""

import json

from src.lib.openai_agents.models import GeneExpressionEnvelope as RuntimeGeneExpressionEnvelope
from src.schemas.models import DomainEnvelopeExtractionResult, ExtractionEnvelopeMetadata
from src.schemas.models.base import ExclusionReasonCode, ExclusionRecord


def test_runtime_gene_expression_envelope_has_contract_defaults():
    envelope = RuntimeGeneExpressionEnvelope()

    assert envelope.curatable_objects == []
    assert envelope.metadata.raw_mentions == []
    assert envelope.metadata.evidence_records == []
    assert envelope.metadata.normalization_notes == []
    assert envelope.metadata.exclusions == []
    assert envelope.metadata.ambiguities == []
    assert envelope.metadata.notes == []
    assert envelope.metadata.repair_notes == []
    assert envelope.run_summary.candidate_count == 0


def test_runtime_gene_expression_envelope_accepts_reason_coded_exclusions():
    exclusion = ExclusionRecord(
        mention="marker transgene only",
        reason_code=ExclusionReasonCode.MARKER_ONLY_VISUALIZATION,
        evidence_record_ids=["evidence-marker-only"],
    )

    envelope = RuntimeGeneExpressionEnvelope(metadata={"exclusions": [exclusion]})

    assert len(envelope.metadata.exclusions) == 1
    assert (
        envelope.metadata.exclusions[0].reason_code
        == ExclusionReasonCode.MARKER_ONLY_VISUALIZATION
    )


def test_runtime_gene_expression_schema_contains_reason_code_enum_values():
    schema_text = json.dumps(RuntimeGeneExpressionEnvelope.model_json_schema())

    assert "previously_reported" in schema_text
    assert "marker_only_visualization" in schema_text
    assert "insufficient_experimental_evidence" in schema_text


def test_core_schema_envelopes_include_shared_extractor_contract_fields():
    expected_fields = {
        "curatable_objects",
        "metadata",
        "run_summary",
        "schema_ref",
        "repair_mode",
    }

    assert expected_fields.issubset(set(DomainEnvelopeExtractionResult.model_fields.keys()))


def test_extraction_envelope_metadata_includes_non_semantic_side_channels():
    expected_fields = {
        "raw_mentions",
        "evidence_records",
        "normalization_notes",
        "exclusions",
        "ambiguities",
        "notes",
        "repair_notes",
        "provenance",
    }

    assert expected_fields.issubset(set(ExtractionEnvelopeMetadata.model_fields.keys()))
