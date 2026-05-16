"""Allele extractor schema for Alliance allele domain-envelope output."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import RootModel, model_validator

from src.lib.domain_packs.repair_patches import (
    DomainEnvelopeExtractorFinalClassification,
    DomainEnvelopeRepairPatch,
)
from src.lib.openai_agents.models import (
    AlleleExtractionResultEnvelope as RuntimeAlleleExtractionResultEnvelope,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DefinitionState


_EXPECTED_OBJECT_ROLES = {
    "AllelePaperEvidenceAssociation": "curatable_unit",
    "Allele": "validated_reference",
    "Reference": "validated_reference",
    "AlleleMention": "metadata_only",
    "EvidenceQuote": "metadata_only",
}
_REQUIRED_ASSOCIATION_REF_TYPES = {
    "Reference",
    "AlleleMention",
    "EvidenceQuote",
}
_REQUIRED_ASSOCIATION_PAYLOAD_FIELDS = (
    "association_kind",
    "evidence_record_ids",
)
_REQUIRED_EVIDENCE_QUOTE_PAYLOAD_FIELDS = (
    "evidence_record_id",
    "verified_quote",
    "page",
    "section",
    "chunk_id",
)


class AlleleExtractionResultEnvelope(RuntimeAlleleExtractionResultEnvelope):
    """Config-discovered allele extraction envelope bound to the allele pack."""

    __envelope_class__ = True

    @model_validator(mode="after")
    def _validate_allele_domain_envelope(self) -> "AlleleExtractionResultEnvelope":
        object_types = {obj.object_type for obj in self.curatable_objects}
        unsupported_types = sorted(set(object_types) - set(_EXPECTED_OBJECT_ROLES))
        if unsupported_types:
            raise ValueError(
                "Allele extractor curatable_objects[] may only contain allele "
                "domain-pack object types: "
                + ", ".join(sorted(_EXPECTED_OBJECT_ROLES))
            )

        evidence_record_ids = {
            record.evidence_record_id
            for record in self.metadata.evidence_records
            if record.evidence_record_id
        }
        for obj in self.curatable_objects:
            self._validate_object_role(obj)
            self._validate_object_definition_state(obj)
            if obj.object_type == "AllelePaperEvidenceAssociation":
                self._validate_association_object(obj, evidence_record_ids)
            elif obj.object_type == "Allele":
                self._validate_extractor_does_not_claim_allele_identity(obj)
            elif obj.object_type == "EvidenceQuote":
                self._validate_required_payload_fields(
                    obj,
                    _REQUIRED_EVIDENCE_QUOTE_PAYLOAD_FIELDS,
                )
        return self

    @classmethod
    def _validate_object_role(cls, obj: CuratableObjectEnvelope) -> None:
        expected_role = _EXPECTED_OBJECT_ROLES[obj.object_type]
        role = obj.object_role or _optional_mapping(obj.metadata).get("object_role")
        if role != expected_role:
            raise ValueError(
                f"{obj.object_type} must declare object_role '{expected_role}'"
            )

    @classmethod
    def _validate_object_definition_state(cls, obj: CuratableObjectEnvelope) -> None:
        if obj.definition_state != DefinitionState.IN_DEVELOPMENT:
            raise ValueError(
                f"{obj.object_type} must declare definition_state 'in_development'"
            )

    @classmethod
    def _validate_association_object(
        cls,
        obj: CuratableObjectEnvelope,
        evidence_record_ids: set[str],
    ) -> None:
        cls._validate_required_payload_fields(obj, _REQUIRED_ASSOCIATION_PAYLOAD_FIELDS)
        if obj.payload.get("association_kind") != "allele_paper_evidence":
            raise ValueError(
                "AllelePaperEvidenceAssociation payload.association_kind must be "
                "'allele_paper_evidence'"
            )
        if not _is_missing_payload_value(obj.payload.get("allele_identifier")):
            raise ValueError(
                "AllelePaperEvidenceAssociation payload.allele_identifier must be "
                "resolved by the active allele validator, not emitted by the extractor"
            )

        object_ref_types = {ref.object_type for ref in obj.object_refs if ref.object_type}
        missing_ref_types = sorted(_REQUIRED_ASSOCIATION_REF_TYPES - object_ref_types)
        if missing_ref_types:
            raise ValueError(
                "AllelePaperEvidenceAssociation must reference supporting objects: "
                + ", ".join(missing_ref_types)
            )

        payload_evidence_ids = _string_list(obj.payload.get("evidence_record_ids"))
        object_evidence_ids = [str(item) for item in obj.evidence_record_ids]
        if not object_evidence_ids:
            raise ValueError(
                "AllelePaperEvidenceAssociation must carry curatable object "
                "evidence_record_ids"
            )
        if payload_evidence_ids != object_evidence_ids:
            raise ValueError(
                "AllelePaperEvidenceAssociation payload.evidence_record_ids must "
                "match curatable object evidence_record_ids"
            )

        missing_metadata_ids = sorted(
            evidence_id
            for evidence_id in object_evidence_ids
            if evidence_id not in evidence_record_ids
        )
        if missing_metadata_ids:
            raise ValueError(
                "AllelePaperEvidenceAssociation evidence_record_ids must resolve in "
                "metadata.evidence_records[]: "
                + ", ".join(missing_metadata_ids)
            )

        write_behavior = _optional_mapping(obj.metadata).get("write_behavior")
        if (
            not isinstance(write_behavior, Mapping)
            or write_behavior.get("status") != "blocked"
        ):
            raise ValueError(
                "AllelePaperEvidenceAssociation metadata.write_behavior.status "
                "must be 'blocked'"
            )

    @classmethod
    def _validate_extractor_does_not_claim_allele_identity(
        cls,
        obj: CuratableObjectEnvelope,
    ) -> None:
        claimed_fields = [
            field_name
            for field_name in ("primary_external_id", "allele_symbol", "taxon")
            if not _is_missing_payload_value(obj.payload.get(field_name))
        ]
        if claimed_fields:
            raise ValueError(
                "Allele validated-reference payload fields must be resolved by "
                "the active allele validator, not emitted by the extractor: "
                + ", ".join(claimed_fields)
            )

    @classmethod
    def _validate_required_payload_fields(
        cls,
        obj: CuratableObjectEnvelope,
        field_names: tuple[str, ...],
    ) -> None:
        missing_fields = [
            field_name
            for field_name in field_names
            if _is_missing_payload_value(obj.payload.get(field_name))
        ]
        if missing_fields:
            raise ValueError(
                f"{obj.object_type} payload is missing required field(s): "
                + ", ".join(missing_fields)
            )


class AlleleExtractorRepairResponse(
    RootModel[
        AlleleExtractionResultEnvelope
        | DomainEnvelopeRepairPatch
        | DomainEnvelopeExtractorFinalClassification
    ]
):
    """Allele first-pass extraction or repair_action response schema."""

    __envelope_class__ = True
    __domain_envelope_extractor_repair_response__ = True


def _optional_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_missing_payload_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    return False


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
