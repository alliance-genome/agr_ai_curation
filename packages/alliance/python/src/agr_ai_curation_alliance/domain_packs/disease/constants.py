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
DISEASE_CORE_SCHEMA_SOURCE_FILE = "model/schema/core.yaml"
DISEASE_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE = "model/schema/ontologyTerm.yaml"
DISEASE_REFERENCE_SCHEMA_SOURCE_FILE = "model/schema/reference.yaml"
DISEASE_LINKML_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{ALLIANCE_LINKML_COMMIT}/{DISEASE_LINKML_SCHEMA_SOURCE_FILE}"
)

# ---------------------------------------------------------------------------------------------
# Builder-pattern (Phase 2 envelope -> builder migration) identity + FULL LinkML alignment.
#
# Unlike phenotype/allele (which preserved the existing pack's pending/abstract/blocked posture),
# disease is brought to FULL LinkML alignment per the approach-doc Decisions (D1-D5):
#   * D1: materialize the CONCRETE GeneDiseaseAnnotation / AlleleDiseaseAnnotation /
#         AGMDiseaseAnnotation subtype chosen by the staged subject kind (the abstract
#         DiseaseAnnotation is emitted ONLY when the subject kind is unknown -> validator_unresolved,
#         which is NOT a structural finding).
#   * D2: stage + resolve the subject (subject_entity_validation activated).
#   * D3: stage ECO evidence_code_curies[] (disease_evidence_code_lookup activated).
#   * D5: per-subtype relation CV subsets.
# D4 (bind single_reference from the loaded workspace document) is BLOCKED: there is no durable
# Alliance reference identity available at chat-extraction time (see approach-doc open questions);
# single_reference stays pending -> reference validator returns validator_unresolved (non-structural).
# D6 (condition_relations) is deferred.
# ---------------------------------------------------------------------------------------------
DISEASE_MATERIALIZER_ID = "agr.alliance.disease.builder_materializer.v1"

# Concrete LinkML subtypes selected by subject kind (D1). The abstract DiseaseAnnotation object_type
# is retained for the unknown-subject fallback only.
DISEASE_GENE_OBJECT_TYPE = "GeneDiseaseAnnotation"
DISEASE_ALLELE_OBJECT_TYPE = "AlleleDiseaseAnnotation"
DISEASE_AGM_OBJECT_TYPE = "AGMDiseaseAnnotation"

DISEASE_GENE_LINKML_SCHEMA_ID = "alliance.linkml.GeneDiseaseAnnotation"
DISEASE_ALLELE_LINKML_SCHEMA_ID = "alliance.linkml.AlleleDiseaseAnnotation"
DISEASE_AGM_LINKML_SCHEMA_ID = "alliance.linkml.AGMDiseaseAnnotation"

# subject_type (staged enum) -> (concrete object_type, concrete schema_id, LinkML class name).
DISEASE_SUBJECT_SUBTYPES = {
    "gene": (DISEASE_GENE_OBJECT_TYPE, DISEASE_GENE_LINKML_SCHEMA_ID, "GeneDiseaseAnnotation"),
    "allele": (DISEASE_ALLELE_OBJECT_TYPE, DISEASE_ALLELE_LINKML_SCHEMA_ID, "AlleleDiseaseAnnotation"),
    "agm": (DISEASE_AGM_OBJECT_TYPE, DISEASE_AGM_LINKML_SCHEMA_ID, "AGMDiseaseAnnotation"),
}

# Per-subtype relation CV subset members (D5; VERIFIED no divergence between LinkML, the formal CV
# subsets, and curator usage). is_implicated_via_orthology / is_marker_via_orthology belong to the
# 'Via Orthology Disease Relation' subset and are permitted for gene-subject orthology inferences.
DISEASE_RELATION_SUBSETS = {
    "gene": ("is_implicated_in", "is_marker_for", "is_implicated_via_orthology", "is_marker_via_orthology"),
    "allele": ("is_implicated_in",),
    "agm": ("is_model_of", "is_ameliorated_model_of", "is_exacerbated_model_of"),
}

# Pending sub-object identity (subject reference + DOID term + reference) materialized alongside the
# concrete annotation, mirroring the phenotype/allele multi-object graph shape.
DISEASE_SUBJECT_OBJECT_TYPE = "DiseaseAnnotationSubject"
DISEASE_TERM_OBJECT_TYPE = "DOTerm"
DISEASE_REFERENCE_OBJECT_TYPE = "Reference"
DISEASE_EVIDENCE_QUOTE_OBJECT_TYPE = "EvidenceQuote"

DISEASE_SUBJECT_LINKML_SCHEMA_ID = "alliance.linkml.BiologicalEntity"
DISEASE_TERM_LINKML_SCHEMA_ID = "alliance.linkml.DOTerm"
DISEASE_REFERENCE_LINKML_SCHEMA_ID = "alliance.linkml.Reference"

