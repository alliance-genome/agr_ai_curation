"""Unit tests for shared domain-envelope extraction schemas."""

import pytest
from pydantic import ValidationError

from src.lib.openai_agents.models import (
    AlleleExtractionResultEnvelope,
    ChemicalExtractionResultEnvelope,
    DiseaseExtractionResultEnvelope,
    GeneExpressionEnvelope,
    GeneExtractionResultEnvelope,
    PhenotypeResultEnvelope,
)
from src.schemas.domain_envelope import DefinitionState, EnvelopeMetadataRef
from src.schemas.models import LEGACY_SEMANTIC_LIST_FIELDS


EXTRACTOR_ENVELOPE_CLASSES = (
    GeneExpressionEnvelope,
    GeneExtractionResultEnvelope,
    AlleleExtractionResultEnvelope,
    DiseaseExtractionResultEnvelope,
    ChemicalExtractionResultEnvelope,
    PhenotypeResultEnvelope,
)


def _valid_domain_envelope_payload() -> dict[str, object]:
    return {
        "summary": "Retained one gene object with verified evidence.",
        "curatable_objects": [
            {
                "object_type": "gene",
                "object_role": "curatable_unit",
                "pending_ref_id": "gene-crb-1",
                "schema_ref": {
                    "schema_id": "alliance.example.gene",
                    "provider": "domain-pack",
                    "definition_state": "draft",
                    "definition_notes": ["Pinned by the domain pack for this run."],
                },
                "model_ref": "alliance.gene.v1",
                "definition_state": "draft",
                "definition_notes": ["Payload fields are domain-pack owned."],
                "payload": {
                    "mention": "crb",
                    "normalized_symbol": "crb",
                    "normalized_id": "FB:FBgn0000368",
                },
                "field_refs": [
                    {
                        "object_ref": {
                            "pending_ref_id": "gene-crb-1",
                            "object_type": "gene",
                        },
                        "field_path": "normalized_id",
                    }
                ],
                "evidence_record_ids": ["evidence-crb"],
                "metadata_refs": [
                    {
                        "metadata_path": "raw_mentions[0]",
                        "role": "source_mention",
                    },
                    {
                        "metadata_path": "evidence_records[0]",
                        "role": "verified_evidence",
                    },
                ],
                "repair_hints": ["Preserve pending_ref_id when repairing normalization."],
                "metadata": {
                    "provider_refs": {
                        "agent": "gene_extractor",
                        "model": "gpt-5.4-mini",
                    }
                },
            }
        ],
        "metadata": {
            "raw_mentions": [
                {
                    "mention": "crb",
                    "entity_type": "gene",
                    "evidence_record_ids": ["evidence-crb"],
                }
            ],
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-crb",
                    "entity": "crb",
                    "verified_quote": "crb was analyzed in the eye disc.",
                    "page": 4,
                    "section": "Results",
                    "chunk_id": "chunk-1",
                }
            ],
            "normalization_notes": ["Normalized by explicit paper symbol."],
            "exclusions": [
                {
                    "mention": "Crb family",
                    "reason_code": "gene_family_not_individual",
                    "evidence_record_ids": ["evidence-crb"],
                }
            ],
            "ambiguities": [
                {
                    "mention": "crb-like",
                    "why_ambiguous": "The paper does not resolve the exact gene.",
                    "recommended_followup": "Ask curator to inspect the source figure.",
                    "evidence_record_ids": ["evidence-crb"],
                }
            ],
            "notes": ["Curator-facing note."],
            "repair_notes": ["Repair should not invent new object IDs."],
            "provenance": {"legacy_mentions": ["crb"]},
        },
        "run_summary": {
            "candidate_count": 2,
            "kept_count": 1,
            "excluded_count": 1,
            "ambiguous_count": 1,
            "warnings": ["One ambiguous mention preserved as metadata."],
        },
        "schema_ref": {
            "schema_id": "domain-envelope-extraction",
            "provider": "agr_ai_curation",
        },
        "repair_mode": True,
    }


@pytest.mark.parametrize("envelope_cls", EXTRACTOR_ENVELOPE_CLASSES)
def test_first_pass_extractor_envelopes_accept_shared_curatable_objects_contract(envelope_cls):
    envelope = envelope_cls.model_validate(_valid_domain_envelope_payload())

    assert envelope.curatable_objects[0].pending_ref_id == "gene-crb-1"
    assert envelope.curatable_objects[0].object_role == "curatable_unit"
    assert envelope.curatable_objects[0].object_type == "gene"
    assert envelope.curatable_objects[0].model_ref == "alliance.gene.v1"
    assert envelope.curatable_objects[0].definition_state == DefinitionState.DRAFT
    assert envelope.curatable_objects[0].field_refs[0].field_path == "normalized_id"
    assert envelope.curatable_objects[0].evidence_record_ids == ["evidence-crb"]
    assert envelope.curatable_objects[0].metadata_refs[0].metadata_path == "raw_mentions[0]"
    assert envelope.curatable_objects[0].repair_hints == [
        "Preserve pending_ref_id when repairing normalization."
    ]
    assert envelope.metadata.evidence_records[0].evidence_record_id == "evidence-crb"
    assert envelope.metadata.exclusions[0].reason_code == "gene_family_not_individual"
    assert envelope.repair_mode is True


@pytest.mark.parametrize("envelope_cls", EXTRACTOR_ENVELOPE_CLASSES)
@pytest.mark.parametrize("legacy_field", sorted(LEGACY_SEMANTIC_LIST_FIELDS))
def test_first_pass_extractor_envelopes_reject_legacy_semantic_lists(
    envelope_cls,
    legacy_field: str,
):
    payload = _valid_domain_envelope_payload()
    payload[legacy_field] = []

    with pytest.raises(ValidationError) as exc_info:
        envelope_cls.model_validate(payload)

    assert "curatable_objects[]" in str(exc_info.value)
    assert legacy_field in str(exc_info.value)


@pytest.mark.parametrize("envelope_cls", EXTRACTOR_ENVELOPE_CLASSES)
def test_first_pass_extractor_envelopes_reject_top_level_evidence_records(envelope_cls):
    payload = _valid_domain_envelope_payload()
    payload["evidence_records"] = []

    with pytest.raises(ValidationError) as exc_info:
        envelope_cls.model_validate(payload)

    assert any(
        error["loc"] == ("evidence_records",)
        and error["type"] == "extra_forbidden"
        for error in exc_info.value.errors()
    )


def test_domain_envelope_extraction_schema_has_no_top_level_legacy_lists():
    schema_properties = GeneExtractionResultEnvelope.model_json_schema()["properties"]

    assert "curatable_objects" in schema_properties
    assert "metadata" in schema_properties
    assert not LEGACY_SEMANTIC_LIST_FIELDS.intersection(schema_properties)


def test_metadata_refs_reject_absolute_or_empty_paths():
    with pytest.raises(ValidationError) as exc_info:
        EnvelopeMetadataRef(metadata_path="$.raw_mentions[0]")

    assert any(error["loc"] == ("metadata_path",) for error in exc_info.value.errors())
