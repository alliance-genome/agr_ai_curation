"""Unit tests for shared extractor evidence schema contracts."""

import json

import pytest
from pydantic import ValidationError

from src.lib.openai_agents.models import GeneExtractionResultEnvelope
from src.schemas.models.base import EvidenceRecord


def _tool_evidence_payload() -> dict[str, object]:
    return {
        "evidence_record_id": "evidence-crumb-1",
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

    assert evidence.evidence_record_id is None
    assert evidence.entity is None
    assert evidence.verified_quote == "Crumb is essential for epithelial polarity."
    assert evidence.page is None
    assert evidence.section is None
    assert evidence.subsection is None
    assert evidence.chunk_id is None
    assert evidence.figure_reference is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, {}),
        ({"section": "Results"}, {"section": "Results"}),
        ({"page": 4, "section": "Results"}, {"page": 4, "section": "Results"}),
        ({"verified_quote": "   "}, {}),
    ],
)
def test_evidence_record_accepts_partial_payloads_during_migration(payload, expected):
    evidence = EvidenceRecord.model_validate(payload)

    assert evidence.model_dump(exclude_none=True) == expected


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
                    "evidence_record_ids": [evidence_payload["evidence_record_id"]],
                }
            ],
            "evidence_records": [evidence_payload],
        }
    )

    dumped = envelope.model_dump(mode="json")
    round_tripped = GeneExtractionResultEnvelope.model_validate_json(envelope.model_dump_json())

    assert dumped["genes"][0]["evidence_record_ids"] == [evidence_payload["evidence_record_id"]]
    assert dumped["evidence_records"][0] == evidence_payload
    assert round_tripped.model_dump(mode="json") == dumped


def test_runtime_gene_extraction_envelope_accepts_partial_evidence_during_migration():
    envelope = GeneExtractionResultEnvelope.model_validate(
        {
            "genes": [
                {
                    "mention": "crumb",
                    "evidence_record_ids": ["evidence-1"],
                }
            ],
            "evidence_records": [{"page": 4, "section": "Results"}],
        }
    )

    assert envelope.genes[0].evidence_record_ids == ["evidence-1"]
    assert envelope.evidence_records[0].page == 4
    assert envelope.evidence_records[0].verified_quote is None


def test_evidence_record_schema_serialization_preserves_all_fields():
    evidence_payload = _tool_evidence_payload()

    encoded = EvidenceRecord(**evidence_payload).model_dump_json()
    decoded = json.loads(encoded)

    assert decoded == evidence_payload


@pytest.mark.parametrize("page", [0, -1])
def test_evidence_record_rejects_non_positive_page_numbers(page):
    with pytest.raises(ValidationError) as exc_info:
        EvidenceRecord.model_validate({"page": page})

    errors = exc_info.value.errors()

    assert any(error["loc"] == ("page",) for error in errors)


@pytest.mark.parametrize("page", [True, "1", 1.5])
def test_evidence_record_rejects_non_integer_page_values(page):
    with pytest.raises(ValidationError) as exc_info:
        EvidenceRecord.model_validate({"page": page})

    errors = exc_info.value.errors()

    assert any(error["loc"] == ("page",) for error in errors)


def test_evidence_record_normalizes_blank_optional_strings_to_none():
    evidence = EvidenceRecord(
        verified_quote="   ",
        entity="  crumb  ",
        section="  Results  ",
        subsection="   ",
        chunk_id="  abc123  ",
        figure_reference="  Table 1  ",
    )

    assert evidence.entity == "crumb"
    assert evidence.verified_quote is None
    assert evidence.section == "Results"
    assert evidence.subsection is None
    assert evidence.chunk_id == "abc123"
    assert evidence.figure_reference == "Table 1"


@pytest.mark.parametrize(
    ("payload", "field_name"),
    [
        ({"section": {"title": "Results"}}, "section"),
        ({"verified_quote": ["quoted text"]}, "verified_quote"),
        ({"entity": 123}, "entity"),
        ({"subsection": ("gene", "expression")}, "subsection"),
        ({"chunk_id": {"id": "abc123"}}, "chunk_id"),
        ({"figure_reference": ["Figure 2A"]}, "figure_reference"),
    ],
)
def test_evidence_record_rejects_non_string_optional_text_fields(payload, field_name):
    with pytest.raises(ValidationError) as exc_info:
        EvidenceRecord.model_validate(payload)

    errors = exc_info.value.errors()

    assert any(error["loc"] == (field_name,) for error in errors)


def test_runtime_gene_extraction_envelope_rejects_non_string_evidence_fields():
    with pytest.raises(ValidationError) as exc_info:
        GeneExtractionResultEnvelope.model_validate(
            {
                "genes": [
                    {
                        "mention": "crumb",
                        "evidence_record_ids": [123],
                    }
                ],
                "evidence_records": [{"verified_quote": ["quoted text"]}],
            }
        )

    errors = exc_info.value.errors()

    assert any(error["loc"] == ("genes", 0, "evidence_record_ids", 0) for error in errors)
    assert any(error["loc"] == ("evidence_records", 0, "verified_quote") for error in errors)


def test_runtime_gene_extraction_envelope_rejects_invalid_page_values():
    with pytest.raises(ValidationError) as exc_info:
        GeneExtractionResultEnvelope.model_validate(
            {
                "genes": [
                    {
                        "mention": "crumb",
                        "evidence_record_ids": ["evidence-1"],
                    }
                ],
                "evidence_records": [{"page": "1"}],
            }
        )

    errors = exc_info.value.errors()

    assert any(error["loc"] == ("evidence_records", 0, "page") for error in errors)
