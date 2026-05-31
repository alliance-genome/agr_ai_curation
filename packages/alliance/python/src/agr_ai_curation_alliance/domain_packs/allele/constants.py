"""Constants for the Alliance allele paper/evidence domain pack."""

from __future__ import annotations

from ..schema_refs import ALLIANCE_LINKML_COMMIT


ALLELE_DOMAIN_PACK_ID = "agr.alliance.allele"
ALLELE_DOMAIN_PACK_VERSION = "0.1.0"

# Object types in the 4-object pending association graph (the extractor NEVER emits Allele;
# the active allele validator materializes allele identity).
ALLELE_ASSOCIATION_OBJECT_TYPE = "AllelePaperEvidenceAssociation"
ALLELE_MENTION_OBJECT_TYPE = "AlleleMention"
ALLELE_REFERENCE_OBJECT_TYPE = "Reference"
ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE = "EvidenceQuote"

ALLELE_ASSOCIATION_MODEL_ID = "AllelePaperEvidenceAssociationPayload"
ALLELE_MENTION_MODEL_ID = "AlleleMentionPayload"
ALLELE_REFERENCE_MODEL_ID = "ReferencePayload"
ALLELE_EVIDENCE_QUOTE_MODEL_ID = "EvidenceQuotePayload"

ALLELE_ASSOCIATION_OBJECT_ROLE = "curatable_unit"
ALLELE_ASSOCIATION_KIND = "allele_paper_evidence"

# The single ACTIVE validator binding (reused unchanged; the migration changes the extraction
# mechanism, not the curation target). It fires on AlleleMention.mention.text and materializes
# the validator-owned Allele identity scalars (curie/symbol/taxon).
ALLELE_MENTION_REFERENCE_VALIDATOR_BINDING_ID = "allele_mention_reference_validation"

# LinkML grounding (pinned commit). The abstract AlleleAssociation class is used only for
# pending-envelope metadata; writes/exports stay blocked.
ALLELE_ASSOCIATION_LINKML_SCHEMA_ID = "alliance.linkml.AlleleAssociation"
ALLELE_REFERENCE_LINKML_SCHEMA_ID = "alliance.linkml.Reference"
ALLELE_LINKML_SCHEMA_SOURCE_FILE = "model/schema/allele.yaml"
ALLELE_REFERENCE_SCHEMA_SOURCE_FILE = "model/schema/reference.yaml"

# Builder-pattern (Phase 4 envelope -> builder migration) identity. Mirrors gene's
# GENE_MATERIALIZER_ID and phenotype's PHENOTYPE_MATERIALIZER_ID. The builder materializer emits
# the SAME 4-object graph the envelope converter produced, with the SAME blocked write/export
# posture and the SAME reused validator binding.
ALLELE_MATERIALIZER_ID = "agr.alliance.allele.builder_materializer.v1"
ALLELE_DOMAIN_PACK_CONVERTER_ID = "agr_ai_curation_alliance.domain_packs.allele"


__all__ = [
    "ALLELE_ASSOCIATION_KIND",
    "ALLELE_ASSOCIATION_LINKML_SCHEMA_ID",
    "ALLELE_ASSOCIATION_MODEL_ID",
    "ALLELE_ASSOCIATION_OBJECT_ROLE",
    "ALLELE_ASSOCIATION_OBJECT_TYPE",
    "ALLELE_DOMAIN_PACK_CONVERTER_ID",
    "ALLELE_DOMAIN_PACK_ID",
    "ALLELE_DOMAIN_PACK_VERSION",
    "ALLELE_EVIDENCE_QUOTE_MODEL_ID",
    "ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE",
    "ALLELE_LINKML_SCHEMA_SOURCE_FILE",
    "ALLELE_MATERIALIZER_ID",
    "ALLELE_MENTION_MODEL_ID",
    "ALLELE_MENTION_OBJECT_TYPE",
    "ALLELE_MENTION_REFERENCE_VALIDATOR_BINDING_ID",
    "ALLELE_REFERENCE_LINKML_SCHEMA_ID",
    "ALLELE_REFERENCE_MODEL_ID",
    "ALLELE_REFERENCE_OBJECT_TYPE",
    "ALLELE_REFERENCE_SCHEMA_SOURCE_FILE",
]
