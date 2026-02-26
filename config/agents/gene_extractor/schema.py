"""Gene extraction agent schema.

This module defines the envelope schema for the gene extraction agent.
The envelope class is discovered at startup and registered in the schema registry.
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

from src.schemas.models.base import StructuredMessageEnvelope


class GeneExtractionResultEnvelope(StructuredMessageEnvelope):
    """Envelope for gene extraction responses."""

    model_config = ConfigDict(extra='forbid')
    __envelope_class__ = True

    actor: str = Field(
        default="gene_extraction_specialist",
        description="The gene extraction agent"
    )
    findings: str = Field(
        description="Summary of gene assertions extracted from the paper"
    )
    gene_terms: List[str] = Field(
        default_factory=list,
        description="Gene terms retained by the extractor"
    )
    species: Optional[List[str]] = Field(
        default=None,
        description="Species/taxa context if explicitly identified"
    )
