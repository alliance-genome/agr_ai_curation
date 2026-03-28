"""Unit tests for shared extractor evidence schema contracts."""

import json

import pytest
from pydantic import ValidationError

from src.lib.openai_agents.models import GeneExtractionResultEnvelope
from src.schemas.models.base import EvidenceRecord


def _tool_evidence_payload() -> dict[str, object]:
    return {
        "entity": "crumb",
        "verified_quote": "Crumb is essential for maintaining epithelial polarity in the embryo.",
        "page": 4,
        "section": "Results",
        "subsection": "Gene Expression Analysis",
        "chunk_id": "abc123",
        "figure_reference": "Figure 2A",
    }


def test_evidence_record_defaults_optional_fields_to_none_when_quote_present():
    evidence = EvidenceRecord(verified_quote="Crumb is essential for epithelial polarity.")

    assert evidence.entity is None
    assert evidence.verified_quote == "Crumb is essential for epithelial polarity."
    assert evidence.page is None
    assert evidence.section is None
    assert evidence.subsection is None
    assert evidence.chunk_id is None
    assert evidence.figure_reference is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"section": "Results"},
        {"page": 4, "section": "Results"},
        {"verified_quote": "   "},
    ],
)
def test_evidence_record_requires_non_empty_verified_quote(payload):
    with pytest.raises(ValidationError, match="verified_quote"):
        EvidenceRecord.model_validate(payload)


def test_evidence_record_rejects_legacy_and_unverified_fields():
    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(
            {
                "snippet": "Legacy section summary.",
                "not_found": True,
                "match_score": 0.42,
            }
        )


def test_runtime_gene_extraction_envelope_round_trips_verified_evidence_json():
    evidence_payload = _tool_evidence_payload()
    envelope = GeneExtractionResultEnvelope.model_validate(
        {
            "summary": "crumb is a focal gene in this paper",
            "genes": [
                {
                    "mention": "crumb",
                    "normalized_symbol": "crumb",
                    "evidence": [evidence_payload],
                }
            ],
            "evidence_records": [evidence_payload],
        }
    )

    dumped = envelope.model_dump(mode="json")
    round_tripped = GeneExtractionResultEnvelope.model_validate_json(envelope.model_dump_json())

    assert dumped["genes"][0]["evidence"][0] == evidence_payload
    assert dumped["evidence_records"][0] == evidence_payload
    assert round_tripped.model_dump(mode="json") == dumped


def test_evidence_record_schema_serialization_preserves_all_fields():
    evidence_payload = _tool_evidence_payload()

    encoded = EvidenceRecord(**evidence_payload).model_dump_json()
    decoded = json.loads(encoded)

    assert decoded == evidence_payload


def test_evidence_record_normalizes_blank_optional_strings_to_none():
    evidence = EvidenceRecord(
        verified_quote="Quoted support.",
        entity="  crumb  ",
        section="  Results  ",
        subsection="   ",
        chunk_id="  abc123  ",
        figure_reference="  Table 1  ",
    )

    assert evidence.entity == "crumb"
    assert evidence.section == "Results"
    assert evidence.subsection is None
    assert evidence.chunk_id == "abc123"
    assert evidence.figure_reference == "Table 1"
