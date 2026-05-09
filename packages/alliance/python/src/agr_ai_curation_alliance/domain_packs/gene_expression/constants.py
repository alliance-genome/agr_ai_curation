"""Gene-expression domain-pack constants."""

from pathlib import Path

from ..paths import get_alliance_domain_packs_dir
from ..schema_refs import ALLIANCE_LINKML_COMMIT

GENE_EXPRESSION_DOMAIN_PACK_ID = "agr.alliance.gene_expression"
GENE_EXPRESSION_DOMAIN_PACK_DIR_NAME = "gene_expression"
GENE_EXPRESSION_DOMAIN_PACK_VERSION = "0.1.0"
GENE_EXPRESSION_OBJECT_TYPE = "GeneExpressionAnnotation"
GENE_EXPRESSION_OBJECT_ROLE = "curatable_unit"
GENE_EXPRESSION_MODEL_ID = "GeneExpressionAnnotationPayload"
GENE_EXPRESSION_FIXTURE_PACK_ID = "tmem67_pending"
GENE_EXPRESSION_VALIDATOR_STATES = ("active", "planned", "blocked")
GENE_EXPRESSION_DOMAIN_PACK_CONVERTER_ID = "agr.alliance.gene_expression.converter"
GENE_EXPRESSION_LINKML_SCHEMA_ID = "alliance.linkml.GeneExpressionAnnotation"
GENE_EXPRESSION_LINKML_SCHEMA_NAME = "GeneExpressionAnnotation"
GENE_EXPRESSION_LINKML_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_curation_schema/blob/"
    f"{ALLIANCE_LINKML_COMMIT}/model/schema/expression.yaml"
)
def get_gene_expression_domain_pack_metadata_path() -> Path:
    """Return the bundled gene-expression domain-pack metadata path."""

    return (
        get_alliance_domain_packs_dir()
        / GENE_EXPRESSION_DOMAIN_PACK_DIR_NAME
        / "domain_pack.yaml"
    )


__all__ = [
    "GENE_EXPRESSION_DOMAIN_PACK_CONVERTER_ID",
    "GENE_EXPRESSION_DOMAIN_PACK_DIR_NAME",
    "GENE_EXPRESSION_DOMAIN_PACK_ID",
    "GENE_EXPRESSION_DOMAIN_PACK_VERSION",
    "GENE_EXPRESSION_FIXTURE_PACK_ID",
    "GENE_EXPRESSION_LINKML_SCHEMA_ID",
    "GENE_EXPRESSION_LINKML_SCHEMA_NAME",
    "GENE_EXPRESSION_LINKML_SCHEMA_URI",
    "GENE_EXPRESSION_MODEL_ID",
    "GENE_EXPRESSION_OBJECT_ROLE",
    "GENE_EXPRESSION_OBJECT_TYPE",
    "GENE_EXPRESSION_VALIDATOR_STATES",
    "get_gene_expression_domain_pack_metadata_path",
]
