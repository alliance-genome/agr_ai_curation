"""Unit tests for provider-neutral domain-envelope review-row materialization."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import (
    load_domain_fixture_pack,
    load_domain_pack_metadata,
)
from src.lib.domain_packs.materialization import (
    REVIEW_ROW_PROJECTION_TYPE,
    DomainEnvelopeMaterializationError,
    DomainPackMetadataReviewRowMaterializer,
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
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
    ValidationFindingStatus,
)
from src.schemas.domain_validator import DomainValidatorResultBase
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)


pytestmark = pytest.mark.provider_agnostic_domain_pack

PROVIDER_AGNOSTIC_PACK_PATH = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "domain_packs"
    / "provider_agnostic"
    / "museum.catalog"
)


def _metadata() -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.pack",
        display_name="Fixture Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        metadata={
            "validator_bindings": {
                "active": [],
                "under_development": [
                    {
                        "binding_id": "fixture.gene_symbol_lookup",
                        "display_name": "Gene symbol lookup",
                        "state_explanation": "Package-scoped lookup dispatch is pending.",
                        "applies_to": {
                            "domain_pack_id": "fixture.pack",
                            "object_types": ["GeneAssertion"],
                            "field_paths": ["gene.symbol"],
                        },
                    }
                ],
            }
        },
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


def _validator_metadata() -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.validator",
        display_name="Fixture Validator Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        metadata={
            "validator_bindings": {
                "active": [
                    {
                        "binding_id": "fixture.allele_lookup",
                        "display_name": "Allele lookup",
                        "validator_agent": {
                            "package_id": "fixture.validators",
                            "agent_id": "allele_validator",
                        },
                        "applies_to": {
                            "domain_pack_id": "fixture.validator",
                            "object_types": ["AlleleMention"],
                            "field_paths": ["mention.text"],
                        },
                        "input_fields": {
                            "mention": {
                                "source": "payload",
                                "path": "mention.text",
                            }
                        },
                        "expected_result_fields": {
                            "curie": "allele.primary_external_id",
                            "symbol": "allele.allele_symbol",
                            "taxon": "allele.taxon",
                        },
                    }
                ],
                "under_development": [],
            }
        },
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="AlleleMention",
                display_name="Allele mention",
                metadata={"object_role": "metadata_only"},
                fields=[
                    DomainPackFieldDefinition(
                        field_path="mention.text",
                        field_type=DomainPackFieldType.STRING,
                    )
                ],
            ),
            DomainPackObjectDefinition(
                object_type="Allele",
                display_name="Allele",
                metadata={"object_role": "validated_reference"},
                fields=[
                    DomainPackFieldDefinition(
                        field_path="primary_external_id",
                        field_type=DomainPackFieldType.STRING,
                        required=True,
                    ),
                    DomainPackFieldDefinition(
                        field_path="allele_symbol",
                        field_type=DomainPackFieldType.STRING,
                        required=True,
                    ),
                    DomainPackFieldDefinition(
                        field_path="taxon",
                        field_type=DomainPackFieldType.STRING,
                        required=True,
                    ),
                ],
            ),
            DomainPackObjectDefinition(
                object_type="AlleleCandidate",
                display_name="Allele candidate",
                metadata={"object_role": "metadata_only"},
                fields=[
                    DomainPackFieldDefinition(
                        field_path="primary_external_id",
                        field_type=DomainPackFieldType.STRING,
                    )
                ],
            ),
        ],
    )


def _validator_envelope() -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="validator-env",
        domain_pack_id="fixture.validator",
        objects=[
            CuratableObjectEnvelope(
                object_type="AlleleMention",
                object_id="allele-mention-1",
                payload={"mention": {"text": "crb 11A22"}},
            )
        ],
    )


def _validator_item(
    metadata: DomainPackMetadata,
    envelope: DomainEnvelope,
    *,
    status: str = "resolved",
    resolved_values: dict | None = None,
    resolved_objects: list[dict] | None = None,
    missing_expected_fields: list[str] | None = None,
    candidates: list[dict] | None = None,
    lookup_outcome: str = "success",
) -> ValidatorResultMaterializationInput:
    registry = DomainPackValidationRegistry.from_domain_pack(
        LoadedDomainPack(
            pack_id=metadata.pack_id,
            display_name=metadata.display_name,
            version=metadata.version,
            pack_path=Path("."),
            metadata_path=Path("."),
            metadata=metadata,
        )
    )
    match = registry.match_bindings(
        envelope,
        states=[ValidationBindingState.ACTIVE],
    )[0]
    selector_result = build_domain_validation_request(match)
    assert selector_result.request is not None
    request = selector_result.request
    values = resolved_values if resolved_values is not None else {
        "curie": "DEMO:Allele0001817",
        "symbol": "crb<sup>11A22</sup>",
        "taxon": "NCBITaxon:7227",
    }
    objects = resolved_objects if resolved_objects is not None else [
        {
            "object_type": "Allele",
            "canonical_id": values.get("curie"),
            "payload": {
                "primary_external_id": values.get("curie"),
                "allele_symbol": values.get("symbol"),
                "taxon": values.get("taxon"),
                "ignored_provider_extra": "not materialized",
            },
        }
    ]
    result = DomainValidatorResultBase(
        status=status,
        request_id=request.request_id,
        validator_binding_id=request.validator_binding_id,
        validator_agent=request.validator_agent,
        target=request.target,
        resolved_values=values if status == "resolved" else {},
        resolved_objects=objects if status == "resolved" else [],
        missing_expected_fields=missing_expected_fields or [],
        candidates=candidates or [],
        lookup_attempts=[
            {
                "provider": "fixture_lookup",
                "method": "exact_symbol",
                "query": {"mention": request.selected_inputs["mention"]},
                "result_count": 1 if lookup_outcome == "success" else 2,
                "outcome": lookup_outcome,
            }
        ],
        curator_message=None,
        explanation="Fixture validator decision.",
    )
    return ValidatorResultMaterializationInput(
        match=match,
        request=request,
        result=result,
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
    assert row.summary_fields[0].metadata["unavailable_validator_capabilities"] == [
        {
            "validator_binding_id": "fixture.gene_symbol_lookup",
            "state": "under_development",
            "label": "Gene symbol lookup",
            "state_explanation": "Package-scoped lookup dispatch is pending.",
            "scope": "field",
            "affected_fields": ["gene.symbol"],
            "object_type": "GeneAssertion",
        }
    ]
    assert row.metadata["semantic_source"] == "domain_envelope.objects"
    assert row.metadata["payload_path"] == "objects[0].payload"
    assert row.metadata["evidence_record_ids"] == []
    assert row.metadata["metadata_refs"] == []
    assert row.metadata["unavailable_validator_capabilities"] == (
        row.summary_fields[0].metadata["unavailable_validator_capabilities"]
    )


def test_metadata_materializer_projects_provider_agnostic_fixture_pack_objects():
    metadata = load_domain_pack_metadata(PROVIDER_AGNOSTIC_PACK_PATH / "domain_pack.yaml")
    fixture_pack = load_domain_fixture_pack(
        PROVIDER_AGNOSTIC_PACK_PATH / "fixtures" / "smoke.yaml"
    )
    envelope = fixture_pack.fixtures[0].envelope

    rows = DomainPackMetadataReviewRowMaterializer(metadata).materialize(
        envelope,
        envelope_revision=1,
    )

    assert [row.object_id for row in rows] == ["artifact-1", "action-1"]
    assert [row.object_type for row in rows] == [
        "MuseumArtifact",
        "ConservationAction",
    ]
    assert {row.domain_pack_id for row in rows} == {"museum.catalog"}
    assert {row.schema_provider for row in rows} == {"json-schema"}
    assert rows[0].schema_ref["schema_id"] == "artifact.schema.json"
    assert rows[0].metadata["payload_path"] == "objects[0].payload"
    assert [field.field_path for field in rows[0].summary_fields] == [
        "artifact.accession_id",
        "artifact.title",
        "condition.status",
    ]
    workspace_fields = rows[0].metadata["workspace_fields"]
    assert [field["field_path"] for field in workspace_fields] == [
        "artifact.accession_id",
        "artifact.title",
        "condition.status",
        "curator_review.status",
        "curator_review.measurements",
        "related_artifacts[0].accession_id",
    ]
    assert workspace_fields[0]["metadata"]["workspace_group"] == {
        "id": "artifact_identity",
        "label": "Artifact identity",
        "order": 0,
        "field_order": 0,
    }
    assert workspace_fields[0]["metadata"]["required"] is True
    assert workspace_fields[0]["metadata"]["read_only"] is False


def test_workspace_display_group_requires_explicit_label():
    metadata = DomainPackMetadata(
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
                        "groups": [
                            {
                                "id": "subject",
                                "fields": ["gene.symbol"],
                            }
                        ]
                    },
                },
                fields=[
                    DomainPackFieldDefinition(
                        field_path="gene.symbol",
                        field_type=DomainPackFieldType.STRING,
                        display_name="Gene symbol",
                    )
                ],
            )
        ],
    )
    envelope = DomainEnvelope(
        envelope_id="env-review-1",
        domain_pack_id="fixture.pack",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                pending_ref_id="object-1",
                payload={"gene": {"symbol": "ABC-1"}},
            )
        ],
    )

    with pytest.raises(
        DomainEnvelopeMaterializationError,
        match=r"workspace_display\.groups\[0\]\.label",
    ):
        DomainPackMetadataReviewRowMaterializer(metadata).materialize(
            envelope,
            envelope_revision=1,
        )


def test_workspace_display_group_requires_object_entry():
    metadata = DomainPackMetadata(
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
                        "groups": ["subject"],
                    },
                },
                fields=[
                    DomainPackFieldDefinition(
                        field_path="gene.symbol",
                        field_type=DomainPackFieldType.STRING,
                        display_name="Gene symbol",
                    )
                ],
            )
        ],
    )
    envelope = DomainEnvelope(
        envelope_id="env-review-1",
        domain_pack_id="fixture.pack",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                pending_ref_id="object-1",
                payload={"gene": {"symbol": "ABC-1"}},
            )
        ],
    )

    with pytest.raises(
        DomainEnvelopeMaterializationError,
        match=r"workspace_display\.groups\[0\] must be an object",
    ):
        DomainPackMetadataReviewRowMaterializer(metadata).materialize(
            envelope,
            envelope_revision=1,
        )


def test_workspace_field_without_definition_and_missing_value_uses_any_field_type():
    metadata = DomainPackMetadata(
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
                        "groups": [
                            {
                                "id": "subject",
                                "label": "Subject",
                                "fields": ["gene.missing"],
                            }
                        ]
                    },
                },
                fields=[],
            )
        ],
    )
    envelope = DomainEnvelope(
        envelope_id="env-review-1",
        domain_pack_id="fixture.pack",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                pending_ref_id="object-1",
                payload={"gene": {"symbol": "ABC-1"}},
            )
        ],
    )

    rows = DomainPackMetadataReviewRowMaterializer(metadata).materialize(
        envelope,
        envelope_revision=1,
    )

    workspace_fields = rows[0].metadata["workspace_fields"]
    assert workspace_fields[0]["field_path"] == "gene.missing"
    assert workspace_fields[0]["value"] is None
    assert workspace_fields[0]["field_type"] == "any"


def test_validator_result_materialization_creates_reference_object_and_finding():
    metadata = _validator_metadata()
    envelope = _validator_envelope()
    item = _validator_item(metadata, envelope)

    result = materialize_validator_results_into_envelope(
        envelope,
        metadata,
        [item],
        source_envelope_revision=7,
    )

    assert len(result.materialized_objects) == 1
    reference = result.materialized_objects[0]
    assert reference.object_type == "Allele"
    assert reference.status is CuratableObjectStatus.VALIDATED
    assert reference.payload == {
        "primary_external_id": "DEMO:Allele0001817",
        "allele_symbol": "crb<sup>11A22</sup>",
        "taxon": "NCBITaxon:7227",
    }
    assert reference.metadata["object_role"] == "validated_reference"
    assert reference.metadata["validator_materialization"] == {
        "source": "domain_validator_result",
        "request_id": item.request.request_id,
        "validator_binding_id": "fixture.allele_lookup",
        "validator_agent": {
            "package_id": "fixture.validators",
            "agent_id": "allele_validator",
        },
        "canonical_id": "DEMO:Allele0001817",
        "source_envelope_revision": 7,
    }
    assert result.envelope.objects[0].object_refs == [reference.to_object_ref()]

    finding = result.appended_findings[0]
    assert finding.status is ValidationFindingStatus.RESOLVED
    assert finding.code == "domain_pack.validator_resolved"
    assert finding.field_ref is not None
    assert finding.field_ref.field_path == "mention.text"
    assert finding.details["validation_request"]["input_selectors"]["mention"] == {
        "source": "payload",
        "path": "mention.text",
        "required": True,
    }
    assert finding.details["validation_result"]["resolved_values"]["curie"] == (
        "DEMO:Allele0001817"
    )
    assert finding.details["lookup_attempts"][0]["lookup_status"] == "success"
    DomainEnvelope.model_validate(result.envelope.model_dump(mode="json"))


def test_validator_result_materialization_patches_target_payload_from_resolved_values():
    metadata = DomainPackMetadata(
        pack_id="fixture.target_patch",
        display_name="Fixture Target Patch Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        metadata={
            "validator_bindings": {
                "active": [
                    {
                        "binding_id": "fixture.gene_lookup",
                        "display_name": "Gene lookup",
                        "validator_agent": {
                            "package_id": "fixture.validators",
                            "agent_id": "gene_validator",
                        },
                        "applies_to": {
                            "domain_pack_id": "fixture.target_patch",
                            "object_types": ["GeneMention"],
                        },
                        "input_fields": {
                            "mention": {
                                "source": "payload",
                                "path": "mention",
                            }
                        },
                        "expected_result_fields": {
                            "curie": "primary_external_id",
                            "symbol": "gene_symbol",
                            "taxon": "taxon",
                        },
                    }
                ],
                "under_development": [],
            }
        },
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="GeneMention",
                display_name="Gene mention",
                metadata={"object_role": "validated_reference"},
                fields=[
                    DomainPackFieldDefinition(
                        field_path="mention",
                        field_type=DomainPackFieldType.STRING,
                        required=True,
                    ),
                    DomainPackFieldDefinition(
                        field_path="primary_external_id",
                        field_type=DomainPackFieldType.STRING,
                    ),
                    DomainPackFieldDefinition(
                        field_path="gene_symbol",
                        field_type=DomainPackFieldType.STRING,
                    ),
                    DomainPackFieldDefinition(
                        field_path="taxon",
                        field_type=DomainPackFieldType.STRING,
                    ),
                ],
            )
        ],
    )
    envelope = DomainEnvelope(
        envelope_id="target-patch-env",
        domain_pack_id="fixture.target_patch",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneMention",
                pending_ref_id="gene-mention-1",
                status=CuratableObjectStatus.PENDING,
                payload={"mention": "crumbs"},
            )
        ],
    )
    item = _validator_item(
        metadata,
        envelope,
        resolved_values={
            "curie": "FB:FBgn0259685",
            "symbol": "crb",
            "taxon": "NCBITaxon:7227",
        },
        resolved_objects=[
            {
                "gene_id": "FB:FBgn0259685",
                "symbol": "crb",
                "taxon": "NCBITaxon:7227",
                "species": "Drosophila melanogaster",
                "data_provider": "FB",
            }
        ],
    )

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    assert result.materialized_objects == ()
    patched = result.envelope.objects[0]
    assert patched.status is CuratableObjectStatus.VALIDATED
    assert patched.payload == {
        "mention": "crumbs",
        "primary_external_id": "FB:FBgn0259685",
        "gene_symbol": "crb",
        "taxon": "NCBITaxon:7227",
    }
    patch_event = patched.metadata["validator_resolved_value_materialization"][0]
    assert patch_event["validator_binding_id"] == "fixture.gene_lookup"
    assert patch_event["selected_inputs"] == {"mention": "crumbs"}
    assert patch_event["input_selectors"]["mention"] == {
        "source": "payload",
        "path": "mention",
        "required": True,
    }
    assert patch_event["original_values"] == {}
    finding = result.appended_findings[0]
    assert finding.status is ValidationFindingStatus.RESOLVED
    assert finding.details["validation_result"]["resolved_values"]["curie"] == (
        "FB:FBgn0259685"
    )


def test_validator_result_materialization_merges_multiple_target_payload_patches():
    metadata = DomainPackMetadata(
        pack_id="fixture.target_patch",
        display_name="Fixture Target Patch Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        metadata={
            "validator_bindings": {
                "active": [
                    {
                        "binding_id": "fixture.gene_lookup",
                        "display_name": "Gene lookup",
                        "validator_agent": {
                            "package_id": "fixture.validators",
                            "agent_id": "gene_validator",
                        },
                        "applies_to": {
                            "domain_pack_id": "fixture.target_patch",
                            "object_types": ["GeneMention"],
                        },
                        "input_fields": {
                            "mention": {
                                "source": "payload",
                                "path": "mention",
                            }
                        },
                        "expected_result_fields": {
                            "curie": "primary_external_id",
                            "symbol": "gene_symbol",
                            "taxon": "taxon",
                        },
                    }
                ],
                "under_development": [],
            }
        },
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="GeneMention",
                display_name="Gene mention",
                metadata={"object_role": "validated_reference"},
                fields=[
                    DomainPackFieldDefinition(
                        field_path="mention",
                        field_type=DomainPackFieldType.STRING,
                        required=True,
                    ),
                    DomainPackFieldDefinition(
                        field_path="primary_external_id",
                        field_type=DomainPackFieldType.STRING,
                    ),
                    DomainPackFieldDefinition(
                        field_path="gene_symbol",
                        field_type=DomainPackFieldType.STRING,
                    ),
                    DomainPackFieldDefinition(
                        field_path="taxon",
                        field_type=DomainPackFieldType.STRING,
                    ),
                ],
            )
        ],
    )
    envelope = DomainEnvelope(
        envelope_id="target-patch-env",
        domain_pack_id="fixture.target_patch",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneMention",
                pending_ref_id="gene-mention-1",
                status=CuratableObjectStatus.PENDING,
                payload={"mention": "crumbs"},
            )
        ],
    )
    first_item = _validator_item(
        metadata,
        envelope,
        resolved_values={"curie": "FB:FBgn0259685"},
        resolved_objects=[],
    )
    second_item = _validator_item(
        metadata,
        envelope,
        resolved_values={"symbol": "crb", "taxon": "NCBITaxon:7227"},
        resolved_objects=[],
    )

    result = materialize_validator_results_into_envelope(
        envelope,
        metadata,
        [first_item, second_item],
    )

    assert result.envelope.objects[0].payload == {
        "mention": "crumbs",
        "primary_external_id": "FB:FBgn0259685",
        "gene_symbol": "crb",
        "taxon": "NCBITaxon:7227",
    }
    assert len(
        result.envelope.objects[0].metadata["validator_resolved_value_materialization"]
    ) == 2


def test_validator_result_materialization_is_deterministic_for_existing_reference():
    metadata = _validator_metadata()
    envelope = _validator_envelope()
    item = _validator_item(metadata, envelope)

    first_result = materialize_validator_results_into_envelope(
        envelope,
        metadata,
        [item],
        source_envelope_revision=7,
    )
    second_result = materialize_validator_results_into_envelope(
        first_result.envelope,
        metadata,
        [item],
        source_envelope_revision=7,
    )

    first_reference = first_result.materialized_objects[0]
    assert second_result.materialized_objects == ()
    assert [
        domain_object.object_id
        for domain_object in second_result.envelope.objects
        if domain_object.object_type == "Allele"
    ] == [first_reference.object_id]
    assert second_result.appended_findings == ()


def test_unresolved_validator_result_materializes_missing_field_finding():
    metadata = _validator_metadata()
    envelope = _validator_envelope()
    item = _validator_item(
        metadata,
        envelope,
        status="unresolved",
        missing_expected_fields=["curie", "symbol"],
        lookup_outcome="not_found",
    )

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    assert result.materialized_objects == ()
    finding = result.appended_findings[0]
    assert finding.status is ValidationFindingStatus.OPEN
    assert finding.code == "domain_pack.validator_unresolved"
    assert finding.details["failure_classification"] == "missing_expected_result_field"
    assert finding.details["missing_expected_fields"] == ["curie", "symbol"]
    assert finding.details["lookup_attempts"][0]["lookup_status"] == "not_found"


def test_ambiguous_validator_result_preserves_candidate_diagnostics():
    metadata = _validator_metadata()
    envelope = _validator_envelope()
    item = _validator_item(
        metadata,
        envelope,
        status="unresolved",
        candidates=[
            {
                "value": "DEMO:Allele0001817",
                "label": "crb<sup>11A22</sup>",
                "object_type": "Allele",
                "score": 0.71,
            },
            {
                "value": "DEMO:Allele9999999",
                "label": "crb-like",
                "object_type": "Allele",
                "score": 0.62,
            },
        ],
        lookup_outcome="ambiguous",
    )

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    finding = result.appended_findings[0]
    assert finding.details["failure_classification"] == "ambiguous"
    assert [candidate["value"] for candidate in finding.details["candidate_matches"]] == [
        "DEMO:Allele0001817",
        "DEMO:Allele9999999",
    ]
    assert finding.details["lookup_attempts"][0]["candidate_count"] == 2


def test_invalid_resolved_object_materializes_open_finding_without_reference():
    metadata = _validator_metadata()
    envelope = _validator_envelope()
    item = _validator_item(
        metadata,
        envelope,
        resolved_objects=[
            {
                "object_type": "AlleleCandidate",
                "canonical_id": "DEMO:Allele0001817",
                "payload": {"primary_external_id": "DEMO:Allele0001817"},
            }
        ],
    )

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    assert result.materialized_objects == ()
    assert len(result.envelope.objects) == 1
    finding = result.appended_findings[0]
    assert finding.status is ValidationFindingStatus.OPEN
    assert finding.code == "domain_pack.validator_materialization_invalid"
    assert finding.details["failure_classification"] == "invalid_materialization_input"
    assert "not a validated_reference" in finding.details["materialization_error"]


def test_validator_materialization_rejects_invalid_source_revision():
    metadata = _validator_metadata()
    envelope = _validator_envelope()
    item = _validator_item(metadata, envelope)

    with pytest.raises(DomainEnvelopeMaterializationError):
        materialize_validator_results_into_envelope(
            envelope,
            metadata,
            [item],
            source_envelope_revision=0,
        )
