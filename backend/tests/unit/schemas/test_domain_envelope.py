"""Unit tests for provider-agnostic domain envelope contracts."""

import pytest
from pydantic import ValidationError

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    DomainEnvelope,
    FieldRef,
    HistoryEvent,
    HistoryEventKind,
    ObjectRef,
    SchemaRef,
    ValidationFinding,
    ValidationFindingSeverity,
    parse_field_path,
)


def _pending_gene_object() -> CuratableObjectEnvelope:
    return CuratableObjectEnvelope(
        object_type="GeneAssertion",
        pending_ref_id="pending-gene-1",
        payload={
            "gene": {
                "symbol": "abc-1",
                "identifiers": [{"curie": "AGR:0001"}],
            },
            "evidence": [{"snippet": "abc-1 is expressed in neurons"}],
        },
    )


def test_envelope_validates_pending_object_and_field_refs_after_extraction():
    envelope = DomainEnvelope(
        envelope_id="env-1",
        domain_pack_id="fixture.core",
        objects=[_pending_gene_object()],
        validation_findings=[
            ValidationFinding(
                severity=ValidationFindingSeverity.WARNING,
                message="Identifier should be checked by a downstream resolver",
                field_ref=FieldRef(
                    object_ref=ObjectRef(pending_ref_id="pending-gene-1"),
                    field_path="gene.identifiers[0].curie",
                ),
            )
        ],
        history=[
            HistoryEvent(
                event_type=HistoryEventKind.OBJECT_EXTRACTED,
                object_ref=ObjectRef(pending_ref_id="pending-gene-1"),
            )
        ],
    )

    assert (
        envelope.validation_findings[0].field_ref.field_path
        == "gene.identifiers[0].curie"
    )
    assert envelope.history[0].object_ref.pending_ref_id == "pending-gene-1"


def test_curatable_object_can_produce_canonical_object_ref():
    pending = _pending_gene_object()
    stable = CuratableObjectEnvelope(
        object_type="GeneAssertion",
        object_id="WB:WBGene00000001",
        pending_ref_id="pending-gene-1",
        payload={},
    )

    assert pending.to_object_ref() == ObjectRef(
        pending_ref_id="pending-gene-1",
        object_type="GeneAssertion",
    )
    assert stable.to_object_ref() == ObjectRef(
        object_id="WB:WBGene00000001",
        object_type="GeneAssertion",
    )


def test_envelope_schema_provider_accepts_non_linkml_json_schema_refs():
    envelope_schema = SchemaRef(
        schema_id="museum-envelope.schema.json",
        provider="json-schema",
        name="Museum catalog envelope",
        version="draft-2020-12",
        uri="https://schemas.example.test/museum/envelope.schema.json",
        metadata={"dialect": "https://json-schema.org/draft/2020-12/schema"},
    )
    artifact_schema = SchemaRef(
        schema_id="artifact.schema.json",
        provider="json-schema",
        name="Artifact payload",
        version="draft-2020-12",
        uri="https://schemas.example.test/museum/artifact.schema.json",
    )

    envelope = DomainEnvelope(
        envelope_id="museum-env-1",
        domain_pack_id="museum.catalog",
        schema_ref=envelope_schema,
        objects=[
            CuratableObjectEnvelope(
                object_type="MuseumArtifact",
                pending_ref_id="artifact-1",
                schema_ref=artifact_schema,
                payload={"artifact": {"accession_id": "MC-2026-0001"}},
            )
        ],
    )

    assert envelope.schema_ref.provider == "json-schema"
    assert envelope.objects[0].schema_ref.schema_id == "artifact.schema.json"
    assert envelope.objects[0].object_type == "MuseumArtifact"


def test_envelope_rejects_unknown_pending_refs():
    with pytest.raises(ValidationError) as exc_info:
        DomainEnvelope(
            envelope_id="env-1",
            domain_pack_id="fixture.core",
            objects=[_pending_gene_object()],
            validation_findings=[
                ValidationFinding(
                    severity=ValidationFindingSeverity.ERROR,
                    message="Unknown object",
                    object_ref=ObjectRef(pending_ref_id="missing-object"),
                )
            ],
        )

    assert "unknown pending_ref_id 'missing-object'" in str(exc_info.value)


def test_validation_finding_field_refs_can_target_missing_fields():
    envelope = DomainEnvelope(
        envelope_id="env-1",
        domain_pack_id="fixture.core",
        objects=[_pending_gene_object()],
        validation_findings=[
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                message="Missing field",
                field_ref=FieldRef(
                    object_ref=ObjectRef(pending_ref_id="pending-gene-1"),
                    field_path="gene.missing",
                ),
            )
        ],
    )

    assert envelope.validation_findings[0].field_ref.field_path == "gene.missing"


def test_object_field_refs_reject_missing_payload_paths():
    with pytest.raises(ValidationError) as exc_info:
        DomainEnvelope(
            envelope_id="env-1",
            domain_pack_id="fixture.core",
            objects=[
                _pending_gene_object().model_copy(
                    update={
                        "field_refs": [
                            FieldRef(
                                object_ref=ObjectRef(pending_ref_id="pending-gene-1"),
                                field_path="gene.missing",
                            )
                        ]
                    }
                )
            ],
        )

    assert "field_path 'gene.missing' does not exist" in str(exc_info.value)


def test_field_path_syntax_rejects_absolute_or_empty_segments():
    invalid_paths = ["$gene.symbol", ".gene.symbol", "gene..symbol", "gene[abc]"]

    for invalid_path in invalid_paths:
        with pytest.raises(ValueError):
            parse_field_path(invalid_path)


def test_object_envelope_carries_definition_state_and_notes():
    obj = CuratableObjectEnvelope(
        object_type="PrototypeAssertion",
        pending_ref_id="pending-prototype-1",
        payload={"prototype": {"value": "under review"}},
        definition_state=DefinitionState.IN_DEVELOPMENT,
        definition_notes=["Initial contract for a domain pack still under design."],
    )

    assert obj.definition_state is DefinitionState.IN_DEVELOPMENT
    assert obj.definition_notes == [
        "Initial contract for a domain pack still under design."
    ]
