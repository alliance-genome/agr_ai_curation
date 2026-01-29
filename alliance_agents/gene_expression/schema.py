"""Gene expression extraction agent schema.

This module defines the envelope schema for the gene expression extraction agent.
The envelope class is discovered at startup and registered in the schema registry.

Naming convention: {AgentFunction}Envelope -> GeneExpressionEnvelope
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

# Import base class from shared schemas location
from backend.src.schemas.models.base import StructuredMessageEnvelope


class ExpressionPattern(ConfigDict):
    """Expression pattern with anatomical and temporal context."""
    gene_symbol: str = Field(description="Gene symbol")
    gene_id: Optional[str] = Field(default=None, description="Gene CURIE if resolved")
    anatomy_term: Optional[str] = Field(default=None, description="Anatomy term")
    anatomy_id: Optional[str] = Field(default=None, description="Anatomy ontology ID")
    life_stage: Optional[str] = Field(default=None, description="Life stage term")
    life_stage_id: Optional[str] = Field(default=None, description="Life stage ontology ID")
    assay_type: Optional[str] = Field(default=None, description="Assay/detection method")
    expression_level: Optional[str] = Field(default=None, description="Expression level if quantified")


class GeneExpressionEnvelope(StructuredMessageEnvelope):
    """Envelope for gene expression extraction agent responses.

    Contains expression pattern data extracted from PDFs.
    """
    model_config = ConfigDict(extra='forbid')

    # Required: Marker for schema discovery
    __envelope_class__ = True

    actor: str = Field(
        default="gene_expression_specialist",
        description="The gene expression extraction agent"
    )
    findings: str = Field(
        description="Summary of expression patterns found"
    )
    organism: Optional[str] = Field(
        default=None,
        description="Primary organism studied"
    )
    expression_patterns: List[dict] = Field(
        default_factory=list,
        description="List of expression pattern records"
    )
    gene_curies: List[str] = Field(
        default_factory=list,
        description="List of gene CURIEs mentioned"
    )
