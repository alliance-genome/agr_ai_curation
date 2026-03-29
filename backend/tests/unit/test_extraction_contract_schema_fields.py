"""Tests for shared extractor contract schema fields and reason-code taxonomy."""

import json

from src.lib.openai_agents.models import GeneExpressionEnvelope as RuntimeGeneExpressionEnvelope
from src.schemas.models import GeneExpressionEnvelope as CoreGeneExpressionEnvelope
from src.schemas.models import PdfExtractionEnvelope
from src.schemas.models import PdfSpecialistEnvelope
from src.schemas.models.base import ExclusionReasonCode, ExclusionRecord, EvidenceRecord


def test_runtime_gene_expression_envelope_has_contract_defaults():
    envelope = RuntimeGeneExpressionEnvelope()

    assert envelope.items == []
    assert envelope.raw_mentions == []
    assert envelope.evidence_records == []
    assert envelope.normalization_notes == []
    assert envelope.exclusions == []
    assert envelope.ambiguities == []
    assert envelope.run_summary.candidate_count == 0


def test_runtime_gene_expression_envelope_accepts_reason_coded_exclusions():
    exclusion = ExclusionRecord(
        mention="marker transgene only",
        reason_code=ExclusionReasonCode.MARKER_ONLY_VISUALIZATION,
        evidence=[EvidenceRecord(verified_quote="marker used to visualize neurons only")],
    )

    envelope = RuntimeGeneExpressionEnvelope(exclusions=[exclusion])

    assert len(envelope.exclusions) == 1
    assert envelope.exclusions[0].reason_code == ExclusionReasonCode.MARKER_ONLY_VISUALIZATION


def test_runtime_gene_expression_schema_contains_reason_code_enum_values():
    schema_text = json.dumps(RuntimeGeneExpressionEnvelope.model_json_schema())

    assert "previously_reported" in schema_text
    assert "marker_only_visualization" in schema_text
    assert "insufficient_experimental_evidence" in schema_text


def test_core_schema_envelopes_include_shared_extractor_contract_fields():
    expected_fields = {
        "items",
        "raw_mentions",
        "normalization_notes",
        "exclusions",
        "ambiguities",
        "run_summary",
    }

    assert expected_fields.issubset(set(CoreGeneExpressionEnvelope.model_fields.keys()))
    assert expected_fields.issubset(set(PdfSpecialistEnvelope.model_fields.keys()))
    assert expected_fields.issubset(set(PdfExtractionEnvelope.model_fields.keys()))
