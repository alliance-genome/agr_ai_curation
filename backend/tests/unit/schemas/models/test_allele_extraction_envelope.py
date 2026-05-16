"""Unit tests for the Alliance allele extractor domain-envelope schema."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.lib.config import schema_discovery


REPO_ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture(autouse=True)
def _reset_schema_discovery():
    schema_discovery.reset_cache()
    yield
    schema_discovery.reset_cache()


@pytest.fixture
def allele_schema(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_ROOT / "packages"))
    schema_discovery.discover_agent_schemas(force_reload=True)
    schema = schema_discovery.get_schema_for_agent("allele_extractor")
    assert schema is not None
    return schema


def _validate_allele_envelope(allele_schema, payload: dict):
    return allele_schema.model_validate(payload)


def _valid_allele_envelope_payload() -> dict:
    return {
        "summary": "Retained daf-2(m41) with verified allele evidence.",
        "curatable_objects": [
            {
                "object_type": "Reference",
                "object_role": "validated_reference",
                "pending_ref_id": "paper-reference-1",
                "model_ref": "ReferencePayload",
                "definition_state": "in_development",
                "payload": {
                    "title": "daf-2 allele evidence fixture paper",
                    "filename": "test_tool_verified_allele_paper.pdf",
                },
            },
            {
                "object_type": "AlleleMention",
                "object_role": "metadata_only",
                "pending_ref_id": "allele-mention-1",
                "model_ref": "AlleleMentionPayload",
                "definition_state": "in_development",
                "payload": {
                    "mention": {
                        "text": "daf-2(m41)",
                        "normalized_hint": "WB:WBVar00000001",
                    },
                    "associated_gene": {"symbol": "daf-2"},
                    "taxon": {"curie": "NCBITaxon:6239"},
                    "source_mentions": ["daf-2(m41)"],
                },
            },
            {
                "object_type": "EvidenceQuote",
                "object_role": "metadata_only",
                "pending_ref_id": "evidence-quote-1",
                "model_ref": "EvidenceQuotePayload",
                "definition_state": "in_development",
                "payload": {
                    "evidence_record_id": "daf-2-m41-evidence-1",
                    "entity": "daf-2(m41)",
                    "verified_quote": "daf-2(m41) animals formed dauer larvae at 25 C.",
                    "page": 3,
                    "section": "Results",
                    "subsection": "Dauer phenotype",
                    "chunk_id": "chunk-allele-phenotype",
                    "figure_reference": "Figure 3B",
                },
            },
            {
                "object_type": "AllelePaperEvidenceAssociation",
                "object_role": "curatable_unit",
                "pending_ref_id": "allele-paper-evidence-association-1",
                "model_ref": "AllelePaperEvidenceAssociationPayload",
                "definition_state": "in_development",
                "definition_notes": [
                    "Pending only; write behavior is blocked until write targets are verified."
                ],
                "payload": {
                    "association_kind": "allele_paper_evidence",
                    "allele_label": "daf-2(m41)",
                    "associated_gene": "daf-2",
                    "confidence": "high",
                    "evidence_record_ids": ["daf-2-m41-evidence-1"],
                },
                "object_refs": [
                    {"pending_ref_id": "paper-reference-1", "object_type": "Reference"},
                    {"pending_ref_id": "allele-mention-1", "object_type": "AlleleMention"},
                    {"pending_ref_id": "evidence-quote-1", "object_type": "EvidenceQuote"},
                ],
                "evidence_record_ids": ["daf-2-m41-evidence-1"],
                "metadata_refs": [
                    {"metadata_path": "raw_mentions[0]", "role": "source_mention"},
                    {"metadata_path": "evidence_records[0]", "role": "verified_evidence"},
                ],
                "metadata": {
                    "write_behavior": {
                        "status": "blocked",
                        "reason": "Reference materialization is not verified.",
                    }
                },
            },
        ],
        "metadata": {
            "raw_mentions": [
                {
                    "mention": "daf-2(m41)",
                    "entity_type": "allele",
                    "evidence_record_ids": ["daf-2-m41-evidence-1"],
                },
                {"mention": "daf-2(e1368)", "entity_type": "allele"},
            ],
            "evidence_records": [
                {
                    "evidence_record_id": "daf-2-m41-evidence-1",
                    "entity": "daf-2(m41)",
                    "verified_quote": "daf-2(m41) animals formed dauer larvae at 25 C.",
                    "page": 3,
                    "section": "Results",
                    "subsection": "Dauer phenotype",
                    "chunk_id": "chunk-allele-phenotype",
                    "figure_reference": "Figure 3B",
                }
            ],
            "normalization_notes": [
                "Resolved daf-2(m41) to WB:WBVar00000001 with agr_curation_query."
            ],
            "exclusions": [
                {
                    "mention": "daf-2(e1368)",
                    "reason_code": "background_genotype_only",
                    "details": "Only maintained as strain background.",
                }
            ],
            "ambiguities": [
                {
                    "mention": "daf-2(mx)",
                    "why_ambiguous": "The paper does not resolve the exact allele code.",
                    "recommended_followup": "Curator should inspect the strain table.",
                }
            ],
            "notes": ["Association write behavior remains blocked."],
            "provenance": {"semantic_source": "curatable_objects"},
        },
        "run_summary": {
            "candidate_count": 3,
            "kept_count": 1,
            "excluded_count": 1,
            "ambiguous_count": 1,
        },
    }


def test_allele_extractor_schema_accepts_domain_pack_objects_and_metadata(allele_schema):
    envelope = _validate_allele_envelope(
        allele_schema,
        _valid_allele_envelope_payload(),
    )

    association = envelope.curatable_objects[-1]
    assert association.object_type == "AllelePaperEvidenceAssociation"
    assert association.evidence_record_ids == ["daf-2-m41-evidence-1"]
    assert envelope.metadata.exclusions[0].reason_code == "background_genotype_only"
    assert envelope.metadata.ambiguities[0].mention == "daf-2(mx)"


def test_allele_extractor_schema_rejects_legacy_semantic_lists(allele_schema):
    payload = _valid_allele_envelope_payload()
    payload["alleles"] = []

    with pytest.raises(ValidationError) as exc_info:
        allele_schema.model_validate(payload)

    assert "curatable_objects[]" in str(exc_info.value)
    assert "alleles" in str(exc_info.value)


def test_allele_extractor_schema_rejects_unblocked_association_writes(allele_schema):
    payload = _valid_allele_envelope_payload()
    payload["curatable_objects"][-1]["metadata"]["write_behavior"]["status"] = "ready"

    with pytest.raises(ValidationError) as exc_info:
        allele_schema.model_validate(payload)

    assert "metadata.write_behavior.status" in str(exc_info.value)
    assert "blocked" in str(exc_info.value)


def test_allele_extractor_schema_rejects_evidence_ids_missing_from_metadata(allele_schema):
    payload = _valid_allele_envelope_payload()
    payload["metadata"]["evidence_records"] = []

    with pytest.raises(ValidationError) as exc_info:
        allele_schema.model_validate(payload)

    assert "metadata.evidence_records[]" in str(exc_info.value)
    assert "daf-2-m41-evidence-1" in str(exc_info.value)


def test_allele_extractor_schema_rejects_extractor_owned_allele_identity(allele_schema):
    payload = _valid_allele_envelope_payload()
    payload["curatable_objects"][-1]["payload"]["allele_identifier"] = (
        "WB:WBVar00000001"
    )

    with pytest.raises(ValidationError) as exc_info:
        allele_schema.model_validate(payload)

    assert "payload.allele_identifier" in str(exc_info.value)
    assert "active allele validator" in str(exc_info.value)


def test_allele_extractor_schema_rejects_pre_validation_allele_objects(allele_schema):
    payload = _valid_allele_envelope_payload()
    payload["curatable_objects"].insert(
        2,
        {
            "object_type": "Allele",
            "object_role": "validated_reference",
            "pending_ref_id": "allele-reference-1",
            "model_ref": "AllelePayload",
            "definition_state": "in_development",
            "payload": {},
        },
    )

    with pytest.raises(ValidationError) as exc_info:
        allele_schema.model_validate(payload)

    assert "validator-materialized object types" in str(exc_info.value)
    assert "Allele" in str(exc_info.value)
    assert "Active allele validation" in str(exc_info.value)


def test_allele_extractor_schema_rejects_pre_validation_allele_object_refs(
    allele_schema,
):
    payload = _valid_allele_envelope_payload()
    payload["curatable_objects"][-1]["object_refs"].append(
        {"pending_ref_id": "allele-reference-1", "object_type": "Allele"}
    )

    with pytest.raises(ValidationError) as exc_info:
        allele_schema.model_validate(payload)

    assert "object_refs[]" in str(exc_info.value)
    assert "validator-materialized object types" in str(exc_info.value)
    assert "Allele" in str(exc_info.value)


@pytest.mark.parametrize(
    ("location", "field_name", "value"),
    (
        ("object", "repair_hints", ["legacy repair hint"]),
        ("metadata", "repair_notes", ["legacy repair note"]),
        ("top_level", "repair_mode", True),
    ),
)
def test_allele_extractor_schema_rejects_repair_surfaces(
    allele_schema,
    location: str,
    field_name: str,
    value: object,
):
    payload = deepcopy(_valid_allele_envelope_payload())
    if location == "object":
        payload["curatable_objects"][-1][field_name] = value
    elif location == "metadata":
        payload["metadata"][field_name] = value
    else:
        payload[field_name] = value

    with pytest.raises(ValidationError):
        allele_schema.model_validate(payload)
