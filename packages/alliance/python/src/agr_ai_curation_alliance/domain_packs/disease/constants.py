"""Constants for the Alliance disease pending-assertion domain pack."""

from __future__ import annotations

from pathlib import Path

from ..paths import get_alliance_domain_packs_dir
from ..schema_refs import ALLIANCE_LINKML_COMMIT

DISEASE_DOMAIN_PACK_ID = "agr.alliance.disease"
DISEASE_DOMAIN_PACK_DIR_NAME = "disease"
DISEASE_DOMAIN_PACK_VERSION = "0.1.0"
DISEASE_OBJECT_TYPE = "DiseaseAnnotation"
DISEASE_MODEL_ID = "PendingDiseaseAssertionPayload"
DISEASE_FIXTURE_PACK_ID = "tool_verified"
DISEASE_VALIDATOR_STATES = ("active", "under_development")
DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID = "disease_pending_envelope_validator"
DISEASE_DOMAIN_PACK_CONVERTER_ID = "agr_ai_curation_alliance.domain_packs.disease"

DISEASE_LINKML_SCHEMA_ID = "alliance.linkml.DiseaseAnnotation"
DISEASE_LINKML_SCHEMA_NAME = "DiseaseAnnotation"
DISEASE_LINKML_SCHEMA_SOURCE_FILE = "model/schema/phenotypeAndDiseaseAnnotation.yaml"
DISEASE_LINKML_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{ALLIANCE_LINKML_COMMIT}/{DISEASE_LINKML_SCHEMA_SOURCE_FILE}"
)

DISEASE_DEFINITION_NOTES = (
    "Pending assertion payload grounded to abstract LinkML DiseaseAnnotation metadata.",
    "Concrete GeneDiseaseAnnotation, AlleleDiseaseAnnotation, or AGMDiseaseAnnotation "
    "materialization is blocked until subject, reference, evidence-code, and export "
    "targets are verified for migrated disease extractor output.",
)

REQUIRED_DISEASE_PAYLOAD_FIELDS = (
    "mention",
    "disease_annotation_object",
    "disease_annotation_object.name",
    "role",
    "confidence",
    "data_provider",
    "data_provider.abbreviation",
    "evidence_record_ids",
    "evidence_records",
)

FORBIDDEN_LEGACY_COLLECTIONS = frozenset(
    {
        "items",
        "annotations",
        "genes",
        "alleles",
        "diseases",
        "chemicals",
        "phenotypes",
        "CurationPrepCandidate",
        "NormalizedCandidate",
        "normalized_payload",
        "annotation_drafts",
    }
)


def get_disease_domain_pack_metadata_path() -> Path:
    """Return the bundled disease domain-pack metadata path."""

    return (
        get_alliance_domain_packs_dir()
        / DISEASE_DOMAIN_PACK_DIR_NAME
        / "domain_pack.yaml"
    )


__all__ = [
    "DISEASE_DEFINITION_NOTES",
    "DISEASE_DOMAIN_PACK_CONVERTER_ID",
    "DISEASE_DOMAIN_PACK_DIR_NAME",
    "DISEASE_DOMAIN_PACK_ID",
    "DISEASE_DOMAIN_PACK_VERSION",
    "DISEASE_FIXTURE_PACK_ID",
    "DISEASE_LINKML_SCHEMA_ID",
    "DISEASE_LINKML_SCHEMA_NAME",
    "DISEASE_LINKML_SCHEMA_SOURCE_FILE",
    "DISEASE_LINKML_SCHEMA_URI",
    "DISEASE_MODEL_ID",
    "DISEASE_OBJECT_TYPE",
    "DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID",
    "DISEASE_VALIDATOR_STATES",
    "FORBIDDEN_LEGACY_COLLECTIONS",
    "REQUIRED_DISEASE_PAYLOAD_FIELDS",
    "get_disease_domain_pack_metadata_path",
]
