"""Constants for the Alliance gene validated-reference domain pack."""

from __future__ import annotations

from ..schema_refs import ALLIANCE_LINKML_COMMIT

GENE_DOMAIN_PACK_ID = "gene"
GENE_DOMAIN_PACK_VERSION = "0.1.0"
GENE_MENTION_EVIDENCE_OBJECT_TYPE = "gene_mention_evidence"
GENE_MENTION_EVIDENCE_MODEL_ID = "GeneMentionEvidencePayload"
GENE_REFERENCE_VALIDATOR_BINDING_ID = "alliance_gene_reference_lookup"
GENE_REFERENCE_TOOL_NAME = "agr_curation_query"
GENE_REFERENCE_TOOL_METHOD = "get_gene_by_id"
GENE_DOMAIN_PACK_CONVERTER_ID = "agr_ai_curation_alliance.domain_packs.gene"

GENE_LINKML_SCHEMA_ID = "alliance.linkml.Gene"
GENE_LINKML_SCHEMA_NAME = "Gene"
GENE_LINKML_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{ALLIANCE_LINKML_COMMIT}/model/schema/gene.yaml"
)

GENE_MENTION_EVIDENCE_DEFINITION_NOTES = (
    "Envelope-only validated reference evidence; this object does not create or "
    "mutate Alliance Gene rows.",
    "Paper-gene association write behavior is intentionally out of scope "
    "until a concrete target is verified.",
)

__all__ = [
    "GENE_DOMAIN_PACK_CONVERTER_ID",
    "GENE_DOMAIN_PACK_ID",
    "GENE_DOMAIN_PACK_VERSION",
    "GENE_LINKML_SCHEMA_ID",
    "GENE_LINKML_SCHEMA_NAME",
    "GENE_LINKML_SCHEMA_URI",
    "GENE_MENTION_EVIDENCE_DEFINITION_NOTES",
    "GENE_MENTION_EVIDENCE_MODEL_ID",
    "GENE_MENTION_EVIDENCE_OBJECT_TYPE",
    "GENE_REFERENCE_TOOL_METHOD",
    "GENE_REFERENCE_TOOL_NAME",
    "GENE_REFERENCE_VALIDATOR_BINDING_ID",
]
