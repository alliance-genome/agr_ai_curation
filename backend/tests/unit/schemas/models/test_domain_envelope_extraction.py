"""Unit tests for shared domain-envelope extraction schemas."""

import pytest
from pydantic import ValidationError

from src.lib.openai_agents.models import (
    AlleleExtractionResultEnvelope,
    AlleleExtractorRepairResponse,
    ChemicalExtractionResultEnvelope,
    ChemicalExtractorRepairResponse,
    DiseaseExtractionResultEnvelope,
    DiseaseExtractorRepairResponse,
    GeneExpressionEnvelope,
    GeneExpressionExtractorRepairResponse,
    GeneExtractionResultEnvelope,
    GeneExtractorRepairResponse,
    PhenotypeResultEnvelope,
    PhenotypeExtractorRepairResponse,
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

EXTRACTOR_REPAIR_RESPONSE_CLASSES = (
    GeneExpressionExtractorRepairResponse,
    GeneExtractorRepairResponse,
    AlleleExtractorRepairResponse,
    DiseaseExtractorRepairResponse,
    ChemicalExtractorRepairResponse,
    PhenotypeExtractorRepairResponse,
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
            "repair_notes": ["Repair should not invent new object IDs."],
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
        "repair_mode": True,
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
    assert envelope.curatable_objects[0].repair_hints == [
        "Preserve pending_ref_id when repairing normalization."
    ]
    assert envelope.metadata.evidence_records[0].evidence_record_id == "evidence-alpha"
    assert envelope.metadata.exclusions[0].reason_code == "unsupported_entity_type"
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


@pytest.mark.parametrize("response_cls", EXTRACTOR_REPAIR_RESPONSE_CLASSES)
def test_repair_response_schemas_accept_first_pass_envelope_branch(response_cls):
    response = response_cls.model_validate(_valid_domain_envelope_payload())

    assert response.root.curatable_objects[0].pending_ref_id == "object-alpha-1"


@pytest.mark.parametrize("response_cls", EXTRACTOR_REPAIR_RESPONSE_CLASSES)
def test_repair_response_schemas_accept_extractor_patch_branch(response_cls):
    response = response_cls.model_validate(
        {
            "repair_action": "extractor_patch",
            "patch_id": "repair-patch:test",
            "envelope_id": "env-1",
            "expected_revision": 2,
            "source_finding_ids": ["validation:1"],
            "operations": [
                {
                    "op": "replace",
                    "object_ref": {
                        "pending_ref_id": "object-alpha-1",
                        "object_type": "example_object",
                    },
                    "field_path": "normalized_id",
                    "expected_before": "example-object-0001",
                    "after": "example-object-0002",
                    "reason": "Validator supplied a grounded replacement.",
                }
            ],
            "rationale": "Bounded field-path repair.",
        }
    )

    assert response.model_dump()["repair_action"] == "extractor_patch"


@pytest.mark.parametrize("response_cls", EXTRACTOR_REPAIR_RESPONSE_CLASSES)
def test_repair_response_schemas_accept_no_repair_possible_branch(response_cls):
    response = response_cls.model_validate(
        {
            "repair_action": "no_repair_possible",
            "envelope_id": "env-1",
            "expected_revision": 2,
            "status": "no_repair_possible",
            "reason": "Available evidence cannot repair the requested field.",
            "finding_ids": ["validation:1"],
            "object_ref": {
                "pending_ref_id": "object-alpha-1",
                "object_type": "example_object",
            },
            "field_path": "normalized_id",
        }
    )

    assert response.model_dump()["repair_action"] == "no_repair_possible"


def test_metadata_refs_reject_absolute_or_empty_paths():
    with pytest.raises(ValidationError) as exc_info:
        EnvelopeMetadataRef(metadata_path="$.raw_mentions[0]")

    assert any(error["loc"] == ("metadata_path",) for error in exc_info.value.errors())
