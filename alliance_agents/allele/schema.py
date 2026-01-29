"""Allele validation agent schema.

This module defines the envelope schema for the allele validation agent.
The envelope class is discovered at startup and registered in the schema registry.

Naming convention: {AgentFunction}Envelope -> AlleleValidationEnvelope
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

# Import base class from shared schemas location
from backend.src.schemas.models.base import StructuredMessageEnvelope


class AlleleValidationEnvelope(StructuredMessageEnvelope):
    """Envelope for allele validation agent responses.

    Contains allele lookup results from the Alliance Curation Database.
    """
    model_config = ConfigDict(extra='forbid')

    # Required: Marker for schema discovery
    __envelope_class__ = True

    actor: str = Field(
        default="allele_validation_specialist",
        description="The allele validation agent"
    )
    findings: str = Field(
        description="Allele information from curation database"
    )
    allele_curies: List[str] = Field(
        default_factory=list,
        description="List of allele CURIEs found (e.g., WB:WBVar00000001)"
    )
    species: Optional[List[str]] = Field(
        default=None,
        description="Species/taxa mentioned (e.g., NCBITaxon:6239)"
    )
