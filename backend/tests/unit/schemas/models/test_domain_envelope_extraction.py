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
        "summary": "Retained one example object with verified evidence.",
        "curatable_objects": [
            {
                "object_type": "example_object",
                "object_role": "curatable_unit",
                "pending_ref_id": "object-alpha-1",
                "schema_ref": {
                    "schema_id": "example.domain.object",
                    "provider": "domain-pack",
                    "definition_state": "draft",
                    "definition_notes": ["Pinned by the domain pack for this run."],
                },
                "model_ref": "example.object.v1",
                "definition_state": "draft",
                "definition_notes": ["Payload fields are domain-pack owned."],
                "payload": {
                    "mention": "alpha",
                    "normalized_label": "alpha",
                    "normalized_id": "example-object-0001",
                },
                "field_refs": [
                    {
                        "object_ref": {
                            "pending_ref_id": "object-alpha-1",
                            "object_type": "example_object",
                        },
                        "field_path": "normalized_id",
                    }
                ],
                "evidence_record_ids": ["evidence-alpha"],
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
                "metadata": {
                    "provider_refs": {
                        "agent": "gene_extractor",
                        "model": "gpt-5-mini",
                    }
                },
            }
        ],
        "metadata": {
            "raw_mentions": [
                {
                    "mention": "alpha",
                    "entity_type": "example_object",
                    "evidence_record_ids": ["evidence-alpha"],
                }
            ],
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-alpha",
                    "entity": "alpha",
                    "verified_quote": "alpha was analyzed in the source passage.",
                    "page": 4,
                    "section": "Results",
                    "chunk_id": "chunk-1",
                }
            ],
            "normalization_notes": ["Normalized by explicit paper symbol."],
            "exclusions": [
                {
                    "mention": "Alpha collection",
                    "reason_code": "unsupported_entity_type",
                    "evidence_record_ids": ["evidence-alpha"],
                }
            ],
            "ambiguities": [
                {
                    "mention": "alpha-like",
                    "why_ambiguous": "The source does not resolve the exact object.",
                    "recommended_followup": "Ask curator to inspect the source figure.",
                    "evidence_record_ids": ["evidence-alpha"],
                }
            ],
            "notes": ["Curator-facing note."],
            "provenance": {"legacy_mentions": ["alpha"]},
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
    }


@pytest.mark.parametrize("envelope_cls", EXTRACTOR_ENVELOPE_CLASSES)
def test_first_pass_extractor_envelopes_accept_shared_curatable_objects_contract(envelope_cls):
    envelope = envelope_cls.model_validate(_valid_domain_envelope_payload())

    assert envelope.curatable_objects[0].pending_ref_id == "object-alpha-1"
    assert envelope.curatable_objects[0].object_role == "curatable_unit"
    assert envelope.curatable_objects[0].object_type == "example_object"
    assert envelope.curatable_objects[0].model_ref == "example.object.v1"
    assert envelope.curatable_objects[0].definition_state == DefinitionState.DRAFT
    assert envelope.curatable_objects[0].field_refs[0].field_path == "normalized_id"
    assert envelope.curatable_objects[0].evidence_record_ids == ["evidence-alpha"]
    assert envelope.curatable_objects[0].metadata_refs[0].metadata_path == "raw_mentions[0]"
    assert envelope.metadata.evidence_records[0].evidence_record_id == "evidence-alpha"
    assert envelope.metadata.exclusions[0].reason_code == "unsupported_entity_type"


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


def test_first_pass_extractor_evidence_records_accept_workspace_attachment_metadata():
    payload = _valid_domain_envelope_payload()
    payload["metadata"]["evidence_records"][0].update(
        {
            "pending_ref_id": "object-alpha-1",
            "object_ref": {"pending_ref_id": "object-alpha-1"},
            "field_path": "normalized_id",
            "field_paths": ["normalized_id"],
            "agent_note": "Supports the normalized identifier field.",
        }
    )

    envelope = GeneExpressionEnvelope.model_validate(payload)
    evidence_record = envelope.metadata.evidence_records[0]

    assert evidence_record.pending_ref_id == "object-alpha-1"
    assert evidence_record.object_ref is not None
    assert evidence_record.object_ref.pending_ref_id == "object-alpha-1"
    assert evidence_record.field_paths == ["normalized_id"]


def test_domain_envelope_extraction_schema_has_no_top_level_legacy_lists():
    schema_properties = GeneExtractionResultEnvelope.model_json_schema()["properties"]

    assert "curatable_objects" in schema_properties
    assert "metadata" in schema_properties
    assert not LEGACY_SEMANTIC_LIST_FIELDS.intersection(schema_properties)


@pytest.mark.parametrize("envelope_cls", EXTRACTOR_ENVELOPE_CLASSES)
@pytest.mark.parametrize(
    ("location", "field_name", "value"),
    (
        ("object", "repair_hints", ["legacy repair hint"]),
        ("metadata", "repair_notes", ["legacy repair note"]),
        ("top_level", "repair_mode", True),
    ),
)
def test_first_pass_extractor_envelopes_reject_repair_surfaces(
    envelope_cls,
    location: str,
    field_name: str,
    value: object,
):
    payload = _valid_domain_envelope_payload()
    if location == "object":
        payload["curatable_objects"][0][field_name] = value
    elif location == "metadata":
        payload["metadata"][field_name] = value
    else:
        payload[field_name] = value

    with pytest.raises(ValidationError) as exc_info:
        envelope_cls.model_validate(payload)

    assert any(error["loc"][-1] == field_name for error in exc_info.value.errors())


def test_metadata_refs_reject_absolute_or_empty_paths():
    with pytest.raises(ValidationError) as exc_info:
        EnvelopeMetadataRef(metadata_path="$.raw_mentions[0]")

    assert any(error["loc"] == ("metadata_path",) for error in exc_info.value.errors())
