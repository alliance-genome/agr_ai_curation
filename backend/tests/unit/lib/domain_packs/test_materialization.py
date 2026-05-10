"""Unit tests for provider-neutral domain-envelope review-row materialization."""

from __future__ import annotations

from src.lib.domain_packs.materialization import (
    REVIEW_ROW_PROJECTION_TYPE,
    DomainPackMetadataReviewRowMaterializer,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    DomainEnvelopeStatus,
    FieldRef,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)


def _metadata() -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.pack",
        display_name="Fixture Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="GeneAssertion",
                display_name="Gene assertion",
                metadata={
                    "object_role": "curatable_unit",
                    "workspace_display": {
                        "primary_label_field": "gene.symbol",
                        "secondary_label_field": "condition.label",
                        "summary_fields": [
                            "gene.symbol",
                            "condition.label",
                            "evidence[0].quote",
                        ],
                    },
                    "provider_refs": {"schema": "fixture"},
                },
                fields=[
                    DomainPackFieldDefinition(
                        field_path="gene.symbol",
                        field_type=DomainPackFieldType.STRING,
                        display_name="Gene symbol",
                        metadata={"provider_refs": {"slot": "gene_symbol"}},
                    ),
                    DomainPackFieldDefinition(
                        field_path="condition.label",
                        field_type=DomainPackFieldType.STRING,
                        display_name="Condition",
                    ),
                    DomainPackFieldDefinition(
                        field_path="evidence[0].quote",
                        field_type=DomainPackFieldType.STRING,
                        display_name="Evidence quote",
                    ),
                ],
            ),
            DomainPackObjectDefinition(
                object_type="EvidenceQuote",
                display_name="Evidence quote",
                metadata={"object_role": "metadata_only"},
            ),
        ],
    )


def test_metadata_materializer_regenerates_review_rows_from_envelope_objects():
    envelope = DomainEnvelope(
        envelope_id="env-1",
        domain_pack_id="fixture.pack",
        domain_pack_version="0.1.0",
        status=DomainEnvelopeStatus.EXTRACTED,
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                object_id="gene-1",
                status=CuratableObjectStatus.PENDING,
                payload={
                    "gene": {"symbol": "abc-1"},
                    "condition": {"label": "Condition A"},
                    "evidence": [{"quote": "abc-1 was observed."}],
                },
            ),
            CuratableObjectEnvelope(
                object_type="EvidenceQuote",
                object_id="quote-1",
                payload={"quote": "supporting metadata"},
            ),
        ],
        validation_findings=[
            ValidationFinding(
                severity=ValidationFindingSeverity.WARNING,
                message="Review symbol case.",
                field_ref=FieldRef(
                    object_ref=ObjectRef(object_id="gene-1"),
                    field_path="gene.symbol",
                ),
            )
        ],
    )

    rows = DomainPackMetadataReviewRowMaterializer(_metadata()).materialize(
        envelope,
        envelope_revision=3,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.envelope_id == "env-1"
    assert row.object_id == "gene-1"
    assert row.envelope_revision == 3
    assert row.object_type == "GeneAssertion"
    assert row.object_role == "curatable_unit"
    assert row.status == "pending"
    assert row.validation_state == "warning"
    assert row.projection_type == REVIEW_ROW_PROJECTION_TYPE
    assert row.projection_key == "gene-1"
    assert row.display_label == "abc-1"
    assert row.secondary_label == "Condition A"
    assert [field.field_path for field in row.summary_fields] == [
        "gene.symbol",
        "condition.label",
        "evidence[0].quote",
    ]
    assert row.summary_fields[0].metadata["provider_refs"]["slot"] == "gene_symbol"
    assert row.metadata["semantic_source"] == "domain_envelope.objects"
