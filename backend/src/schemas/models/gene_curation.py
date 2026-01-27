"""Gene curation envelope schema.

Used by gene curation database specialist.
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope


class GeneCurationEnvelope(StructuredMessageEnvelope):
    """Envelope for gene curation database specialist responses"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="gene_curation_specialist", description="The gene curation agent")
    findings: str = Field(description="Gene information from curation database")
    gene_curies: List[str] = Field(
        default_factory=list,
        description="List of gene CURIEs found (e.g., WB:WBGene00006763)"
    )
    species: Optional[List[str]] = Field(
        default=None,
        description="Species/taxa mentioned (e.g., NCBITaxon:6239)"
    )
