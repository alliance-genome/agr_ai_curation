"""Constants for the Alliance chemical-condition domain pack."""

from __future__ import annotations

from pathlib import Path

from ..paths import get_alliance_domain_packs_dir


CHEMICAL_CONDITION_DOMAIN_PACK_ID = "agr.alliance.chemical_condition"
CHEMICAL_CONDITION_DOMAIN_PACK_DIR_NAME = "chemical_condition"
CHEMICAL_CONDITION_DOMAIN_PACK_VERSION = "0.1.0"
CHEMICAL_CONDITION_MODEL_ID = "ChemicalConditionPayload"
CHEMICAL_CONDITION_OBJECT_TYPE = "ChemicalCondition"
CHEMICAL_TERM_OBJECT_TYPE = "ChemicalTerm"
REFERENCE_OBJECT_TYPE = "Reference"
EVIDENCE_QUOTE_OBJECT_TYPE = "EvidenceQuote"
CHEMICAL_CONDITION_VALIDATOR_STATES = ("active", "under_development")
CHEMICAL_CONDITION_PENDING_VALIDATOR_ID = (
    "chemical_condition.pending_envelope_validator"
)
CHEMICAL_CONDITION_CHEBI_FORMAT_VALIDATOR_ID = "chemical_condition.chebi_curie_format"
CHEMICAL_CONDITION_CONVERTER_ID = (
    "agr_ai_curation_alliance.domain_packs.chemical_condition"
)
CHEMICAL_CONDITION_EXPORT_CONTEXT_FIELDS = (
    "host_annotation_type",
    "host_annotation_id",
    "source_reference.reference_id",
)

CHEMICAL_CONDITION_LINKML_SCHEMA_SOURCE_FILE = (
    "model/schema/phenotypeAndDiseaseAnnotation.yaml"
)
CHEMICAL_CONDITION_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE = "model/schema/ontologyTerm.yaml"
CHEMICAL_CONDITION_REFERENCE_SCHEMA_SOURCE_FILE = "model/schema/reference.yaml"


def get_chemical_condition_domain_pack_metadata_path() -> Path:
    """Return the bundled chemical-condition domain-pack metadata path."""

    return (
        get_alliance_domain_packs_dir()
        / CHEMICAL_CONDITION_DOMAIN_PACK_DIR_NAME
        / "domain_pack.yaml"
    )


__all__ = [
    "CHEMICAL_CONDITION_CHEBI_FORMAT_VALIDATOR_ID",
    "CHEMICAL_CONDITION_CONVERTER_ID",
    "CHEMICAL_CONDITION_DOMAIN_PACK_DIR_NAME",
    "CHEMICAL_CONDITION_DOMAIN_PACK_ID",
    "CHEMICAL_CONDITION_DOMAIN_PACK_VERSION",
    "CHEMICAL_CONDITION_EXPORT_CONTEXT_FIELDS",
    "CHEMICAL_CONDITION_LINKML_SCHEMA_SOURCE_FILE",
    "CHEMICAL_CONDITION_MODEL_ID",
    "CHEMICAL_CONDITION_OBJECT_TYPE",
    "CHEMICAL_CONDITION_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE",
    "CHEMICAL_CONDITION_PENDING_VALIDATOR_ID",
    "CHEMICAL_CONDITION_REFERENCE_SCHEMA_SOURCE_FILE",
    "CHEMICAL_CONDITION_VALIDATOR_STATES",
    "CHEMICAL_TERM_OBJECT_TYPE",
    "EVIDENCE_QUOTE_OBJECT_TYPE",
    "REFERENCE_OBJECT_TYPE",
    "get_chemical_condition_domain_pack_metadata_path",
]
