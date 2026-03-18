"""Gene validation agent schema.

This module defines the envelope schema for the gene validation agent.
The envelope class is discovered at startup and registered in the schema registry.

Naming convention: {AgentFunction}Envelope -> GeneValidationEnvelope
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

# Import base class from shared schemas location
# TODO: After full migration, this import path will change to a shared base
from src.schemas.models.base import StructuredMessageEnvelope


class GeneValidationEnvelope(StructuredMessageEnvelope):
    """Envelope for gene validation agent responses.

    Contains gene lookup results from the Alliance Curation Database.
    """
    model_config = ConfigDict(extra='forbid')

    # Required: Marker for schema discovery
    __envelope_class__ = True

    actor: str = Field(
        default="gene_validation_specialist",
        description="The gene validation agent"
    )
    findings: str = Field(
        description="Gene information from curation database"
    )
    gene_curies: List[str] = Field(
        default_factory=list,
        description="List of gene CURIEs found (e.g., WB:WBGene00006763)"
    )
    species: Optional[List[str]] = Field(
        default=None,
        description="Species/taxa mentioned (e.g., NCBITaxon:6239)"
    )
