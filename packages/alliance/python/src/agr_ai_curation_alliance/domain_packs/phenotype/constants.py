"""Constants for the Alliance phenotype pending-annotation domain pack."""

from __future__ import annotations

from pathlib import Path

from ..paths import get_alliance_domain_packs_dir


PHENOTYPE_DOMAIN_PACK_ID = "agr.alliance.phenotype"
PHENOTYPE_DOMAIN_PACK_DIR_NAME = "phenotype"
PHENOTYPE_DOMAIN_PACK_VERSION = "0.1.0"
PHENOTYPE_OBJECT_TYPE = "PhenotypeAnnotation"
PHENOTYPE_SUBJECT_OBJECT_TYPE = "PhenotypeSubject"
PHENOTYPE_TERM_OBJECT_TYPE = "PhenotypeTerm"
PHENOTYPE_FIXTURE_PACK_ID = "tool_verified_pending"
PHENOTYPE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID = (
    "phenotype_pending_envelope_validator"
)
PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID = "phenotype_subject_entity_validator"
PHENOTYPE_TERM_VALIDATOR_BINDING_ID = "phenotype_term_ontology_validator"

PHENOTYPE_LINKML_SCHEMA_SOURCE_FILE = (
    "model/schema/phenotypeAndDiseaseAnnotation.yaml"
)
PHENOTYPE_CORE_SCHEMA_SOURCE_FILE = "model/schema/core.yaml"
PHENOTYPE_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE = "model/schema/ontologyTerm.yaml"
PHENOTYPE_REFERENCE_SCHEMA_SOURCE_FILE = "model/schema/reference.yaml"


def get_phenotype_domain_pack_metadata_path() -> Path:
    """Return the bundled phenotype domain-pack metadata path."""

    return (
        get_alliance_domain_packs_dir()
        / PHENOTYPE_DOMAIN_PACK_DIR_NAME
        / "domain_pack.yaml"
    )


__all__ = [
    "PHENOTYPE_CORE_SCHEMA_SOURCE_FILE",
    "PHENOTYPE_DOMAIN_PACK_DIR_NAME",
    "PHENOTYPE_DOMAIN_PACK_ID",
    "PHENOTYPE_DOMAIN_PACK_VERSION",
    "PHENOTYPE_FIXTURE_PACK_ID",
    "PHENOTYPE_LINKML_SCHEMA_SOURCE_FILE",
    "PHENOTYPE_OBJECT_TYPE",
    "PHENOTYPE_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE",
    "PHENOTYPE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID",
    "PHENOTYPE_REFERENCE_SCHEMA_SOURCE_FILE",
    "PHENOTYPE_SUBJECT_OBJECT_TYPE",
    "PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID",
    "PHENOTYPE_TERM_OBJECT_TYPE",
    "PHENOTYPE_TERM_VALIDATOR_BINDING_ID",
    "get_phenotype_domain_pack_metadata_path",
]