# Validator binding ids (activated by this migration; declared in domain_pack.yaml).
DISEASE_ONTOLOGY_TERM_VALIDATOR_BINDING_ID = "disease_ontology_term_lookup"
DISEASE_RELATION_VALIDATOR_BINDING_ID = "disease_relation_cv_lookup"
DISEASE_DATA_PROVIDER_VALIDATOR_BINDING_ID = "disease_data_provider_lookup"
DISEASE_SUBJECT_VALIDATOR_BINDING_ID = "disease_subject_materialization"
DISEASE_REFERENCE_VALIDATOR_BINDING_ID = "disease_reference_materialization"
DISEASE_EVIDENCE_CODE_VALIDATOR_BINDING_ID = "disease_evidence_code_lookup"

# Optional-slot bindings (R4): annotation_type constant, genetic_sex CV, disease_qualifiers CV,
# with_or_from gene reference. annotation_type is fixed to a constant and never extracted/staged;
# the other three are optional, paper-supported extractor inputs validated through the active
# CV / gene_validation agents and follow the existing `[0]` first-element convention for the
# multivalued slots (disease_qualifier_names / with_gene_identifiers).
DISEASE_ANNOTATION_TYPE_CV_BINDING_ID = "disease_annotation_type_cv_lookup"
DISEASE_GENETIC_SEX_CV_BINDING_ID = "disease_genetic_sex_cv_lookup"
DISEASE_QUALIFIER_CV_BINDING_ID = "disease_qualifier_cv_lookup"
DISEASE_WITH_GENE_VALIDATION_BINDING_ID = "disease_with_gene_validation"

# annotation_type is the curation method, fixed to manually_curated; it is NOT an extractor edit
# target — the builder always materializes this constant onto every disease annotation payload.
DISEASE_ANNOTATION_TYPE_CONSTANT = "manually_curated"

DISEASE_ANNOTATION_OBJECT_ROLE = "curatable_unit"
DISEASE_ANNOTATION_KIND = "disease_assertion"

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
    "DISEASE_AGM_LINKML_SCHEMA_ID",
    "DISEASE_AGM_OBJECT_TYPE",
    "DISEASE_ALLELE_LINKML_SCHEMA_ID",
    "DISEASE_ALLELE_OBJECT_TYPE",
    "DISEASE_ANNOTATION_KIND",
    "DISEASE_ANNOTATION_OBJECT_ROLE",
    "DISEASE_ANNOTATION_TYPE_CONSTANT",
    "DISEASE_ANNOTATION_TYPE_CV_BINDING_ID",
    "DISEASE_CORE_SCHEMA_SOURCE_FILE",
    "DISEASE_DATA_PROVIDER_VALIDATOR_BINDING_ID",
    "DISEASE_DEFINITION_NOTES",
    "DISEASE_DOMAIN_PACK_CONVERTER_ID",
    "DISEASE_DOMAIN_PACK_DIR_NAME",
    "DISEASE_DOMAIN_PACK_ID",
    "DISEASE_DOMAIN_PACK_VERSION",
    "DISEASE_EVIDENCE_CODE_VALIDATOR_BINDING_ID",
    "DISEASE_EVIDENCE_QUOTE_OBJECT_TYPE",
    "DISEASE_FIXTURE_PACK_ID",
    "DISEASE_GENE_LINKML_SCHEMA_ID",
    "DISEASE_GENE_OBJECT_TYPE",
    "DISEASE_GENETIC_SEX_CV_BINDING_ID",
    "DISEASE_LINKML_SCHEMA_ID",
    "DISEASE_LINKML_SCHEMA_NAME",
    "DISEASE_LINKML_SCHEMA_SOURCE_FILE",
    "DISEASE_LINKML_SCHEMA_URI",
    "DISEASE_MATERIALIZER_ID",
    "DISEASE_MODEL_ID",
    "DISEASE_OBJECT_TYPE",
    "DISEASE_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE",
    "DISEASE_ONTOLOGY_TERM_VALIDATOR_BINDING_ID",
    "DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID",
    "DISEASE_QUALIFIER_CV_BINDING_ID",
    "DISEASE_REFERENCE_LINKML_SCHEMA_ID",
    "DISEASE_REFERENCE_OBJECT_TYPE",
    "DISEASE_REFERENCE_SCHEMA_SOURCE_FILE",
    "DISEASE_REFERENCE_VALIDATOR_BINDING_ID",
    "DISEASE_RELATION_SUBSETS",
    "DISEASE_RELATION_VALIDATOR_BINDING_ID",
    "DISEASE_SUBJECT_LINKML_SCHEMA_ID",
    "DISEASE_SUBJECT_OBJECT_TYPE",
    "DISEASE_SUBJECT_SUBTYPES",
    "DISEASE_SUBJECT_VALIDATOR_BINDING_ID",
    "DISEASE_TERM_LINKML_SCHEMA_ID",
    "DISEASE_TERM_OBJECT_TYPE",
    "DISEASE_VALIDATOR_STATES",
    "DISEASE_WITH_GENE_VALIDATION_BINDING_ID",
    "FORBIDDEN_LEGACY_COLLECTIONS",
    "REQUIRED_DISEASE_PAYLOAD_FIELDS",
    "get_disease_domain_pack_metadata_path",
]
