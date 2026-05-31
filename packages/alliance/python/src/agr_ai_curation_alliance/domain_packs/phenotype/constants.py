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

# Builder-pattern (Phase 3 envelope -> builder migration) identity. Mirrors gene_expression's
# GENE_EXPRESSION_MATERIALIZER_ID. The builder materializer emits the SAME object graph the
# envelope converter produced (one PhenotypeAnnotation curatable_unit plus pending
# PhenotypeSubject / PhenotypeTerm / Reference / EvidenceQuote objects), so the existing pack
# posture (blocked export/write, pending ontology resolution) is preserved.
PHENOTYPE_MATERIALIZER_ID = "agr.alliance.phenotype.builder_materializer.v1"
PHENOTYPE_DOMAIN_PACK_CONVERTER_ID = (
    "agr_ai_curation_alliance.domain_packs.phenotype"
)
PHENOTYPE_ANNOTATION_MODEL_ID = "PhenotypeAnnotationPayload"
PHENOTYPE_ANNOTATION_OBJECT_ROLE = "curatable_unit"
PHENOTYPE_ANNOTATION_KIND = "phenotype_assertion"
PHENOTYPE_REFERENCE_OBJECT_TYPE = "Reference"
PHENOTYPE_EVIDENCE_QUOTE_OBJECT_TYPE = "EvidenceQuote"
PHENOTYPE_REFERENCE_VALIDATOR_BINDING_ID = "phenotype_reference_validator"
PHENOTYPE_ANNOTATION_LINKML_SCHEMA_ID = "alliance.linkml.PhenotypeAnnotation"
PHENOTYPE_TERM_LINKML_SCHEMA_ID = "alliance.linkml.PhenotypeTerm"
PHENOTYPE_SUBJECT_LINKML_SCHEMA_ID = "alliance.linkml.BiologicalEntity"
PHENOTYPE_REFERENCE_LINKML_SCHEMA_ID = "alliance.linkml.Reference"


def get_phenotype_domain_pack_metadata_path() -> Path:
    """Return the bundled phenotype domain-pack metadata path."""

    return (
        get_alliance_domain_packs_dir()
        / PHENOTYPE_DOMAIN_PACK_DIR_NAME
        / "domain_pack.yaml"
    )


__all__ = [
    "PHENOTYPE_ANNOTATION_KIND",
    "PHENOTYPE_ANNOTATION_LINKML_SCHEMA_ID",
    "PHENOTYPE_ANNOTATION_MODEL_ID",
    "PHENOTYPE_ANNOTATION_OBJECT_ROLE",
    "PHENOTYPE_CORE_SCHEMA_SOURCE_FILE",
    "PHENOTYPE_DOMAIN_PACK_CONVERTER_ID",
    "PHENOTYPE_DOMAIN_PACK_DIR_NAME",
    "PHENOTYPE_DOMAIN_PACK_ID",
    "PHENOTYPE_DOMAIN_PACK_VERSION",
    "PHENOTYPE_EVIDENCE_QUOTE_OBJECT_TYPE",
    "PHENOTYPE_FIXTURE_PACK_ID",
    "PHENOTYPE_LINKML_SCHEMA_SOURCE_FILE",
    "PHENOTYPE_MATERIALIZER_ID",
    "PHENOTYPE_OBJECT_TYPE",
    "PHENOTYPE_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE",
    "PHENOTYPE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID",
    "PHENOTYPE_REFERENCE_LINKML_SCHEMA_ID",
    "PHENOTYPE_REFERENCE_OBJECT_TYPE",
    "PHENOTYPE_REFERENCE_SCHEMA_SOURCE_FILE",
    "PHENOTYPE_REFERENCE_VALIDATOR_BINDING_ID",
    "PHENOTYPE_SUBJECT_LINKML_SCHEMA_ID",
    "PHENOTYPE_SUBJECT_OBJECT_TYPE",
    "PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID",
    "PHENOTYPE_TERM_LINKML_SCHEMA_ID",
    "PHENOTYPE_TERM_OBJECT_TYPE",
    "PHENOTYPE_TERM_VALIDATOR_BINDING_ID",
    "get_phenotype_domain_pack_metadata_path",
]
